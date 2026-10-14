"""Benchmarks. El paseo aleatorio sin drift es EL benchmark de la literatura (Meese & Rogoff
1983; Rossi 2013): pronostica que el dólar en 21 días será igual al de hoy."""
from __future__ import annotations

import numpy as np
import pandas as pd


class RandomWalk:
    name = "rw"
    label = "Paseo aleatorio"

    def fit(self, X, y, log_s=None):
        return self

    def predict(self, X, log_s=None):
        return np.zeros(len(X))


class RandomWalkDrift:
    name = "rw_drift"
    label = "Paseo aleatorio con drift"

    def fit(self, X, y, log_s=None):
        self.mu_ = float(np.nanmean(y))
        return self

    def predict(self, X, log_s=None):
        return np.full(len(X), self.mu_)


class ARDaily:
    """AR(p) sobre retornos diarios del dólar (OLS), iterado H pasos y acumulado.
    Benchmark estadístico puro (sin variables externas), en lugar de un ARIMA completo:
    misma idea, pero condicionar en los últimos p retornos de cada día es inmediato."""

    name = "ar"
    label = "AR(5) diario"

    def __init__(self, p: int = 5, horizon: int = 21, window: int = 1260):
        self.p, self.horizon, self.window = p, horizon, window

    def fit(self, X, y, log_s: pd.Series):
        # Solo retornos conocidos al momento del último origen de entrenamiento + H.
        end = X.index[-1]
        pos = log_s.index.get_loc(end) + self.horizon
        r = log_s.diff().iloc[max(1, pos - self.window): pos + 1].dropna().to_numpy()
        Z = np.column_stack([np.ones(len(r) - self.p)] + [r[self.p - k - 1: len(r) - k - 1] for k in range(self.p)])
        self.coef_, *_ = np.linalg.lstsq(Z, r[self.p:], rcond=None)
        return self

    def predict(self, X, log_s: pd.Series):
        r = log_s.diff()
        out = np.empty(len(X))
        for i, t in enumerate(X.index):
            pos = log_s.index.get_loc(t)
            hist = list(r.iloc[pos - self.p + 1: pos + 1].to_numpy()[::-1])  # más reciente primero
            total = 0.0
            for _ in range(self.horizon):
                nxt = self.coef_[0] + float(np.dot(self.coef_[1:], hist[: self.p]))
                total += nxt
                hist.insert(0, nxt)
            out[i] = total
        return out
