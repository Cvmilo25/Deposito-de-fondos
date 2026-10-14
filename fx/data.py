"""Descarga de series abiertas para el modelo USD/CLP y construcción del panel diario.

Fuentes (todas sin credenciales):
  - mindicador.cl : dólar observado (objetivo), TPM e IPC de Chile.
  - Yahoo Finance : cobre, WTI, DXY, BRL, MXN, COP, EEM, S&P 500 y USD/CLP spot (validación).
  - FRED          : VIX, UST 2 años, Fed Funds efectiva, CPI de EE.UU.

Convención de fechas (clave para no tener "look-ahead"):
  - El dólar observado con fecha t se fija con transacciones de t-1 y se publica la tarde de t-1.
    Por eso el panel está indexado por la fecha del dólar observado y las series de mercado se
    alinean con su cierre del día hábil anterior (`shift(1)` sobre el calendario del panel).
  - Series mensuales (IPC Chile, CPI EE.UU.) se usan solo desde su fecha de publicación
    aproximada (día 10 y día 15 del mes siguiente, respectivamente).

Cada fuente se guarda cruda en data/fx/raw/*.csv. Si una descarga falla, se usa la caché
anterior (el modelo sigue corriendo con el último dato disponible) y se deja aviso en el log.
"""
from __future__ import annotations

import io
import logging
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import requests

log = logging.getLogger(__name__)

START = "2007-01-01"
RAW_DIR = Path("data/fx/raw")

YAHOO_TICKERS = {
    "copper": "HG=F",
    "wti": "CL=F",
    "dxy": "DX-Y.NYB",
    "brl": "BRL=X",
    "mxn": "MXN=X",
    "cop": "COP=X",
    "eem": "EEM",
    "spx": "^GSPC",
    "clp_spot": "CLP=X",
}
FRED_SERIES = {
    "vix": "VIXCLS",
    "ust2y": "DGS2",
    "fedfunds": "DFF",
    "us_cpi": "CPIAUCSL",
}
MINDICADOR_SERIES = {
    "dolar_obs": "dolar",
    "tpm": "tpm",
    "ipc_cl": "ipc",  # variación mensual en %
}
# Rezago de publicación de las series mensuales (días desde el inicio del mes siguiente).
MONTHLY_PUBLICATION_DAY = {"ipc_cl": 10, "us_cpi": 15}

HTTP_TIMEOUT = 30
HEADERS = {"User-Agent": "Mozilla/5.0 (fx-forecast; +https://github.com/cvmilo25/deposito-de-fondos)"}


# --------------------------------------------------------------------------------------
# Descargas
# --------------------------------------------------------------------------------------
def fetch_mindicador(code: str, years: list[int], session: requests.Session) -> pd.Series:
    parts = []
    for y in years:
        r = session.get(f"https://mindicador.cl/api/{code}/{y}", timeout=HTTP_TIMEOUT, headers=HEADERS)
        r.raise_for_status()
        serie = r.json().get("serie", [])
        if not serie:
            continue
        df = pd.DataFrame(serie)
        # mindicador entrega "2024-01-02T03:00:00.000Z" (medianoche hora Chile en UTC).
        fechas = pd.to_datetime(df["fecha"], utc=True).dt.tz_convert("America/Santiago").dt.normalize()
        parts.append(pd.Series(df["valor"].astype(float).values, index=fechas.dt.tz_localize(None)))
    if not parts:
        return pd.Series(dtype=float)
    s = pd.concat(parts).sort_index()
    return s[~s.index.duplicated(keep="last")]


def fetch_fred(series_id: str, session: requests.Session) -> pd.Series:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={START}"
    r = session.get(url, timeout=HTTP_TIMEOUT, headers=HEADERS)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = ["date", "value"]
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")  # FRED usa "." para faltantes
    return df.set_index("date")["value"].dropna()


def fetch_yahoo(ticker: str) -> pd.Series:
    import yfinance as yf

    df = yf.download(ticker, start=START, progress=False, auto_adjust=True, threads=False)
    if df is None or df.empty:
        raise RuntimeError(f"Yahoo no devolvió datos para {ticker}")
    close = df["Close"]
    if isinstance(close, pd.DataFrame):  # yfinance >= 0.2.48 devuelve columnas MultiIndex
        close = close.iloc[:, 0]
    close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
    return close.dropna().astype(float)


def _cache_path(name: str, raw_dir: Path) -> Path:
    return raw_dir / f"{name}.csv"


def _save(name: str, s: pd.Series, raw_dir: Path) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    s.rename("value").to_frame().rename_axis("date").to_csv(_cache_path(name, raw_dir))


def load_cached(name: str, raw_dir: Path = RAW_DIR) -> pd.Series:
    p = _cache_path(name, raw_dir)
    if not p.exists():
        return pd.Series(dtype=float)
    df = pd.read_csv(p, parse_dates=["date"])
    return df.set_index("date")["value"].astype(float)


def download_all(raw_dir: Path = RAW_DIR, today: date | None = None) -> dict[str, pd.Series]:
    """Descarga todas las fuentes. mindicador solo re-descarga los 2 últimos años (el resto
    se toma de la caché) para no hacer ~20 requests en cada corrida diaria."""
    today = today or date.today()
    session = requests.Session()
    out: dict[str, pd.Series] = {}

    for name, code in MINDICADOR_SERIES.items():
        cached = load_cached(name, raw_dir)
        first_year = int(START[:4]) if cached.empty else max(int(START[:4]), today.year - 1)
        try:
            fresh = fetch_mindicador(code, list(range(first_year, today.year + 1)), session)
            s = pd.concat([cached, fresh]).sort_index()
            s = s[~s.index.duplicated(keep="last")]
            _save(name, s, raw_dir)
        except Exception as e:  # noqa: BLE001 - cualquier falla de red: usar caché
            log.warning("mindicador/%s falló (%s); se usa caché (%d filas)", code, e, len(cached))
            s = cached
        out[name] = s

    for name, sid in FRED_SERIES.items():
        try:
            s = fetch_fred(sid, session)
            _save(name, s, raw_dir)
        except Exception as e:  # noqa: BLE001
            log.warning("FRED/%s falló (%s); se usa caché", sid, e)
            s = load_cached(name, raw_dir)
        out[name] = s

    for name, ticker in YAHOO_TICKERS.items():
        try:
            s = fetch_yahoo(ticker)
            _save(name, s, raw_dir)
        except Exception as e:  # noqa: BLE001
            log.warning("Yahoo/%s falló (%s); se usa caché", ticker, e)
            s = load_cached(name, raw_dir)
        out[name] = s

    missing = [k for k, v in out.items() if v.empty]
    if "dolar_obs" in missing:
        raise RuntimeError("No hay datos del dólar observado (ni descarga ni caché): no se puede seguir.")
    if missing:
        log.warning("Series sin datos (se excluyen del modelo): %s", missing)
    return out


def load_all_cached(raw_dir: Path = RAW_DIR) -> dict[str, pd.Series]:
    names = list(MINDICADOR_SERIES) + list(FRED_SERIES) + list(YAHOO_TICKERS)
    return {n: load_cached(n, raw_dir) for n in names}


# --------------------------------------------------------------------------------------
# Limpieza y panel
# --------------------------------------------------------------------------------------
def clean_jumps(s: pd.Series, z: float = 8.0) -> pd.Series:
    """Elimina saltos aislados (error típico de Yahoo en monedas): un retorno extremo seguido
    de otro extremo de signo contrario. Los valores eliminados se re-imputan con ffill."""
    s = s[s > 0].copy()
    r = np.log(s).diff()
    sd = r.rolling(250, min_periods=60).std().bfill()
    spike = (r.abs() > z * sd) & (r.shift(-1).abs() > z * sd) & (np.sign(r) != np.sign(r.shift(-1)))
    if spike.any():
        log.info("clean_jumps: %d valores aislados removidos de %s", int(spike.sum()), s.name)
    return s[~spike]


def monthly_to_available(s: pd.Series, publication_day: int) -> pd.Series:
    """Re-indexa una serie mensual (fechada el día 1 del mes de referencia) a la fecha en
    que efectivamente se conoce: día `publication_day` del mes siguiente."""
    s = s.copy()
    months = s.index.to_period("M")
    s.index = (months + 1).to_timestamp() + pd.Timedelta(days=publication_day - 1)
    return s


def build_panel(raw: dict[str, pd.Series]) -> pd.DataFrame:
    """Panel diario indexado por las fechas del dólar observado.

    Columnas: dolar_obs (objetivo, sin rezago) + todas las demás series alineadas con la
    última información conocida antes de que se publique el dólar observado de ese día.
    """
    obs = raw["dolar_obs"].dropna()
    obs = obs[obs.index >= pd.Timestamp(START)]
    idx = obs.index
    panel = pd.DataFrame({"dolar_obs": obs})

    market = [k for k in list(YAHOO_TICKERS) + ["vix", "ust2y", "fedfunds"] if k in raw and not raw[k].empty]
    for k in market:
        s = raw[k].dropna()
        if k in YAHOO_TICKERS:
            s = clean_jumps(s.rename(k))
        # Valor conocido al cierre del día previo al dólar observado de cada fecha del panel.
        prev_day = idx - pd.Timedelta(days=1)
        panel[k] = s.reindex(s.index.union(prev_day)).sort_index().ffill().reindex(prev_day).values

    if "tpm" in raw and not raw["tpm"].empty:
        # La TPM vigente un día ya es conocida ese día (se anuncia antes de entrar en vigencia).
        s = raw["tpm"].dropna()
        panel["tpm"] = s.reindex(s.index.union(idx)).sort_index().ffill().reindex(idx).values

    if "ipc_cl" in raw and not raw["ipc_cl"].empty:
        # mindicador entrega la variación % mensual: se acumula a índice (base 100).
        v = raw["ipc_cl"].dropna().resample("MS").last().dropna()
        cl_index = 100 * (1 + v / 100).cumprod()
        s = monthly_to_available(cl_index, MONTHLY_PUBLICATION_DAY["ipc_cl"])
        panel["ipc_cl_idx"] = s.reindex(s.index.union(idx)).sort_index().ffill().reindex(idx).values

    if "us_cpi" in raw and not raw["us_cpi"].empty:
        v = raw["us_cpi"].dropna().resample("MS").last().dropna()
        s = monthly_to_available(v, MONTHLY_PUBLICATION_DAY["us_cpi"])
        panel["us_cpi"] = s.reindex(s.index.union(idx)).sort_index().ffill().reindex(idx).values

    panel.index.name = "fecha"
    return panel


def spot_vs_observado_check(panel: pd.DataFrame) -> dict:
    """Chequeo de calidad: diferencia % entre el spot de Yahoo (cierre t-1) y el dólar
    observado de t. Debería ser chica (<1% típico); si no, alguna fuente está mal."""
    if "clp_spot" not in panel:
        return {}
    d = (panel["clp_spot"] / panel["dolar_obs"] - 1).dropna() * 100
    return {
        "dif_media_pct": round(float(d.mean()), 3),
        "dif_abs_p95_pct": round(float(d.abs().quantile(0.95)), 3),
        "n": int(d.size),
    }
