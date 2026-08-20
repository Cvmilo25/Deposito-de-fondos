"""Normalización compartida de los datos de Depósito de Fondos Mutuos (CMF Chile).

Fuente oficial: https://www.cmfchile.cl/institucional/inc/deposito_fondos_mutuos.php
"""
import re

import pandas as pd

# Mapeo de columnas tal como vienen en el Excel exportado por la CMF -> nombres normalizados.
COLUMN_MAP = {
    "N° de registro": "id_registro",
    "Fecha de depósito": "fecha_deposito_raw",
    "RUN del Fondo Mutuo": "run_fondo",
    "Nombre del Fondo Mutuo": "nombre_fondo",
    "Nombre Administradora": "administradora",
    "Fecha última modificación": "fecha_ultima_modificacion_raw",
    "Estado (Vigente/No Vigente)": "estado_registro",
    "Estado (indica si fondo está liquidado)": "estado_liquidacion",
    "Reglamento Interno Vigente": "reglamento_codigo",
    "Tipo Fondo": "tipo_fondo",
}

HISTORY_COLUMNS = [
    "id_registro",
    "fecha_deposito",
    "run_fondo",
    "nombre_fondo",
    "administradora",
    "estado_registro",
    "estado_liquidacion",
    "tipo_fondo",
    "reglamento_codigo",
    "reglamento_url",
    "fecha_ultima_modificacion",
    "primera_deteccion",
]

# Patrón confirmado vía documentos reales de la CMF (ver README para el detalle de la
# validación): el código de "Reglamento Interno Vigente" es el identificador del PDF
# publicado bajo /documentos/rfm/. No verificado al 100% para Fondos de Inversión;
# scripts/validate_reglamento_links.py mide la tasa real de éxito contra el sitio.
REGLAMENTO_BASE_URL = "https://www.cmfchile.cl/documentos/rfm/rfm_{codigo}.pdf"


def _parse_fecha(value):
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return None
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        d, mo, y = m.groups()
        return f"{y}-{int(mo):02d}-{int(d):02d}"
    ts = pd.to_datetime(s, dayfirst=True, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.strftime("%Y-%m-%d")


def build_reglamento_url(codigo):
    if codigo is None:
        return None
    c = str(codigo).strip()
    if not c or not c.isdigit():
        return None
    return REGLAMENTO_BASE_URL.format(codigo=c)


def normalize_dataframe(df_raw):
    """Convierte el DataFrame leído directamente del Excel de la CMF al esquema normalizado."""
    missing = [c for c in COLUMN_MAP if c not in df_raw.columns]
    if missing:
        raise ValueError(
            "Columnas esperadas no encontradas en el Excel de la CMF (¿cambió el formato de "
            f"la fuente?): {missing}. Columnas disponibles: {list(df_raw.columns)}"
        )

    df = df_raw.rename(columns=COLUMN_MAP)[list(COLUMN_MAP.values())].copy()

    df["fecha_deposito"] = df["fecha_deposito_raw"].map(_parse_fecha)
    df["fecha_ultima_modificacion"] = df["fecha_ultima_modificacion_raw"].map(_parse_fecha)

    df["reglamento_codigo"] = df["reglamento_codigo"].astype(str).str.strip()
    df.loc[df["reglamento_codigo"].isin(["", "None", "nan"]), "reglamento_codigo"] = None
    df["reglamento_url"] = df["reglamento_codigo"].map(build_reglamento_url)

    df["administradora"] = df["administradora"].astype(str).str.strip()
    df["nombre_fondo"] = df["nombre_fondo"].astype(str).str.strip()
    df["tipo_fondo"] = df["tipo_fondo"].astype(str).str.strip()
    df["estado_registro"] = df["estado_registro"].astype(str).str.strip()
    df["id_registro"] = df["id_registro"].astype(str).str.strip()

    df["run_fondo"] = df["run_fondo"].apply(
        lambda v: str(int(v)) if pd.notna(v) and str(v).strip() != "" else None
    )

    df = df.drop(columns=["fecha_deposito_raw", "fecha_ultima_modificacion_raw"])
    df = df.dropna(subset=["id_registro", "fecha_deposito"])
    df = df[df["id_registro"] != ""]
    return df


def merge_into_history(history_df, new_df, run_date):
    """Combina new_df (ya normalizado) dentro de history_df por id_registro.

    Si un id_registro ya existía, se actualiza con los datos más recientes (la CMF puede
    modificar el estado o el reglamento vigente de un registro existente) pero conserva su
    'primera_deteccion' original. Los id_registro nuevos quedan con primera_deteccion=run_date.
    """
    new_df = new_df.copy()
    new_df["primera_deteccion"] = run_date

    if history_df is None or history_df.empty:
        combined = new_df
    else:
        previous_first_seen = history_df.set_index("id_registro")["primera_deteccion"].to_dict()
        combined = pd.concat([history_df, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["id_registro"], keep="last")
        combined["primera_deteccion"] = combined.apply(
            lambda r: previous_first_seen.get(r["id_registro"], r["primera_deteccion"]),
            axis=1,
        )

    combined = combined.sort_values("fecha_deposito", ascending=False).reset_index(drop=True)
    return combined[HISTORY_COLUMNS]
