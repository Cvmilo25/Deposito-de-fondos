"""Extrae el párrafo de "Objetivo del Fondo" desde el PDF del reglamento interno.

El texto del objetivo no es un campo estructurado en el Excel de la CMF: solo existe
dentro del PDF del reglamento, en redacción libre y con un formato que puede variar
entre administradoras. Esta es una extracción heurística (best-effort): busca un
encabezado de sección tipo "Objetivo del Fondo" y captura el texto hasta el siguiente
encabezado o un tope de caracteres.

Si no encuentra la sección con confianza razonable, devuelve None — quien llame a esto
debe mostrar un respaldo (link directo al reglamento) en vez de inventar contenido.

Uso standalone (para probar el patrón contra un PDF real):
    python scripts/reglamento_objetivo.py --url https://www.cmfchile.cl/documentos/rfm/rfm_12345.pdf
"""
import argparse
import re
import sys
from io import BytesIO

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

MAX_OBJETIVO_CHARS = 900

# Variantes de encabezado observadas en reglamentos de fondos mutuos y de inversión.
# Se buscan como línea (o inicio de línea) para evitar falsos positivos dentro de
# un párrafo que solo menciona la palabra "objetivo" de pasada.
HEADING_RE = re.compile(
    r"""^\s*
    (?:art[íi]culo\s+\d+\s*[:\.\-]?\s*)?      # opcional: "Artículo 5:" antes del título
    (?:\d+\s*[\.\)]\s*)?                       # opcional: "5." o "5)" como numeral de sección
    objetivo
    (?:\s+(?:del|general|principal)?\s*(?:fondo|de\s+inversi[óo]n)?)?
    \s*[:\.\-]?\s*
    (?P<inline>.*)$                            # texto que pueda venir en la misma línea
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Una línea que probablemente sea OTRO encabezado de sección (para saber dónde cortar):
# corta, y con una proporción alta de mayúsculas entre sus letras.
NEXT_HEADING_RE = re.compile(r"^\s*(?:art[íi]culo\s+\d+|[\dIVX]+\s*[\.\)])", re.IGNORECASE)

# Bajo este largo, un "objetivo" capturado es casi siempre ruido: una entrada de tabla
# de contenidos ("Artículo 5: Objetivo del Fondo .......... 3") o un título repetido,
# no el párrafo real. Los reglamentos redactan el objetivo en al menos 1-2 oraciones.
MIN_OBJETIVO_CHARS = 60


def _looks_like_heading(line: str) -> bool:
    letters = [c for c in line if c.isalpha()]
    if len(letters) < 4 or len(line) > 90:
        return False
    upper_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
    return upper_ratio > 0.7


def _collect_body(lines: list[str], start_idx: int, inline: str) -> str:
    body_lines = []
    if inline and sum(c.isalpha() for c in inline) >= 15:
        body_lines.append(inline)
    total_len = len(" ".join(body_lines))

    for line in lines[start_idx:]:
        if not line:
            if body_lines:
                break  # párrafo en blanco tras ya tener texto: fin razonable de sección
            continue
        if NEXT_HEADING_RE.match(line) or (_looks_like_heading(line) and body_lines):
            break
        body_lines.append(line)
        total_len += len(line)
        if total_len >= MAX_OBJETIVO_CHARS:
            break

    return " ".join(body_lines).strip()


def find_objetivo_in_text(text: str) -> str | None:
    """Función pura (sin red/PDF) para poder testear el heurístico con texto sintético.

    Prueba TODOS los encabezados "Objetivo..." que encuentre (un reglamento suele
    mencionar el título en la tabla de contenidos antes de la sección real) y se
    queda con el primero cuyo cuerpo capturado sea sustancial, para no devolver una
    entrada de índice en vez del párrafo real.
    """
    lines = [ln.strip() for ln in text.splitlines()]

    for i, line in enumerate(lines):
        m = HEADING_RE.match(line)
        if not m:
            continue
        objetivo = _collect_body(lines, i + 1, (m.group("inline") or "").strip())
        if len(objetivo) >= MIN_OBJETIVO_CHARS:
            if len(objetivo) > MAX_OBJETIVO_CHARS:
                objetivo = objetivo[:MAX_OBJETIVO_CHARS].rsplit(" ", 1)[0] + "…"
            return objetivo

    return None


def extract_objetivo_from_pdf_bytes(pdf_bytes: bytes) -> str | None:
    from pypdf import PdfReader  # importado acá: no lo necesita quien solo usa find_objetivo_in_text

    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:  # noqa: BLE001 - PDF corrupto o formato inesperado
        print(f"[reglamento_objetivo]   no se pudo leer el PDF: {exc}")
        return None
    return find_objetivo_in_text(text)


def fetch_objetivo(url: str, session: requests.Session | None = None) -> str | None:
    if not url:
        return None
    session = session or requests.Session()
    try:
        resp = session.get(url, headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            print(f"[reglamento_objetivo]   HTTP {resp.status_code} en {url}")
            return None
        if resp.content[:4] != b"%PDF":
            print(f"[reglamento_objetivo]   respuesta no es un PDF válido: {url}")
            return None
    except requests.RequestException as exc:
        print(f"[reglamento_objetivo]   error de red en {url}: {exc}")
        return None

    objetivo = extract_objetivo_from_pdf_bytes(resp.content)
    if objetivo:
        print(f"[reglamento_objetivo]   OK ({len(objetivo)} caracteres) {url}")
    else:
        print(f"[reglamento_objetivo]   no se encontró sección 'Objetivo' en {url}")
    return objetivo


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="URL del PDF de reglamento a probar")
    args = parser.parse_args()

    objetivo = fetch_objetivo(args.url)
    if objetivo:
        print("\n== Objetivo extraído ==")
        print(objetivo)
        return 0
    print("\nNo se pudo extraer el objetivo de este reglamento.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
