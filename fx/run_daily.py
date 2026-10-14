"""Corrida diaria del modelo USD/CLP a 21 días hábiles.

Uso:
    python -m fx.run_daily                       # descarga, backtest, pronóstico de hoy, publica
    python -m fx.run_daily --offline             # usa solo la caché data/fx/raw (sin red)
    python -m fx.run_daily --retune              # re-estima hiperparámetros (solo con datos < 2016)

Salidas (en data/fx/ y copiadas a --publish-dir para GitHub Pages):
    dataset.csv               panel diario limpio
    backtest_predictions.csv  pronóstico vs real de cada modelo por fecha de origen (desde 2016)
    metrics.json              pronóstico de hoy, métricas y tests (últimos 5 años y 2016→), drivers
    rolling_theil.csv         RMSE relativo al paseo aleatorio, ventana móvil 12 meses
    tvp_betas.csv             trayectoria de los coeficientes del modelo de Kalman
    forecast_log.csv          registro en vivo de cada pronóstico emitido (append-only)
    hyperparams.json          hiperparámetros fijados con el período de desarrollo
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import data as fxdata
from .backtest import ENSEMBLE_OF, QUANTILES, run_backtest, train_mask
from .evaluate import diebold_mariano, model_metrics, rolling_theil
from .features import HORIZON, build_features
from .models import ARDaily, LightGBMModel, RandomWalk, RandomWalkDrift, RidgeECM, TVPKalman
from .models.regression import hac_inference
from .tune import DEV_END, load_or_tune

log = logging.getLogger("fx")

BACKTEST_START = DEV_END  # todo lo posterior al período de desarrollo es fuera de muestra
TEST_YEARS = 5
MODEL_ORDER = ["ensemble", "ridge", "lgbm", "tvp", "ar", "rw_drift", "rw"]
LABELS = {
    "ensemble": "Ensamble (promedio de las 3 propuestas)",
    "ridge": RidgeECM.label,
    "lgbm": LightGBMModel.label,
    "tvp": TVPKalman.label,
    "ar": ARDaily.label,
    "rw_drift": RandomWalkDrift.label,
    "rw": RandomWalk.label,
}
FEATURE_LABELS = {
    "ect": "Desvío vs. relación de largo plazo cobre/DXY",
    "rer_gap": "Tipo de cambio real vs. promedio 5 años",
    "copper_r21": "Cobre, retorno 1 mes",
    "dxy_r21": "DXY, retorno 1 mes",
    "vix": "VIX (nivel)",
    "vix_chg21": "VIX, cambio 1 mes",
    "rate_diff": "TPM − Fed Funds",
    "rate_diff_chg63": "TPM − Fed Funds, cambio 3 meses",
    "clp_r21": "Dólar, retorno 1 mes (momentum)",
    "clp_r63": "Dólar, retorno 3 meses",
    "clp_ma200_gap": "Dólar vs. media móvil 200 días",
}


def build_models(hp: dict) -> dict:
    return {
        "rw": RandomWalk(),
        "rw_drift": RandomWalkDrift(),
        "ar": ARDaily(horizon=HORIZON),
        "ridge": RidgeECM(alpha=hp["ridge"]["alpha"]),
        "lgbm": LightGBMModel(hp["lgbm"]["params"]),
        "tvp": TVPKalman.from_params(hp["tvp"], horizon=HORIZON),
    }


def forecast_today(X, y, log_s, models: dict, bt: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Re-entrena los modelos batch con TODO lo realizado y pronostica el último origen.
    Reemplaza esa fila del backtest (que venía de un re-entrenamiento de hasta 20 días atrás)."""
    last = len(X) - 1
    m = train_mask(X.index, y, last + HORIZON)  # todo objetivo ya realizado
    x_last = X.iloc[[last]]
    rets, drivers = {}, {}
    for name, model in models.items():
        if hasattr(model, "online_predict"):
            rets[name] = float(model.online_predict(X, y, HORIZON)[last])
        else:
            model.fit(X[m], y[m], log_s)
            rets[name] = float(model.predict(x_last, log_s)[0])
        if hasattr(model, "explain"):
            drivers[name] = model.explain(x_last)
    rets["ensemble"] = float(np.mean([rets[k] for k in ENSEMBLE_OF]))

    i = bt.index[-1]
    spot = bt.at[i, "spot"]
    for name, r in rets.items():
        old_ret = bt.at[i, f"ret_{name}"]
        bt.at[i, f"ret_{name}"] = r
        bt.at[i, f"pred_{name}"] = spot * np.exp(r)
        for k in QUANTILES:  # mantiene el ancho del intervalo, re-centrado en el nuevo pronóstico
            col = f"{k}_{name}"
            if pd.notna(bt.at[i, col]):
                bt.at[i, col] = bt.at[i, col] * np.exp(r - old_ret)
    return bt, drivers


def _round(obj, nd=4):
    if isinstance(obj, float):
        return None if not np.isfinite(obj) else round(obj, nd)
    if isinstance(obj, dict):
        return {k: _round(v, nd) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round(v, nd) for v in obj]
    return obj


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="No descargar; usar la caché de data/fx/raw")
    ap.add_argument("--retune", action="store_true", help="Re-estimar hiperparámetros (período de desarrollo)")
    ap.add_argument("--raw-dir", default="data/fx/raw")
    ap.add_argument("--out-dir", default="data/fx")
    ap.add_argument("--publish-dir", default="docs/data/fx")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    raw_dir, out = Path(args.raw_dir), Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    raw = fxdata.load_all_cached(raw_dir) if args.offline else fxdata.download_all(raw_dir)
    panel = fxdata.build_panel(raw)
    panel.to_csv(out / "dataset.csv", float_format="%.6g")
    log.info("Panel: %d días (%s → %s)", len(panel), panel.index[0].date(), panel.index[-1].date())

    X, y = build_features(panel)
    log_s = np.log(panel["dolar_obs"])

    hp = load_or_tune(out / "hyperparams.json", X, y, log_s, force=args.retune)
    models = build_models(hp)

    bt = run_backtest(X, y, log_s, models, start=BACKTEST_START)
    bt, drivers = forecast_today(X, y, log_s, models, bt)

    # ---------------- Métricas ----------------
    last_real = bt.loc[bt["y"].notna(), "fecha_objetivo"].max()
    test_start = last_real - pd.DateOffset(years=TEST_YEARS)
    windows = {
        "test_5y": bt[bt["fecha_objetivo"] > test_start],
        "oos_desde_2016": bt,
    }
    metrics = {}
    for wname, w in windows.items():
        metrics[wname] = {
            "desde": str(w.loc[w["y"].notna(), "fecha_objetivo"].min().date()),
            "hasta": str(w.loc[w["y"].notna(), "fecha_objetivo"].max().date()),
            "modelos": {m: model_metrics(w, m) for m in MODEL_ORDER},
        }
        d = w.dropna(subset=["y"])
        metrics[wname]["diebold_mariano_vs_ensemble"] = {
            m: dict(zip(["stat", "p"], diebold_mariano(d["y"] - d[f"ret_{m}"], d["y"] - d["ret_ensemble"])))
            for m in ["ridge", "lgbm", "tvp", "ar", "rw"]
        }

    rt = rolling_theil(bt, MODEL_ORDER)
    rt.to_csv(out / "rolling_theil.csv", float_format="%.4f", index_label="fecha")

    tvp = models["tvp"]
    tvp.betas_path(X, y).loc[BACKTEST_START:].to_csv(out / "tvp_betas.csv", float_format="%.5f")

    # ---------------- Pronóstico de hoy ----------------
    row = bt.iloc[-1]
    today = {
        "fecha_origen": str(row["fecha_origen"].date()),
        "fecha_objetivo_estimada": str(row["fecha_objetivo"].date()),
        "dolar_observado_hoy": round(float(row["spot"]), 2),
        "modelos": {
            m: {
                "pronostico": round(float(row[f"pred_{m}"]), 2),
                "cambio_pct": round(float(np.expm1(row[f"ret_{m}"]) * 100), 2),
                **{k: (round(float(row[f"{k}_{m}"]), 2) if pd.notna(row[f"{k}_{m}"]) else None) for k in QUANTILES},
            }
            for m in MODEL_ORDER
        },
    }

    full_mask = y.notna()
    meta = {
        "actualizado_en": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "horizonte_dias_habiles": HORIZON,
        "backtest_desde": BACKTEST_START,
        "periodo_desarrollo_hasta": DEV_END,
        "ultimo_dato": str(panel.index[-1].date()),
        "variables_disponibles": [c for c in X.columns if X[c].notna().any()],
        "calidad_datos": fxdata.spot_vs_observado_check(panel),
        "etiquetas_modelos": LABELS,
        "etiquetas_variables": FEATURE_LABELS,
    }
    result = {
        "meta": meta,
        "hoy": today,
        "metricas": metrics,
        "drivers_hoy": {k: dict(sorted(v.items(), key=lambda kv: -abs(kv[1]))[:10]) for k, v in drivers.items()},
        "regresion_hac": hac_inference(X[full_mask], y[full_mask]),
        "hiperparametros": hp,
    }
    (out / "metrics.json").write_text(json.dumps(_round(result, 5), indent=2, ensure_ascii=False))

    cols = ["fecha_origen", "fecha_objetivo", "spot", "real"] + [
        f"{p}_{m}" for m in MODEL_ORDER for p in ["pred", "lo80", "hi80", "lo95", "hi95"]
    ]
    bt[cols].to_csv(out / "backtest_predictions.csv", index=False, float_format="%.2f", date_format="%Y-%m-%d")

    # ---------------- Registro en vivo (append-only) ----------------
    log_path = out / "forecast_log.csv"
    entry = pd.DataFrame([{
        "fecha_origen": today["fecha_origen"],
        "fecha_objetivo_estimada": today["fecha_objetivo_estimada"],
        "spot": today["dolar_observado_hoy"],
        **{f"pred_{m}": today["modelos"][m]["pronostico"] for m in MODEL_ORDER},
        "emitido_en": meta["actualizado_en"],
    }])
    if log_path.exists():
        prev = pd.read_csv(log_path, dtype={"fecha_origen": str})
        # Un pronóstico emitido no se reescribe: si ya existe la fecha, se conserva el primero.
        if today["fecha_origen"] not in set(prev["fecha_origen"]):
            entry = pd.concat([prev, entry], ignore_index=True)
        else:
            entry = prev
    entry.to_csv(log_path, index=False)

    if args.publish_dir:
        pub = Path(args.publish_dir)
        pub.mkdir(parents=True, exist_ok=True)
        for f in ["backtest_predictions.csv", "metrics.json", "rolling_theil.csv", "tvp_betas.csv", "forecast_log.csv"]:
            shutil.copy2(out / f, pub / f)

    t5 = metrics["test_5y"]["modelos"]
    log.info("Hoy %s: dólar %.2f → ensamble a 21 días %.2f (%+.2f%%)", today["fecha_origen"],
             today["dolar_observado_hoy"], today["modelos"]["ensemble"]["pronostico"],
             today["modelos"]["ensemble"]["cambio_pct"])
    for m in MODEL_ORDER:
        log.info("  5 años %-9s Theil-U=%.3f  RMSE=%.1f CLP  dir=%s", m, t5[m]["theil_u"], t5[m]["rmse_clp"],
                 t5[m].get("acierto_direccional_pct"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
