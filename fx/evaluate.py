"""Métricas de pronóstico y tests estadísticos.

Los objetivos a 21 días se solapan, así que los errores consecutivos están autocorrelacionados
(MA(20) bajo la nula). Todos los tests usan varianza de largo plazo Newey-West con 20 rezagos.

- Theil-U        : RMSE(modelo) / RMSE(paseo aleatorio). < 1 = le gana al paseo aleatorio.
- Clark-West     : test de "el modelo agrega poder predictivo sobre el RW" (modelos anidados;
                   corrige el sesgo del DM cuando el benchmark es un caso particular).
- Diebold-Mariano: igualdad de precisión entre dos modelos no anidados.
- Pesaran-Timmermann: ¿el acierto direccional es mejor que el azar?
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

NW_LAGS = 20


def newey_west_var(x: np.ndarray, lags: int = NW_LAGS) -> float:
    """Varianza de largo plazo de x (kernel de Bartlett)."""
    x = np.asarray(x, float) - np.mean(x)
    n = len(x)
    v = x @ x / n
    for k in range(1, min(lags, n - 1) + 1):
        w = 1 - k / (lags + 1)
        v += 2 * w * (x[k:] @ x[:-k]) / n
    return max(v, 1e-18)


def diebold_mariano(e1: np.ndarray, e2: np.ndarray, lags: int = NW_LAGS) -> tuple[float, float]:
    """H0: igual MSE. Estadístico > 0 ⇒ el modelo 2 es más preciso. p-value bilateral."""
    d = np.asarray(e1) ** 2 - np.asarray(e2) ** 2
    n = len(d)
    stat = d.mean() / np.sqrt(newey_west_var(d, lags) / n)
    return float(stat), float(2 * (1 - stats.norm.cdf(abs(stat))))


def clark_west(y: np.ndarray, pred_model: np.ndarray, pred_bench: np.ndarray | None = None,
               lags: int = NW_LAGS) -> tuple[float, float]:
    """H0: el modelo no mejora al benchmark anidado (por defecto RW: pred = 0). p unilateral."""
    y, f = np.asarray(y), np.asarray(pred_model)
    b = np.zeros_like(f) if pred_bench is None else np.asarray(pred_bench)
    adj = (y - b) ** 2 - ((y - f) ** 2 - (b - f) ** 2)
    n = len(adj)
    stat = adj.mean() / np.sqrt(newey_west_var(adj, lags) / n)
    return float(stat), float(1 - stats.norm.cdf(stat))


def pesaran_timmermann(y: np.ndarray, pred: np.ndarray) -> tuple[float, float]:
    """Test de acierto direccional. p unilateral (H1: acierta más que el azar).
    Nota: con objetivos solapados el test es algo liberal; se reporta como referencia."""
    y, pred = np.asarray(y), np.asarray(pred)
    n = len(y)
    a, b = (y > 0).astype(float), (pred > 0).astype(float)
    p_hat = np.mean(a == b)
    py, px = a.mean(), b.mean()
    p_star = py * px + (1 - py) * (1 - px)
    v_hat = p_star * (1 - p_star) / n
    v_star = ((2 * py - 1) ** 2 * px * (1 - px) / n + (2 * px - 1) ** 2 * py * (1 - py) / n
              + 4 * py * px * (1 - py) * (1 - px) / n ** 2)
    denom = v_hat - v_star
    if denom <= 0:
        return float("nan"), float("nan")
    stat = (p_hat - p_star) / np.sqrt(denom)
    return float(stat), float(1 - stats.norm.cdf(stat))


def model_metrics(df: pd.DataFrame, model: str) -> dict:
    """`df` debe tener columnas: y (retorno log realizado), spot, real, pred_<m>, y opcionalmente
    lo80_<m>/hi80_<m>/lo95_<m>/hi95_<m> (niveles)."""
    d = df.dropna(subset=["y", f"ret_{model}"])
    y, f = d["y"].to_numpy(), d[f"ret_{model}"].to_numpy()
    e = y - f
    lvl_err = d["real"] - d[f"pred_{model}"]
    rmse = float(np.sqrt(np.mean(e ** 2)))
    rmse_rw = float(np.sqrt(np.mean(y ** 2)))
    out = {
        "n": int(len(d)),
        "rmse_clp": float(np.sqrt(np.mean(lvl_err ** 2))),
        "mae_clp": float(np.mean(np.abs(lvl_err))),
        "mape_pct": float(np.mean(np.abs(lvl_err / d["real"])) * 100),
        "theil_u": rmse / rmse_rw if rmse_rw > 0 else float("nan"),
        "acierto_direccional_pct": float(np.mean(np.sign(f) == np.sign(y)) * 100) if model != "rw" else None,
    }
    if model != "rw":
        out["clark_west_stat"], out["clark_west_p"] = clark_west(y, f)
        out["pesaran_timmermann_stat"], out["pesaran_timmermann_p"] = pesaran_timmermann(y, f)
    for lvl in (80, 95):
        lo, hi = f"lo{lvl}_{model}", f"hi{lvl}_{model}"
        if lo in d and d[lo].notna().any():
            m = d[lo].notna()
            inside = (d.loc[m, "real"] >= d.loc[m, lo]) & (d.loc[m, "real"] <= d.loc[m, hi])
            out[f"cobertura{lvl}_pct"] = float(inside.mean() * 100)
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in out.items()}


def rolling_theil(df: pd.DataFrame, models: list[str], window: int = 252) -> pd.DataFrame:
    """RMSE relativo al RW en ventana móvil de 12 meses (por fecha objetivo). Muestra la
    inestabilidad de la predictibilidad (punto central de Rossi 2013)."""
    d = df.dropna(subset=["y"]).set_index("fecha_objetivo").sort_index()
    rw = (d["y"] ** 2).rolling(window).mean()
    out = pd.DataFrame(index=d.index)
    for m in models:
        if m == "rw":
            continue
        out[m] = ((d["y"] - d[f"ret_{m}"]) ** 2).rolling(window).mean().div(rw).pow(0.5)
    return out.dropna(how="all")
