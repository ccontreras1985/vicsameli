"""
ml_auth.py — Autenticación OAuth con Mercado Libre (una sola vez)
==================================================================
Genera el access_token y refresh_token necesarios para sync_ml.py.

Uso:
    $env:ML_APP_ID = "tu_app_id"
    $env:ML_CLIENT_SECRET = "tu_client_secret"
    python ml_auth.py

El script:
1. Genera un link de autorización → lo abres en el browser
2. ML redirige a tu redirect_uri con un ?code=XXXX
3. Pegas el code aquí → el script obtiene los tokens
4. Guarda los tokens en output_vicsa/.env_ml (cárgalos en GitHub Secrets)
"""

import os, sys, json
from pathlib import Path
from datetime import datetime

try:
    import requests
except ImportError:
    print("pip install requests"); sys.exit(1)

API_BASE     = "https://api.mercadolibre.com"
REDIRECT_URI = "https://raispa.cl/callback"  # Debe coincidir con tu app en ML
OUT_FILE     = Path("output_vicsa/.env_ml")

def main():
    app_id        = os.environ.get("ML_APP_ID", "").strip()
    client_secret = os.environ.get("ML_CLIENT_SECRET", "").strip()

    if not app_id or not client_secret:
        print("\n" + "="*60)
        print("  Define las variables antes de correr:")
        print("  PowerShell:")
        print("    $env:ML_APP_ID = 'tu_app_id'")
        print("    $env:ML_CLIENT_SECRET = 'tu_client_secret'")
        print("="*60)
        sys.exit(1)

    # Paso 1: URL de autorización
    auth_url = (
        f"https://auth.mercadolibre.cl/authorization"
        f"?response_type=code"
        f"&client_id={app_id}"
        f"&redirect_uri={REDIRECT_URI}"
    )

    print("\n" + "="*65)
    print("  PASO 1: Abre este link en tu navegador y autoriza la app:")
    print(f"\n  {auth_url}\n")
    print("  Después de autorizar, ML te redirige a una URL como:")
    print(f"  {REDIRECT_URI}?code=TU_CODIGO_AQUI")
    print("="*65)

    code = input("\n  Pega aquí el valor del parámetro 'code' (solo el código): ").strip()

    if not code:
        print("ERROR: No ingresaste un código.")
        sys.exit(1)

    # Paso 2: Intercambiar code por tokens
    print("\nObteniendo tokens...")
    r = requests.post(f"{API_BASE}/oauth/token", data={
        "grant_type":    "authorization_code",
        "client_id":     app_id,
        "client_secret": client_secret,
        "code":          code,
        "redirect_uri":  REDIRECT_URI,
    })

    if not r.ok:
        print(f"ERROR: {r.status_code} {r.text}")
        sys.exit(1)

    tokens = r.json()
    access_token  = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    user_id       = str(tokens.get("user_id", ""))

    # Paso 3: Verificar identidad
    me = requests.get(f"{API_BASE}/users/me",
                      headers={"Authorization": f"Bearer {access_token}"})
    nickname = me.json().get("nickname", "desconocido") if me.ok else "desconocido"

    print(f"\n  ✓ Autenticado como: {nickname} (user_id: {user_id})")

    # Paso 4: Guardar tokens
    OUT_FILE.parent.mkdir(exist_ok=True)
    env_content = f"""# Tokens ML para RAI SPA — generados {datetime.now().strftime('%Y-%m-%d %H:%M')}
# Carga estos valores como GitHub Secrets:
#   ML_APP_ID, ML_CLIENT_SECRET, ML_ACCESS_TOKEN, ML_REFRESH_TOKEN, ML_USER_ID

ML_APP_ID={app_id}
ML_CLIENT_SECRET={client_secret}
ML_ACCESS_TOKEN={access_token}
ML_REFRESH_TOKEN={refresh_token}
ML_USER_ID={user_id}
"""
    OUT_FILE.write_text(env_content, encoding="utf-8")

    print(f"\n  Tokens guardados en: {OUT_FILE.resolve()}")
    print("\n" + "="*65)
    print("  PRÓXIMO PASO:")
    print("  1. Abre output_vicsa/.env_ml")
    print("  2. Copia cada valor como GitHub Secret en tu repositorio:")
    print("     https://github.com/TU_USUARIO/TU_REPO/settings/secrets/actions")
    print("  3. Agrega: ML_APP_ID, ML_CLIENT_SECRET, ML_ACCESS_TOKEN,")
    print("             ML_REFRESH_TOKEN, ML_USER_ID")
    print("="*65)

if __name__ == "__main__":
    main()
