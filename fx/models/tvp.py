"""Propuesta 3 — Modelo de estado: regresión con coeficientes variables en el tiempo.

    y_t = β_t'·x_t + ε_t,        ε_t ~ N(0, r)
    β_t = β_{t-1} + η_t,          η_t ~ N(0, q·I)

Estimado con filtro de Kalman. Captura que la sensibilidad del peso al cobre, al DXY o a las
tasas cambia con el tiempo (Bacchetta & van Wincoop, "scapegoat"), que es la razón principal
de que los modelos de coeficientes fijos fallen fuera de muestra.

Detalle causal: el objetivo de la fila s (retorno s → s+H) recién se conoce en s+H. Por eso,
al pronosticar en t, el filtro solo ha incorporado las observaciones s ≤ t-H. q y r se
estiman por máxima verosimilitud solo con el período de desarrollo y luego quedan fijos.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from ..features import TVP_FEATURES, available


def _design(X: pd.DataFrame, cols, mean, std) -> np.ndarray:
    Z = ((X[cols] - mean) / std).fillna(0.0).to_numpy()
    return np.column_stack([np.ones(len(Z)), Z])


def _filter(Z: np.ndarray, y: np.ndarray, q: float, r: float, horizon: int, p0: float = 1.0):
    """Recorre las filas en orden. En la fila i: (1) pronostica con el estado actual,
    (2) incorpora la observación de la fila i-H, cuyo objetivo se realizó justo en i.
    Devuelve predicciones y log-verosimilitud de las innovaciones."""
    n, k = Z.shape
    beta = np.zeros(k)
    P = np.eye(k) * p0
    Q = np.eye(k) * q
    preds = np.empty(n)
    loglik = 0.0
    for i in range(n):
        preds[i] = Z[i] @ beta
        j = i - horizon
        if j >= 0 and np.isfinite(y[j]):
            P = P + Q
            z = Z[j]
            f = z @ P @ z + r
            e = y[j] - z @ beta
            K = P @ z / f
            beta = beta + K * e
            P = P - np.outer(K, z @ P)
            loglik += -0.5 * (np.log(2 * np.pi * f) + e * e / f)
    return preds, loglik, beta


class TVPKalman:
    name = "tvp"
    label = "Coeficientes variables (Kalman)"

    def __init__(self, q: float | None = None, r: float | None = None, features: list[str] | None = None,
                 horizon: int = 21):
        self.q, self.r = q, r
        self.features = features or TVP_FEATURES
        self.horizon = horizon

    @classmethod
    def from_params(cls, hp: dict, horizon: int = 21) -> "TVPKalman":
        """Reconstruye el modelo calibrado guardado por `fx/tune.py`."""
        m = cls(q=hp["q"], r=hp["r"], features=hp["features"], horizon=horizon)
        m.cols_ = list(hp["features"])
        m.mean_, m.std_ = pd.Series(hp["mean"]), pd.Series(hp["std"])
        m.p0_ = float(hp["p0"])
        return m

    def calibrate(self, X: pd.DataFrame, y: pd.Series):
        """Fija estandarización y (si no vienen dadas) estima q, r por MV con los datos entregados
        (en el pipeline: solo el período de desarrollo)."""
        self.cols_ = available(self.features, X)
        D = X[self.cols_].join(y).dropna()
        self.mean_, self.std_ = D[self.cols_].mean(), D[self.cols_].std().replace(0, 1)
        # Varianza a priori de β_0: fija desde la calibración (usar la de toda la muestra filtraría futuro).
        self.p0_ = v0 = float(np.var(D["y"]))
        if self.q is None or self.r is None:
            Z = _design(D, self.cols_, self.mean_, self.std_)
            yy = D["y"].to_numpy()

            def nll(theta):
                return -_filter(Z, yy, np.exp(theta[0]), np.exp(theta[1]), self.horizon, p0=v0)[1]

            best = minimize(nll, x0=[np.log(v0 * 1e-4), np.log(v0)], method="Nelder-Mead",
                            options={"maxiter": 300, "xatol": 1e-3, "fatol": 1e-3})
            self.q, self.r = float(np.exp(best.x[0])), float(np.exp(best.x[1]))
        return self

    def online_predict(self, X: pd.DataFrame, y: pd.Series, horizon: int | None = None) -> np.ndarray:
        """Predicciones causales para todas las filas de X en una sola pasada.
        `y` puede traer valores futuros: el filtro solo usa y[i-H] al predecir la fila i."""
        h = horizon or self.horizon
        Z = _design(X, self.cols_, self.mean_, self.std_)
        preds, _, beta = _filter(Z, y.to_numpy(), self.q, self.r, h, p0=self.p0_)
        self.beta_last_ = beta
        return preds

    def betas_path(self, X: pd.DataFrame, y: pd.Series, every: int = 5) -> pd.DataFrame:
        """Trayectoria de β_t (cada `every` días) para graficar cómo cambian las sensibilidades."""
        Z = _design(X, self.cols_, self.mean_, self.std_)
        yy = y.to_numpy()
        n, k = Z.shape
        beta, P, Q = np.zeros(k), np.eye(k) * self.p0_, np.eye(k) * self.q
        rows = []
        for i in range(n):
            j = i - self.horizon
            if j >= 0 and np.isfinite(yy[j]):
                P = P + Q
                z = Z[j]
                f = z @ P @ z + self.r
                K = P @ z / f
                beta = beta + K * (yy[j] - z @ beta)
                P = P - np.outer(K, z @ P)
            if i % every == 0 or i == n - 1:
                rows.append([X.index[i], *beta[1:]])
        return pd.DataFrame(rows, columns=["fecha", *self.cols_]).set_index("fecha")

    def explain(self, x_row: pd.DataFrame) -> dict:
        z = _design(x_row, self.cols_, self.mean_, self.std_)[0]
        return {c: float(b * v) for c, b, v in zip(self.cols_, self.beta_last_[1:], z[1:])}
