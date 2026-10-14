"""Modelos del pronóstico USD/CLP.

Interfaz común (ver `backtest.py`):
  - `fit(X, y, log_s)` con filas cuyo objetivo ya está realizado.
  - `predict(X, log_s)` -> np.ndarray con el retorno log esperado a H días.
  - Opcional `online_predict(X, y, horizon)`: modelos que se actualizan día a día (Kalman)
    entregan todas las predicciones en una sola pasada causal en vez de re-entrenarse.
  - Opcional `explain(X_row)` -> dict con la contribución de cada variable.
"""
from .baselines import ARDaily, RandomWalk, RandomWalkDrift
from .ml import LightGBMModel
from .regression import RidgeECM
from .tvp import TVPKalman

__all__ = ["RandomWalk", "RandomWalkDrift", "ARDaily", "RidgeECM", "LightGBMModel", "TVPKalman"]
