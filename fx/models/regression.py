"""Propuesta 1 — Econometría: regresión lineal con término de corrección de errores (ECM)
estimada con Ridge.

    y_t = α + γ·ECT_t + β'·X_t + ε_t

La penalización Ridge encoge los coeficientes hacia 0, es decir, hacia el paseo aleatorio:
si las variables no aportan, el modelo converge solo al benchmark. Es la familia que Rossi
(2013) encuentra más robusta (lineal, pocos parámetros).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ..features import REGRESSION_FEATURES, available


class RidgeECM:
    name = "ridge"
    label = "Regresión Ridge-ECM"

    def __init__(self, alpha: float = 100.0, features: list[str] | None = None):
        self.alpha = alpha
        self.features = features or REGRESSION_FEATURES

    def fit(self, X: pd.DataFrame, y: pd.Series, log_s=None):
        self.cols_ = available(self.features, X)
        D = X[self.cols_].join(y).dropna()
        self.medians_ = D[self.cols_].median()
        self.pipe_ = make_pipeline(StandardScaler(), Ridge(alpha=self.alpha))
        self.pipe_.fit(D[self.cols_], D["y"])
        return self

    def predict(self, X: pd.DataFrame, log_s=None) -> np.ndarray:
        return self.pipe_.predict(X[self.cols_].fillna(self.medians_))

    def explain(self, x_row: pd.DataFrame) -> dict:
        """Contribución de cada variable (coef estandarizado × valor estandarizado)."""
        scaler, ridge = self.pipe_[0], self.pipe_[1]
        z = scaler.transform(x_row[self.cols_].fillna(self.medians_))[0]
        return {c: float(b * v) for c, b, v in zip(self.cols_, ridge.coef_, z)}


def hac_inference(X: pd.DataFrame, y: pd.Series, features: list[str] | None = None, lags: int = 20) -> list[dict]:
    """OLS con errores Newey-West (los objetivos a 21 días se solapan → autocorrelación MA(20)).
    Solo para reportar qué variables son significativas; el pronóstico usa Ridge."""
    import statsmodels.api as sm

    cols = available(features or REGRESSION_FEATURES, X)
    D = X[cols].join(y).dropna()
    Z = (D[cols] - D[cols].mean()) / D[cols].std()
    res = sm.OLS(D["y"], sm.add_constant(Z)).fit(cov_type="HAC", cov_kwds={"maxlags": lags})
    return [
        {"variable": c, "coef_std": float(res.params[c]), "t_hac": float(res.tvalues[c]), "p_value": float(res.pvalues[c])}
        for c in cols
    ]
