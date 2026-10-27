"""Genera el token de Gmail que usa la corrida diaria. Se corre una sola vez, con una persona
frente al navegador, en el equipo donde quedará programado el agente.

    python scripts/autorizar_gmail.py --cliente client_secret.json --token token.json
"""

import argparse
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

ALCANCES = ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.send"]

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--cliente", type=Path, required=True, help="Credencial OAuth de escritorio descargada de Google Cloud")
    p.add_argument("--token", type=Path, default=Path("token.json"))
    p.add_argument("--solo-lectura", action="store_true", help="Sin permiso de envío (el informe no se manda por correo)")
    a = p.parse_args()
    flujo = InstalledAppFlow.from_client_secrets_file(str(a.cliente), ALCANCES[:1] if a.solo_lectura else ALCANCES)
    credenciales = flujo.run_local_server(port=0)
    a.token.write_text(credenciales.to_json(), encoding="utf-8")
    print(f"Token guardado en {a.token}. Trátelo como una contraseña: no lo suba al repositorio.")
