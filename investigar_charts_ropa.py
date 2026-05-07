"""
investigar_charts_ropa.py — Lista los SIZE_GRID charts existentes en tu cuenta ML
y muestra la estructura requerida para las categorías de ropa que usamos.
"""
import os, sys, json
try:
    import requests
except ImportError:
    print("pip install requests"); sys.exit(1)

API = "https://api.mercadolibre.com"
APP_ID  = os.environ.get("ML_APP_ID", "").strip()
SECRET  = os.environ.get("ML_CLIENT_SECRET", "").strip()
REFRESH = os.environ.get("ML_REFRESH_TOKEN", "").strip()
USER_ID = os.environ.get("ML_USER_ID", "").strip()
ACCESS  = os.environ.get("ML_ACCESS_TOKEN", "").strip()

# Intentar renovar; si falla (rotación), usar el ACCESS_TOKEN actual
TOKEN = None
r = requests.post(f"{API}/oauth/token", data={
    "grant_type":"refresh_token","client_id":APP_ID,
    "client_secret":SECRET,"refresh_token":REFRESH})
if r.ok and r.json().get("access_token"):
    TOKEN = r.json()["access_token"]
    new_refresh = r.json()["refresh_token"]
    print(f"Token renovado. Nuevo refresh: {new_refresh}")
    print(f"  → actualiza ML_REFRESH_TOKEN en GitHub Secrets y output_vicsa/.env_ml")
else:
    print(f"Refresh falló: {r.status_code} {r.text[:200]}")
    if ACCESS:
        print("Usando ML_ACCESS_TOKEN del entorno.")
        TOKEN = ACCESS
    else:
        print("ERROR: tampoco hay ML_ACCESS_TOKEN disponible. Corre ml_auth.py de nuevo."); sys.exit(1)
H = {"Authorization": f"Bearer {TOKEN}"}

ROPA_CATS = {
    "MLC440795": "Poleras",
    "MLC417961": "Pantalones de trabajo",
    "MLC433707": "Camperas / Parkas",
}

print("\n=== 1) Charts existentes en tu cuenta ===")
r = requests.get(f"{API}/users/{USER_ID}/charts", headers=H)
if r.ok:
    charts = r.json().get("charts", []) if isinstance(r.json(), dict) else r.json()
    if not charts:
        print("  (sin charts guardados)")
    for c in charts:
        cid = c.get("id") or c.get("chart_id")
        names = c.get("names", {})
        domain = c.get("domain_id", "?")
        print(f"  chart_id={cid}  domain={domain}  names={names}")
else:
    print(f"  ERR {r.status_code}: {r.text[:300]}")

print("\n=== 2) Atributo SIZE_GRID_ID requerido por categoría ropa ===")
for cat_id, nombre in ROPA_CATS.items():
    print(f"\n--- {cat_id} ({nombre}) ---")
    r = requests.get(f"{API}/categories/{cat_id}/attributes", headers=H)
    if not r.ok:
        print(f"  ERR {r.status_code}"); continue
    attrs = r.json()
    grid_attr = next((a for a in attrs if a["id"] == "SIZE_GRID_ID"), None)
    if grid_attr:
        print(f"  SIZE_GRID_ID requerido: {grid_attr.get('tags', {})}")
        domains = grid_attr.get("allowed_units") or grid_attr.get("values", [])
        print(f"    metadata: {grid_attr.get('value_type')} / hierarchy: {grid_attr.get('hierarchy')}")
    else:
        print("  Esta categoría NO usa SIZE_GRID_ID (no requiere chart)")

print("\n=== 3) Charts disponibles para CALZADO (referencia) ===")
# Búsqueda de charts por dominio
for domain in ["APPAREL_LOWERS", "APPAREL_UPPERS", "JACKETS_AND_COATS", "TROUSERS"]:
    r = requests.get(f"{API}/domains/{domain}/charts/search", headers=H,
        params={"site_id": "MLC", "domain_id": domain})
    if r.ok:
        d = r.json()
        n = len(d.get("charts", []))
        print(f"  {domain}: {n} charts disponibles")
