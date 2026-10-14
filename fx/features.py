"""Construcción de variables predictoras X_t y objetivo y_t = ln(S_{t+H} / S_t).

Regla de oro: cada columna de X en la fila t usa solo información del panel hasta la fila t
(ventanas móviles hacia atrás, nunca `shift(-k)`). El único `shift` negativo del módulo es el
del objetivo. `tests/test_fx.py` lo verifica truncando el panel y comparando.

La lista de variables está cerrada a priori a partir de la literatura (ver README, sección
"Modelo USD/CLP"), para no elegir predictores mirando el período de test.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

HORIZON = 21  # días hábiles (~1 mes)

ECT_WINDOW = 1260  # 5 años hábiles para la relación de largo plazo CLP ~ cobre + DXY
ECT_MIN = 504
RER_WINDOW = 1260

MARKETS = ["copper", "dxy", "wti", "brl", "mxn", "cop", "eem"]
MARKET_LAGS = [1, 5, 21, 63]
CLP_LAGS = [5, 21, 63, 126]

# Propuesta 1 (Ridge-ECM): pocas variables, todas con respaldo en la literatura.
REGRESSION_FEATURES = [
    "ect", "rer_gap",
    "copper_r21", "dxy_r21",
    "vix", "vix_chg21",
    "rate_diff", "rate_diff_chg63",
    "clp_r21", "clp_r63", "clp_ma200_gap",
]
# Propuesta 3 (TVP-Kalman): el núcleo más chico, para que los coeficientes variables sean estimables.
TVP_FEATURES = ["ect", "copper_r21", "dxy_r21", "rate_diff", "clp_r21"]


def _rolling_ect(log_s: pd.Series, log_x: pd.DataFrame) -> pd.Series:
    """Residuo de la regresión móvil ln S ~ 1 + ln cobre + ln DXY estimada con los últimos
    ECT_WINDOW días que terminan en t (inclusive). Es causal: el residuo de t solo usa
    coeficientes estimados con datos ≤ t."""
    df = pd.concat([log_s.rename("y"), log_x], axis=1).dropna()
    out = pd.Series(np.nan, index=log_s.index)
    if len(df) < ECT_MIN:
        return out
    y = df["y"].to_numpy()
    X = np.column_stack([np.ones(len(df)), df.drop(columns="y").to_numpy()])
    # Sumas acumuladas para OLS móvil O(n·k²) en vez de re-estimar cada ventana desde cero.
    k = X.shape[1]
    xx = np.einsum("ni,nj->nij", X, X).cumsum(axis=0)
    xy = (X * y[:, None]).cumsum(axis=0)
    res = np.full(len(df), np.nan)
    for i in range(ECT_MIN - 1, len(df)):
        j = i - ECT_WINDOW
        A = xx[i] - (xx[j] if j >= 0 else 0)
        b = xy[i] - (xy[j] if j >= 0 else 0)
        try:
            beta = np.linalg.solve(A + 1e-10 * np.eye(k), b)
        except np.linalg.LinAlgError:
            continue
        res[i] = y[i] - X[i] @ beta
    out.loc[df.index] = res
    return out


def build_features(panel: pd.DataFrame, horizon: int = HORIZON) -> tuple[pd.DataFrame, pd.Series]:
    """Devuelve (X, y). y es NaN en las últimas `horizon` filas (objetivo aún no realizado)."""
    s = panel["dolar_obs"].astype(float)
    log_s = np.log(s)
    X = pd.DataFrame(index=panel.index)

    # Momentum / reversión propia (Menkhoff et al. 2012; BCCh DT 425).
    for k in CLP_LAGS:
        X[f"clp_r{k}"] = log_s - log_s.shift(k)
    X["clp_ma50_gap"] = log_s - np.log(s.rolling(50).mean())
    X["clp_ma200_gap"] = log_s - np.log(s.rolling(200).mean())
    X["clp_vol21"] = log_s.diff().rolling(21).std() * np.sqrt(252)

    # Commodities, dólar global, monedas pares, emergentes.
    for m in MARKETS:
        if m not in panel or panel[m].isna().all():
            continue
        lm = np.log(panel[m].astype(float))
        for k in MARKET_LAGS:
            X[f"{m}_r{k}"] = lm - lm.shift(k)

    # Aversión al riesgo.
    if "vix" in panel:
        X["vix"] = panel["vix"]
        X["vix_chg21"] = np.log(panel["vix"]) - np.log(panel["vix"].shift(21))

    # Diferencial de tasas (carry / regla de Taylor).
    if "tpm" in panel and "fedfunds" in panel:
        X["rate_diff"] = panel["tpm"] - panel["fedfunds"]
        X["rate_diff_chg63"] = X["rate_diff"] - X["rate_diff"].shift(63)
    if "tpm" in panel and "ust2y" in panel:
        X["rate_diff_2y"] = panel["tpm"] - panel["ust2y"]

    # Tipo de cambio real (PPP de largo plazo): desviación de su media móvil de 5 años.
    if "us_cpi" in panel and "ipc_cl_idx" in panel:
        rer = log_s + np.log(panel["us_cpi"]) - np.log(panel["ipc_cl_idx"])
        X["rer_gap"] = rer - rer.rolling(RER_WINDOW, min_periods=504).mean()

    # Corrección de errores respecto de la relación de largo plazo con cobre y DXY.
    if "copper" in panel and "dxy" in panel:
        X["ect"] = _rolling_ect(log_s, np.log(panel[["copper", "dxy"]].astype(float)))

    X = X.replace([np.inf, -np.inf], np.nan)
    y = (log_s.shift(-horizon) - log_s).rename("y")
    return X, y


def available(features: list[str], X: pd.DataFrame) -> list[str]:
    """Subconjunto de `features` que existe y tiene datos (alguna fuente puede faltar)."""
    return [f for f in features if f in X.columns and X[f].notna().any()]
