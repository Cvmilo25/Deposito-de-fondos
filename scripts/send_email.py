"""Envía un correo de aviso (vía Gmail SMTP) cuando hay fondos nuevos en la corrida.

Lee el CSV que deja `process.py --new-rows-output` (por defecto
data/nuevos_ultima_corrida.csv) con los fondos detectados por primera vez en esta
corrida. Si está vacío, no envía nada — el correo es un aviso de "hay algo nuevo",
no un resumen diario incondicional.

Por cada fondo intenta extraer el párrafo de "Objetivo del Fondo" desde su reglamento
interno (scripts/reglamento_objetivo.py, best-effort); si no lo logra, el correo
igual se envía con un link directo al reglamento en su lugar.

Requiere las variables de entorno GMAIL_USER y GMAIL_APP_PASSWORD (una "Contraseña de
aplicación" de Gmail, no la contraseña normal de la cuenta — se genera en
https://myaccount.google.com/apppasswords y se guarda como GitHub Secret, nunca en
el repo).

Uso:
    python scripts/send_email.py --new-rows data/nuevos_ultima_corrida.csv --to correo@ejemplo.com
"""
import argparse
import html
import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).parent))

from reglamento_objetivo import fetch_objetivo

TIPO_COLOR = {
    "Fondo Mutuo": "#2563eb",
    "Fondo Inversión No Rescatable": "#ea580c",
    "Fondo Inversión Rescatable": "#059669",
}


def fmt_fecha(s: str) -> str:
    try:
        return pd.to_datetime(s).strftime("%d-%m-%Y")
    except (ValueError, TypeError):
        return s or ""


def build_fund_block_html(row: dict, objetivo: str | None) -> str:
    color = TIPO_COLOR.get(row["tipo_fondo"], "#2563eb")
    if objetivo:
        objetivo_html = html.escape(objetivo)
    elif row.get("reglamento_url"):
        url = html.escape(row["reglamento_url"])
        objetivo_html = (
            f'No se pudo extraer el objetivo automáticamente. '
            f'<a href="{url}" target="_blank">Ver reglamento completo (PDF)</a>.'
        )
    else:
        objetivo_html = "No hay reglamento disponible para este fondo."

    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="border:1px solid #e2e2e2; border-radius:8px; margin-bottom:16px; font-family:Arial,Helvetica,sans-serif;">
      <tr>
        <td style="padding:14px 16px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
            <tr>
              <td style="font-size:13px; color:#666; padding-bottom:4px;">Administradora</td>
              <td style="font-size:13px; color:#666; padding-bottom:4px;">Tipo de fondo</td>
            </tr>
            <tr>
              <td style="font-size:14px; font-weight:bold; color:#111;">{html.escape(row['administradora'])}</td>
              <td>
                <span style="display:inline-block; background:{color}; color:#fff; font-size:11.5px;
                             padding:2px 8px; border-radius:10px;">{html.escape(row['tipo_fondo'])}</span>
              </td>
            </tr>
            <tr>
              <td colspan="2" style="font-size:13px; color:#666; padding-top:10px;">Fondo</td>
            </tr>
            <tr>
              <td colspan="2" style="font-size:15px; font-weight:bold; color:#111; padding-bottom:10px;">
                {html.escape(row['nombre_fondo'])}
              </td>
            </tr>
            <tr>
              <td colspan="2" style="font-size:13px; color:#666; padding-top:4px;">
                Fecha de depósito: {html.escape(fmt_fecha(row['fecha_deposito']))} · RUN: {html.escape(str(row['run_fondo']))}
              </td>
            </tr>
            <tr>
              <td colspan="2" style="font-size:13px; color:#666; padding-top:12px; padding-bottom:2px;">
                ¿Cuál es el objetivo? <span style="color:#999;">(según el reglamento interno)</span>
              </td>
            </tr>
            <tr>
              <td colspan="2" style="font-size:14px; color:#222; line-height:1.5;">
                {objetivo_html}
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
    """


def build_email(rows_with_objetivo: list[tuple[dict, str | None]], run_date: str) -> tuple[str, str, str]:
    n = len(rows_with_objetivo)
    subject = f"Depósito de Fondos CMF: {n} fondo{'s' if n != 1 else ''} nuevo{'s' if n != 1 else ''} ({run_date})"

    blocks = "".join(build_fund_block_html(row, objetivo) for row, objetivo in rows_with_objetivo)
    html_body = f"""
    <html><body style="margin:0; padding:20px; background:#f7f7f8;">
      <div style="max-width:640px; margin:0 auto;">
        <h2 style="font-family:Arial,Helvetica,sans-serif; color:#111;">
          {n} fondo{'s' if n != 1 else ''} nuevo{'s' if n != 1 else ''} depositado{'s' if n != 1 else ''} en la CMF
        </h2>
        {blocks}
        <p style="font-family:Arial,Helvetica,sans-serif; font-size:12px; color:#999;">
          Fuente: Registro Público de Depósito de Reglamentos Internos, CMF Chile.
        </p>
      </div>
    </body></html>
    """

    text_lines = [f"{n} fondo(s) nuevo(s) depositado(s) en la CMF ({run_date})", ""]
    for row, objetivo in rows_with_objetivo:
        text_lines.append(f"- {row['administradora']} — {row['nombre_fondo']} ({row['tipo_fondo']})")
        text_lines.append(f"  Objetivo: {objetivo or 'no disponible, ver ' + (row.get('reglamento_url') or 'reglamento')}")
        text_lines.append("")
    text_body = "\n".join(text_lines)

    return subject, html_body, text_body


def send_gmail(subject: str, html_body: str, text_body: str, sender: str, app_password: str, to_addr: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_addr
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
        server.starttls()
        server.login(sender, app_password)
        server.sendmail(sender, [to_addr], msg.as_string())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--new-rows", default="data/nuevos_ultima_corrida.csv")
    parser.add_argument("--to", required=True, help="Correo destinatario")
    args = parser.parse_args()

    gmail_user = os.environ.get("GMAIL_USER")
    gmail_app_password = os.environ.get("GMAIL_APP_PASSWORD")
    if not gmail_user or not gmail_app_password:
        print(
            "[send_email] Faltan GMAIL_USER / GMAIL_APP_PASSWORD en el entorno "
            "(deben venir como GitHub Secrets). No se puede enviar el correo."
        )
        return 1

    new_rows_path = Path(args.new_rows)
    if not new_rows_path.exists():
        print(f"[send_email] {new_rows_path} no existe (¿process.py no se ejecutó antes?). Sin envío.")
        return 0

    df = pd.read_csv(new_rows_path, dtype=str)
    if df.empty:
        print("[send_email] No hay fondos nuevos en esta corrida. Sin envío.")
        return 0

    print(f"[send_email] {len(df)} fondo(s) nuevo(s): extrayendo objetivo de cada reglamento...")
    session = requests.Session()
    rows_with_objetivo = []
    for _, row in df.iterrows():
        row_dict = row.to_dict()
        objetivo = fetch_objetivo(row_dict.get("reglamento_url"), session=session)
        rows_with_objetivo.append((row_dict, objetivo))

    run_date = rows_with_objetivo[0][0].get("primera_deteccion") or ""
    subject, html_body, text_body = build_email(rows_with_objetivo, run_date)

    try:
        send_gmail(subject, html_body, text_body, gmail_user, gmail_app_password, args.to)
    except smtplib.SMTPException as exc:
        print(f"[send_email] ERROR enviando el correo: {exc}")
        return 1

    print(f"[send_email] Correo enviado a {args.to}: {subject!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
