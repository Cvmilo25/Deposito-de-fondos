"""Procesa un Excel de "Depósito de Fondos Mutuos" descargado de la CMF y lo integra
al histórico versionado del proyecto.

Uso:
    python scripts/process.py --input data/raw/deposito_fondos_mutuos_latest.xlsx \
        --history data/historico_depositos.csv \
        --run-date 2026-08-20
"""
import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

from common import HISTORY_COLUMNS, merge_into_history, normalize_dataframe


def load_raw_excel(path: Path) -> pd.DataFrame:
    """El Excel de la CMF trae una fila en blanco antes del encabezado real (fila 2)."""
    df = pd.read_excel(path, header=None)
    header_row_idx = None
    for i in range(min(5, len(df))):
        row = df.iloc[i].astype(str)
        if row.str.contains("N° de registro", na=False).any():
            header_row_idx = i
            break
    if header_row_idx is None:
        raise ValueError(
            "No se encontró la fila de encabezado ('N° de registro') en las primeras filas "
            "del Excel. El formato de la fuente pudo haber cambiado."
        )
    df.columns = df.iloc[header_row_idx]
    df = df.iloc[header_row_idx + 1 :].reset_index(drop=True)
    return df


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Ruta al xlsx crudo descargado de la CMF")
    parser.add_argument(
        "--history",
        default="data/historico_depositos.csv",
        help="Ruta al CSV histórico a actualizar (se crea si no existe)",
    )
    parser.add_argument(
        "--run-date",
        default=None,
        help="Fecha (YYYY-MM-DD) a usar como 'primera_deteccion' para registros nuevos. "
        "Por defecto: hoy (UTC).",
    )
    parser.add_argument(
        "--publish-dir",
        default="docs/data",
        help="Carpeta donde se refleja una copia del CSV/JSON para que GitHub Pages "
        "(modo 'Deploy from a branch', sin Actions) la sirva directamente. "
        "Pasa '' para omitir esta copia.",
    )
    parser.add_argument(
        "--new-rows-output",
        default="data/nuevos_ultima_corrida.csv",
        help="Ruta donde dejar (siempre, incluso vacío) el CSV con solo los fondos detectados "
        "por primera vez en esta corrida — insumo de scripts/send_email.py. No se versiona "
        "en git (es transitorio, se regenera cada corrida). Pasa '' para omitir.",
    )
    args = parser.parse_args()

    run_date = args.run_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    input_path = Path(args.input)
    history_path = Path(args.history)

    print(f"Leyendo {input_path} ...")
    raw_df = load_raw_excel(input_path)
    print(f"  {len(raw_df)} filas crudas encontradas")

    new_df = normalize_dataframe(raw_df)
    print(f"  {len(new_df)} filas válidas tras normalizar")

    if history_path.exists():
        history_df = pd.read_csv(history_path, dtype=str)
    else:
        history_df = pd.DataFrame(columns=HISTORY_COLUMNS)

    before = len(history_df)
    combined = merge_into_history(history_df, new_df, run_date)
    after = len(combined)
    nuevos = after - before

    history_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(history_path, index=False)

    meta_path = history_path.parent / "last_update.json"
    meta_path.write_text(
        json.dumps(
            {
                "actualizado_en": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                "total_registros": after,
                "nuevos_en_esta_corrida": nuevos,
                "run_date": run_date,
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    if args.publish_dir:
        publish_dir = Path(args.publish_dir)
        publish_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(history_path, publish_dir / history_path.name)
        shutil.copy(meta_path, publish_dir / meta_path.name)
        print(f"Copia publicada en {publish_dir}/ (para GitHub Pages sin Actions)")

    if args.new_rows_output:
        new_rows_path = Path(args.new_rows_output)
        new_rows_path.parent.mkdir(parents=True, exist_ok=True)
        nuevas_filas = combined[combined["primera_deteccion"] == run_date]
        nuevas_filas.to_csv(new_rows_path, index=False)
        print(f"{len(nuevas_filas)} fondo(s) nuevo(s) de esta corrida en {new_rows_path}")

    print(f"Histórico actualizado: {before} -> {after} filas ({nuevos} nuevas)")

    # Resumen de "nuevos" según Fecha de Depósito real (no según cuándo lo detectamos),
    # tal como define el proyecto: últimos 30 días respecto a la fecha de depósito más
    # reciente disponible en los datos.
    combined["fecha_deposito_dt"] = pd.to_datetime(combined["fecha_deposito"])
    ultima_fecha = combined["fecha_deposito_dt"].max()
    for dias in (7, 30):
        corte = ultima_fecha - pd.Timedelta(days=dias)
        n = (combined["fecha_deposito_dt"] > corte).sum()
        print(f"  Depositados en los últimos {dias} días (desde {ultima_fecha.date()}): {n}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
