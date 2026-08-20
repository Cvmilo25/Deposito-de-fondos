"""Descarga el Excel de "Depósito de Fondos Mutuos" desde el sitio oficial de la CMF.

No conocemos el DOM exacto de la página en tiempo de escritura (el entorno de
desarrollo no tiene salida de red hacia cmfchile.cl), así que esta estrategia es
deliberadamente defensiva y verbosa en sus logs, para poder diagnosticar por los
logs de GitHub Actions sin necesidad de inspeccionar el sitio manualmente:

  1. Descarga el HTML de la página con requests y busca enlaces que apunten a un
     Excel (href terminado en .xls/.xlsx, o que contenga "excel"/"export" en el
     texto/atributos).
  2. Si no encuentra nada (la página arma el botón vía JavaScript), recurre a
     Playwright: renderiza la página, intercepta la descarga que dispara el botón
     de exportar y la guarda directamente.
  3. Verifica que el archivo descargado sea realmente un Excel (firma binaria),
     no una página de error HTML.

Uso:
    python scripts/fetch_cmf.py --output data/raw/deposito_fondos_mutuos_latest.xlsx
"""
import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

PAGE_URL = "https://www.cmfchile.cl/institucional/inc/deposito_fondos_mutuos.php"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

EXCEL_LINK_RE = re.compile(r"\.xlsx?(\?|$)", re.IGNORECASE)
EXCEL_HINT_RE = re.compile(r"excel|exportar", re.IGNORECASE)

XLSX_MAGIC = b"PK\x03\x04"
XLS_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def is_excel_bytes(content: bytes) -> bool:
    return content[:4] == XLSX_MAGIC or content[:8] == XLS_MAGIC


def find_excel_link_in_html(html: str, base_url: str):
    soup = BeautifulSoup(html, "html.parser")
    candidates = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(" ", strip=True)
        score = 0
        if EXCEL_LINK_RE.search(href):
            score += 2
        if EXCEL_HINT_RE.search(href) or EXCEL_HINT_RE.search(text):
            score += 1
        if score:
            candidates.append((score, urljoin(base_url, href), text))

    # También revisa botones/formularios con atributos onclick que referencien un .php de export.
    for tag in soup.find_all(attrs={"onclick": True}):
        onclick = tag["onclick"]
        if EXCEL_HINT_RE.search(onclick):
            m = re.search(r"['\"]([^'\"]+\.php[^'\"]*)['\"]", onclick)
            if m:
                candidates.append((1, urljoin(base_url, m.group(1)), tag.get_text(" ", strip=True)))

    candidates.sort(key=lambda c: c[0], reverse=True)
    return candidates


def try_static_download(session: requests.Session, output_path: Path) -> bool:
    print(f"[fetch_cmf] GET {PAGE_URL}")
    resp = session.get(PAGE_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    print(f"[fetch_cmf]  status={resp.status_code} bytes={len(resp.content)}")

    candidates = find_excel_link_in_html(resp.text, PAGE_URL)
    print(f"[fetch_cmf] Candidatos de descarga encontrados en HTML estático: {len(candidates)}")
    for score, url, text in candidates[:10]:
        print(f"[fetch_cmf]   score={score} text={text!r} url={url}")

    for _, url, _ in candidates:
        print(f"[fetch_cmf] Intentando descargar: {url}")
        file_resp = session.get(url, headers=HEADERS, timeout=60)
        if file_resp.status_code == 200 and is_excel_bytes(file_resp.content):
            output_path.write_bytes(file_resp.content)
            print(f"[fetch_cmf] OK: guardado en {output_path} ({len(file_resp.content)} bytes)")
            return True
        print(
            f"[fetch_cmf]   descarte: status={file_resp.status_code} "
            f"content-type={file_resp.headers.get('Content-Type')} "
            f"primeros bytes={file_resp.content[:16]!r}"
        )
    return False


def try_playwright_download(output_path: Path) -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[fetch_cmf] Playwright no está instalado; no se puede usar el fallback JS.")
        return False

    print("[fetch_cmf] Fallback: renderizando la página con Playwright…")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=HEADERS["User-Agent"])
        page.goto(PAGE_URL, wait_until="networkidle", timeout=60000)

        html = page.content()
        candidates = find_excel_link_in_html(html, PAGE_URL)
        print(f"[fetch_cmf] Candidatos tras renderizar JS: {len(candidates)}")
        for score, url, text in candidates[:10]:
            print(f"[fetch_cmf]   score={score} text={text!r} url={url}")

        # Estrategia A: ya hay un <a href> real -> descargar directo por requests.
        for _, url, _ in candidates:
            file_resp = requests.get(url, headers=HEADERS, timeout=60)
            if file_resp.status_code == 200 and is_excel_bytes(file_resp.content):
                output_path.write_bytes(file_resp.content)
                print(f"[fetch_cmf] OK (link directo tras JS): {output_path}")
                browser.close()
                return True

        # Estrategia B: buscar cualquier botón/enlace visible con texto tipo "Excel" y clickearlo,
        # capturando el evento de descarga del navegador.
        locator = page.locator("text=/excel/i")
        count = locator.count()
        print(f"[fetch_cmf] Elementos visibles que matchean /excel/i: {count}")
        for i in range(count):
            try:
                with page.expect_download(timeout=15000) as dl_info:
                    locator.nth(i).click()
                download = dl_info.value
                tmp_path = output_path.with_suffix(".download")
                download.save_as(tmp_path)
                content = tmp_path.read_bytes()
                if is_excel_bytes(content):
                    tmp_path.rename(output_path)
                    print(f"[fetch_cmf] OK (descarga vía click #{i}): {output_path}")
                    browser.close()
                    return True
                tmp_path.unlink(missing_ok=True)
            except Exception as exc:  # noqa: BLE001 - queremos seguir probando otros elementos
                print(f"[fetch_cmf]   click #{i} no produjo una descarga válida: {exc}")

        browser.close()
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", default="data/raw/deposito_fondos_mutuos_latest.xlsx", help="Ruta de salida"
    )
    args = parser.parse_args()
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    ok = try_static_download(session, output_path)
    if not ok:
        ok = try_playwright_download(output_path)

    if not ok:
        print(
            "[fetch_cmf] ERROR: no se pudo encontrar/descargar el Excel de la CMF. "
            "La estructura de la página pudo haber cambiado; revisa los candidatos "
            "logueados arriba y ajusta EXCEL_LINK_RE/EXCEL_HINT_RE en este script."
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
