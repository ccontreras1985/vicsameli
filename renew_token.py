"""Renueva el token y muestra los comandos para cargar las variables."""
import requests, json
from pathlib import Path

# Lee el .env_ml local
env = {}
for line in Path("output_vicsa/.env_ml").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()

r = requests.post("https://api.mercadolibre.com/oauth/token", data={
    "grant_type":    "refresh_token",
    "client_id":     env["ML_APP_ID"],
    "client_secret": env["ML_CLIENT_SECRET"],
    "refresh_token": env["ML_REFRESH_TOKEN"],
})
print(f"HTTP {r.status_code}")
if not r.ok:
    print("ERROR:", r.text)
    raise SystemExit(1)

d = r.json()
access  = d["access_token"]
refresh = d["refresh_token"]

# Actualiza .env_ml
new_lines = []
for line in Path("output_vicsa/.env_ml").read_text(encoding="utf-8").splitlines():
    if line.startswith("ML_ACCESS_TOKEN="):
        line = f"ML_ACCESS_TOKEN={access}"
    elif line.startswith("ML_REFRESH_TOKEN="):
        line = f"ML_REFRESH_TOKEN={refresh}"
    new_lines.append(line)
Path("output_vicsa/.env_ml").write_text("\n".join(new_lines) + "\n", encoding="utf-8")
print("\n.env_ml actualizado.\n")

print("=" * 70)
print("Pega estos comandos en PowerShell (NO CMD):")
print("=" * 70)
print(f'$env:ML_APP_ID = "{env["ML_APP_ID"]}"')
print(f'$env:ML_CLIENT_SECRET = "{env["ML_CLIENT_SECRET"]}"')
print(f'$env:ML_ACCESS_TOKEN = "{access}"')
print(f'$env:ML_REFRESH_TOKEN = "{refresh}"')
print(f'$env:ML_USER_ID = "{env["ML_USER_ID"]}"')
print()
print("RECUERDA: actualiza también el secret ML_REFRESH_TOKEN en GitHub:")
print(f"  {refresh}")
