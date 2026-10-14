"""Backtest walk-forward sin fuga de información.

Para cada fecha de origen t:
  - Se entrena solo con filas s cuyo objetivo ya se realizó: pos(s) + H ≤ pos(t) ("purga"
    del solapamiento; sin esto el entrenamiento vería parte del futuro que se pronostica).
  - Los modelos batch se re-entrenan cada `refit_every` días hábiles (ventana expansiva).
  - El modelo de Kalman se actualiza día a día en una sola pasada causal.
  - Los intervalos de predicción son cuantiles de los errores fuera de muestra del propio
    modelo ya realizados al momento t (últimos 2 años): un intervalo conformal empírico,
    que no asume normalidad y se ajusta solo cuando cambia la volatilidad.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .features import HORIZON

log = logging.getLogger(__name__)

ENSEMBLE_OF = ["ridge", "lgbm", "tvp"]
INTERVAL_WINDOW = 504
INTERVAL_MIN = 120
QUANTILES = {"lo95": 0.025, "lo80": 0.10, "hi80": 0.90, "hi95": 0.975}


def train_mask(index: pd.Index, y: pd.Series, origin_pos: int, horizon: int = HORIZON) -> np.ndarray:
    pos = np.arange(len(index))
    return (pos + horizon <= origin_pos) & y.notna().to_numpy()


def run_backtest(X: pd.DataFrame, y: pd.Series, log_s: pd.Series, models: dict, start: str,
                 refit_every: int = 21, horizon: int = HORIZON) -> pd.DataFrame:
    """Devuelve un DataFrame con una fila por fecha de origen ≥ start (incluye las últimas H
    fechas, cuyo objetivo aún no se realiza: son los pronósticos "vivos")."""
    dates = X.index
    origin_pos = np.flatnonzero(dates >= pd.Timestamp(start))
    origins = dates[origin_pos]
    rets: dict[str, np.ndarray] = {}

    for name, model in models.items():
        out = np.full(len(origins), np.nan)
        if hasattr(model, "online_predict"):
            full = model.online_predict(X, y, horizon)
            out[:] = full[origin_pos]
        else:
            for c0 in range(0, len(origins), refit_every):
                p0 = origin_pos[c0]
                m = train_mask(dates, y, p0, horizon)
                model.fit(X[m], y[m], log_s)
                chunk = slice(c0, min(c0 + refit_every, len(origins)))
                out[chunk] = model.predict(X.iloc[origin_pos[chunk]], log_s)
        rets[name] = out
        log.info("backtest: %s listo", name)

    ens = [m for m in ENSEMBLE_OF if m in rets]
    if ens:
        rets["ensemble"] = np.mean([rets[m] for m in ens], axis=0)

    spot = np.exp(log_s.reindex(origins).to_numpy())
    target_pos = origin_pos + horizon
    has_target = target_pos < len(dates)
    fecha_obj = np.where(
        has_target,
        dates[np.minimum(target_pos, len(dates) - 1)],
        # Objetivo futuro: fecha estimada en días hábiles (no considera feriados chilenos).
        origins + pd.offsets.BDay(horizon),
    )
    bt = pd.DataFrame({
        "fecha_origen": origins,
        "fecha_objetivo": pd.to_datetime(fecha_obj),
        "spot": spot,
        "y": y.reindex(origins).to_numpy(),
    })
    bt["real"] = np.where(bt["y"].notna(), bt["spot"] * np.exp(bt["y"]), np.nan)

    for name, r in rets.items():
        bt[f"ret_{name}"] = r
        bt[f"pred_{name}"] = bt["spot"] * np.exp(r)
        add_intervals(bt, name, horizon)
    return bt


def add_intervals(bt: pd.DataFrame, name: str, horizon: int = HORIZON) -> None:
    """Intervalos conformales empíricos: en la fila i se usan los errores de las filas
    j ≤ i - H (ya realizados), los últimos INTERVAL_WINDOW."""
    err = (bt["y"] - bt[f"ret_{name}"]).to_numpy()
    n = len(bt)
    qs = {k: np.full(n, np.nan) for k in QUANTILES}
    for i in range(n):
        hi = i - horizon + 1
        if hi <= 0:
            continue
        e = err[max(0, hi - INTERVAL_WINDOW):hi]
        e = e[np.isfinite(e)]
        if len(e) < INTERVAL_MIN:
            continue
        vals = np.quantile(e, list(QUANTILES.values()))
        for k, v in zip(QUANTILES, vals):
            qs[k][i] = v
    ret = bt[f"ret_{name}"].to_numpy()
    for k in QUANTILES:
        bt[f"{k}_{name}"] = bt["spot"].to_numpy() * np.exp(ret + qs[k])
