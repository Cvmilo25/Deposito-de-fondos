"""Propuesta 2 — Machine Learning: Gradient Boosting (LightGBM).

Usa el set completo de variables y puede capturar no linealidades e interacciones (p. ej. que
el efecto del cobre cambie cuando el VIX está alto). Con ~220 observaciones independientes a
21 días el riesgo es el sobreajuste, por eso la regularización es fuerte (árboles chicos,
muchas observaciones por hoja, submuestreo) y los hiperparámetros se fijan solo con datos
de 2008-2015 (`fx/tune.py`).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_PARAMS = {
    "n_estimators": 300,
    "learning_rate": 0.02,
    "num_leaves": 7,
    "max_depth": 3,
    "min_child_samples": 200,
    "subsample": 0.7,
    "subsample_freq": 1,
    "colsample_bytree": 0.7,
    "reg_lambda": 5.0,
}


class LightGBMModel:
    name = "lgbm"
    label = "Gradient Boosting (LightGBM)"

    def __init__(self, params: dict | None = None, seed: int = 7):
        self.params = {**DEFAULT_PARAMS, **(params or {})}
        self.seed = seed

    def fit(self, X: pd.DataFrame, y: pd.Series, log_s=None):
        import lightgbm as lgb

        self.cols_ = [c for c in X.columns if X[c].notna().any()]
        mask = y.notna()
        self.model_ = lgb.LGBMRegressor(**self.params, random_state=self.seed, verbose=-1, deterministic=True)
        self.model_.fit(X.loc[mask, self.cols_], y[mask])
        return self

    def predict(self, X: pd.DataFrame, log_s=None) -> np.ndarray:
        return self.model_.predict(X[self.cols_])

    def explain(self, x_row: pd.DataFrame) -> dict:
        """Valores SHAP (TreeSHAP nativo de LightGBM) del pronóstico de esa fila."""
        contrib = self.model_.predict(x_row[self.cols_], pred_contrib=True)[0]
        return {c: float(v) for c, v in zip(self.cols_, contrib[:-1])}
