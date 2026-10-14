"""Ajuste de hiperparámetros SOLO con el período de desarrollo (orígenes < DEV_END).

Todo lo posterior a DEV_END es fuera de muestra para efectos de evaluación: nada del
backtest de 5 años que muestra el dashboard se usa para elegir parámetros.

Validación cruzada temporal: para cada año de validación V (2012-2015) se entrena con los
orígenes cuyo objetivo se realizó antes del inicio de V (purga de H días) y se mide el MSE en V.
"""
from __future__ import annotations

import itertools
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import train_mask
from .features import HORIZON
from .models import LightGBMModel, RidgeECM, TVPKalman

log = logging.getLogger(__name__)

DEV_END = "2016-01-01"
CV_YEARS = [2012, 2013, 2014, 2015]

RIDGE_ALPHAS = [1, 10, 100, 1_000, 10_000]
LGBM_GRID = {
    "num_leaves": [4, 7],
    "n_estimators": [150, 400],
    "min_child_samples": [100, 300],
}


def _cv_mse(make_model, X: pd.DataFrame, y: pd.Series, log_s: pd.Series) -> float:
    errs = []
    for yr in CV_YEARS:
        val = (X.index.year == yr) & y.notna().to_numpy()
        if not val.any():
            continue
        p0 = int(np.flatnonzero(val)[0])
        m = train_mask(X.index, y, p0, HORIZON)
        if m.sum() < 250:
            continue
        model = make_model().fit(X[m], y[m], log_s)
        pred = model.predict(X[val], log_s)
        errs.append(np.asarray(y[val]) - pred)
    if not errs:
        return float("nan")
    e = np.concatenate(errs)
    return float(np.mean(e ** 2))


def tune(X: pd.DataFrame, y: pd.Series, log_s: pd.Series) -> dict:
    dev = X.index < pd.Timestamp(DEV_END)
    Xd, yd = X[dev], y[dev]
    # El objetivo de los últimos H orígenes del desarrollo se realiza después de DEV_END: se excluye.
    yd = yd.copy()
    yd.iloc[-HORIZON:] = np.nan

    rw_mse = _cv_mse(lambda: _Zero(), Xd, yd, log_s)
    ridge_scores = {a: _cv_mse(lambda a=a: RidgeECM(alpha=a), Xd, yd, log_s) for a in RIDGE_ALPHAS}
    best_alpha = min(ridge_scores, key=lambda a: ridge_scores[a])
    log.info("Ridge CV MSE/RW: %s", {a: round(v / rw_mse, 4) for a, v in ridge_scores.items()})

    lgbm_scores = {}
    for combo in itertools.product(*LGBM_GRID.values()):
        params = dict(zip(LGBM_GRID, combo))
        lgbm_scores[json.dumps(params)] = _cv_mse(lambda p=params: LightGBMModel(p), Xd, yd, log_s)
    best_lgbm = json.loads(min(lgbm_scores, key=lambda k: lgbm_scores[k]))
    log.info("LGBM mejor: %s (MSE/RW %.4f)", best_lgbm, lgbm_scores[json.dumps(best_lgbm)] / rw_mse)

    tvp = TVPKalman().calibrate(Xd, yd)

    return {
        "dev_end": DEV_END,
        "cv_years": CV_YEARS,
        "ridge": {"alpha": best_alpha, "cv_mse_rel_rw": {str(a): v / rw_mse for a, v in ridge_scores.items()}},
        "lgbm": {"params": best_lgbm, "cv_mse_rel_rw": lgbm_scores[json.dumps(best_lgbm)] / rw_mse},
        "tvp": {"q": tvp.q, "r": tvp.r, "p0": tvp.p0_, "features": tvp.cols_,
                "mean": tvp.mean_.to_dict(), "std": tvp.std_.to_dict()},
    }


class _Zero:
    def fit(self, X, y, log_s=None):
        return self

    def predict(self, X, log_s=None):
        return np.zeros(len(X))


def load_or_tune(path: Path, X, y, log_s, force: bool = False) -> dict:
    if path.exists() and not force:
        return json.loads(path.read_text())
    hp = tune(X, y, log_s)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(hp, indent=2, ensure_ascii=False))
    return hp
