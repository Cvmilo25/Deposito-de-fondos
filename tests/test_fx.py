"""Tests del modelo USD/CLP con datos sintéticos (no requieren red).

Cubren lo que puede invalidar un backtest sin que se note: fuga de información futura en las
variables o en el entrenamiento, y tests estadísticos mal implementados.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fx import data as fxdata  # noqa: E402
from fx.backtest import run_backtest, train_mask  # noqa: E402
from fx.evaluate import clark_west, diebold_mariano, model_metrics, pesaran_timmermann  # noqa: E402
from fx.features import HORIZON, build_features  # noqa: E402
from fx.models import RandomWalk, RidgeECM, TVPKalman  # noqa: E402


def synthetic_raw(start="2007-01-01", end="2017-06-30", seed=0) -> dict:
    """Series con la estructura de las fuentes reales. El dólar responde con rezago al cobre
    (predictibilidad verdadera, para comprobar que los modelos la detectan)."""
    rng = np.random.default_rng(seed)
    d = pd.bdate_range(start, end)
    n = len(d)
    cu = np.cumsum(rng.normal(0, 0.015, n))
    dx = np.cumsum(rng.normal(0, 0.005, n))
    lagged = pd.Series(cu).diff(21).shift(1).fillna(0).to_numpy()
    clp = np.log(500) + np.cumsum(rng.normal(0, 0.006, n) - 0.6 * lagged / 21) + 0.3 * dx
    raw = {
        "dolar_obs": pd.Series(np.exp(clp), d),
        "copper": pd.Series(3 * np.exp(cu), d),
        "dxy": pd.Series(95 * np.exp(dx), d),
        "tpm": pd.Series(np.clip(3 + np.cumsum(rng.normal(0, 0.01, n)), 0.5, 12), d),
        "fedfunds": pd.Series(np.clip(1 + np.cumsum(rng.normal(0, 0.01, n)), 0, 6), d),
        "ust2y": pd.Series(np.clip(1.5 + np.cumsum(rng.normal(0, 0.01, n)), 0, 6), d),
        "vix": pd.Series(np.clip(18 + np.cumsum(rng.normal(0, 0.5, n)), 9, 80), d),
    }
    for k in ["wti", "brl", "mxn", "cop", "eem", "spx", "clp_spot"]:
        raw[k] = pd.Series(50 * np.exp(np.cumsum(rng.normal(0, 0.01, n))), d)
    months = pd.date_range(start, end, freq="MS")
    raw["ipc_cl"] = pd.Series(rng.normal(0.3, 0.2, len(months)), months)
    raw["us_cpi"] = pd.Series(200 * np.exp(np.cumsum(rng.normal(0.002, 0.001, len(months)))), months)
    return raw


@pytest.fixture(scope="module")
def panel():
    return fxdata.build_panel(synthetic_raw())


def test_features_no_lookahead(panel):
    """Las variables en t no cambian si se borra todo lo posterior a t."""
    X_full, _ = build_features(panel)
    cut = panel.index[len(panel) - 300]
    X_cut, y_cut = build_features(panel.loc[:cut])
    a, b = X_full.loc[:cut], X_cut
    pd.testing.assert_frame_equal(a[b.columns], b, check_exact=False, rtol=1e-8)
    assert y_cut.iloc[-HORIZON:].isna().all()


def test_market_series_lagged_one_day(panel):
    """Series de mercado: valor conocido el día hábil anterior al dólar observado."""
    raw = synthetic_raw()
    t = panel.index[100]
    prev = raw["copper"].loc[: t - pd.Timedelta(days=1)].iloc[-1]
    assert panel.at[t, "copper"] == pytest.approx(prev)


def test_monthly_publication_lag():
    s = pd.Series([1.0], index=[pd.Timestamp("2020-03-01")])
    out = fxdata.monthly_to_available(s, 10)
    assert out.index[0] == pd.Timestamp("2020-04-10")


def test_train_mask_purges_overlap(panel):
    X, y = build_features(panel)
    p = 2000
    m = train_mask(X.index, y, p)
    assert np.flatnonzero(m).max() + HORIZON <= p


def test_backtest_and_ridge_beat_rw_on_predictable_data(panel):
    X, y = build_features(panel)
    log_s = np.log(panel["dolar_obs"])
    models = {"rw": RandomWalk(), "ridge": RidgeECM(alpha=10, features=["copper_r21", "clp_r21"])}
    bt = run_backtest(X, y, log_s, models, start="2014-01-01")
    assert bt["ret_rw"].eq(0).all()
    assert bt["y"].iloc[-HORIZON:].isna().all() and bt["y"].iloc[:-HORIZON].notna().all()
    m_rw = model_metrics(bt, "rw")
    m_ridge = model_metrics(bt, "ridge")
    assert m_rw["theil_u"] == pytest.approx(1.0)
    assert m_ridge["theil_u"] < 1.0
    assert m_ridge["clark_west_p"] < 0.05
    assert 70 <= m_ridge["cobertura80_pct"] <= 90


def test_tvp_online_is_causal(panel):
    X, y = build_features(panel)
    tvp = TVPKalman(q=1e-7, r=1e-3).calibrate(X, y)
    p_full = tvp.online_predict(X, y)
    y_cut = y.copy()
    y_cut.iloc[1500:] = np.nan  # ocultar el futuro no debe cambiar predicciones hasta 1500 + H
    p_cut = tvp.online_predict(X, y_cut)
    np.testing.assert_allclose(p_full[: 1500 + HORIZON], p_cut[: 1500 + HORIZON])


def test_statistical_tests_null_and_alternative():
    rng = np.random.default_rng(1)
    n = 3000
    x = rng.normal(size=n)
    y = 0.5 * x + rng.normal(size=n)
    # Modelo con información real: Clark-West rechaza, DM favorece al modelo.
    assert clark_west(y, 0.5 * x)[1] < 0.01
    assert diebold_mariano(y, y - 0.5 * x)[0] > 3
    assert pesaran_timmermann(y, 0.5 * x)[1] < 0.01
    # Modelo sin información: no se rechaza.
    noise = rng.normal(size=n) * 0.01
    assert clark_west(y, noise)[1] > 0.05


def test_run_daily_offline_end_to_end(tmp_path):
    raw_dir = tmp_path / "raw"
    for k, s in synthetic_raw(end="2018-12-31").items():
        fxdata._save(k, s, raw_dir)
    from fx.run_daily import main

    out, pub = tmp_path / "out", tmp_path / "pub"
    assert main(["--offline", "--raw-dir", str(raw_dir), "--out-dir", str(out), "--publish-dir", str(pub)]) == 0
    m = json.loads((pub / "metrics.json").read_text())
    assert set(m["hoy"]["modelos"]) >= {"ensemble", "ridge", "lgbm", "tvp", "rw"}
    assert m["metricas"]["test_5y"]["modelos"]["rw"]["theil_u"] == pytest.approx(1.0)
    bt = pd.read_csv(pub / "backtest_predictions.csv")
    assert bt["real"].isna().sum() == HORIZON
    # Segunda corrida el mismo día: el registro en vivo no duplica ni reescribe.
    main(["--offline", "--raw-dir", str(raw_dir), "--out-dir", str(out), "--publish-dir", str(pub)])
    assert len(pd.read_csv(pub / "forecast_log.csv")) == 1
