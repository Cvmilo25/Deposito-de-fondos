"""Valida (contra el sitio real de la CMF) qué porcentaje de las URLs de reglamento
construidas por scripts/common.py realmente resuelven a un PDF, desglosado por
Tipo de Fondo. El patrón de URL fue inferido por evidencia externa (no por
inspección directa del sitio) — este script es la comprobación real.

Uso:
    python scripts/validate_reglamento_links.py --history data/historico_depositos.csv --sample 15
"""
import argparse
import random
import sys
from collections import defaultdict

import pandas as pd
import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


def check_url(url: str) -> str:
    try:
        resp = requests.head(url, headers=HEADERS, timeout=20, allow_redirects=True)
        if resp.status_code == 200:
            ctype = resp.headers.get("Content-Type", "")
            return "OK" if "pdf" in ctype.lower() or resp.headers.get("Content-Length") else "OK?"
        if resp.status_code in (403, 405):
            # Algunos servidores no soportan HEAD; reintenta con GET liviano.
            resp = requests.get(url, headers=HEADERS, timeout=20, stream=True)
            resp.close()
            return "OK" if resp.status_code == 200 else f"HTTP {resp.status_code}"
        return f"HTTP {resp.status_code}"
    except requests.RequestException as exc:
        return f"ERROR {exc.__class__.__name__}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", default="data/historico_depositos.csv")
    parser.add_argument("--sample", type=int, default=15, help="Muestras por tipo de fondo")
    args = parser.parse_args()

    df = pd.read_csv(args.history, dtype=str)
    df = df.dropna(subset=["reglamento_url"])

    results = defaultdict(lambda: defaultdict(int))
    examples_fail = []

    for tipo, group in df.groupby("tipo_fondo"):
        sample = group.sample(min(args.sample, len(group)), random_state=42)
        print(f"\n== {tipo} ({len(sample)} muestras de {len(group)}) ==")
        for _, row in sample.iterrows():
            status = check_url(row["reglamento_url"])
            results[tipo][status] += 1
            marker = "OK" if status.startswith("OK") else "FAIL"
            print(f"  [{marker}] {status:12s} {row['reglamento_url']}")
            if marker == "FAIL":
                examples_fail.append((tipo, row["reglamento_url"]))

    print("\n== Resumen ==")
    for tipo, counts in results.items():
        total = sum(counts.values())
        ok = sum(v for k, v in counts.items() if k.startswith("OK"))
        print(f"  {tipo}: {ok}/{total} OK ({counts})")

    if examples_fail:
        print(
            "\nHay URLs que no resolvieron. Si el patrón falla sistemáticamente para un "
            "Tipo de Fondo, hay que ajustar build_reglamento_url() en scripts/common.py "
            "para ese tipo (posible prefijo distinto de 'rfm_')."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
