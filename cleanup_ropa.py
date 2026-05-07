"""
cleanup_ropa.py — Cierra publicaciones de ropa que se subieron por error
con foto de calzado (antes de agregar el filtro tipo='ropa' en sync_ml).
"""
import json, os, sys
from pathlib import Path

try:
    import requests
except ImportError:
    print("pip install requests"); sys.exit(1)

ROPA_KW = ["POLERA","PANTALON","CHAQUETA","PARKA","CHALECO","BUZO",
           "CAMISETA","JEAN","POLAR","CAMISA","SHORT","OVEROL"]

API   = "https://api.mercadolibre.com"
APP_ID  = os.environ.get("ML_APP_ID", "").strip()
SECRET  = os.environ.get("ML_CLIENT_SECRET", "").strip()
REFRESH = os.environ.get("ML_REFRESH_TOKEN", "").strip()
TOKEN   = os.environ.get("ML_ACCESS_TOKEN", "").strip()

def renovar_token():
    global TOKEN
    r = requests.post(f"{API}/oauth/token", data={
        "grant_type": "refresh_token", "client_id": APP_ID,
        "client_secret": SECRET, "refresh_token": REFRESH,
    })
    if not r.ok:
        print(f"ERROR renovando token: {r.status_code} {r.text}"); sys.exit(1)
    TOKEN = r.json()["access_token"]
    print(f"Token renovado.")

if not TOKEN or not APP_ID or not SECRET or not REFRESH:
    print("ERROR: Variables ML_* no definidas en el entorno"); sys.exit(1)

# Probar el token; si está expirado, renovar
test = requests.get(f"{API}/users/me", headers={"Authorization": f"Bearer {TOKEN}"})
if not test.ok:
    print("Token actual no funciona, renovando...")
    renovar_token()

state = json.load(open("output_vicsa/ml_state.json", encoding="utf-8"))
catalogo = json.load(open("output_vicsa/catalogo_vicsa_20260428_1942.json", encoding="utf-8"))

# Mapa SKU -> nombre
sku_a_nombre = {}
for p in catalogo["productos"]:
    for v in p["variantes"]:
        sku_a_nombre[v["sku"]] = p["nombre_base"]

# Detectar items que SON ropa
ropa_items = {}  # item_id -> primer nombre
for sku, item_id in state.items():
    nombre = sku_a_nombre.get(sku, "")
    n = nombre.upper()
    if any(kw in n for kw in ROPA_KW):
        if item_id not in ropa_items:
            ropa_items[item_id] = nombre

print(f"Items de ropa publicados por error: {len(ropa_items)}")
for iid, n in list(ropa_items.items())[:10]:
    print(f"  {iid}  {n[:55]}")

if not ropa_items:
    print("Nada que cerrar. Saliendo.")
    sys.exit(0)

resp = input(f"\nCerrar {len(ropa_items)} publicaciones en ML? (s/N): ").strip().lower()
if resp != "s":
    print("Cancelado."); sys.exit(0)

ok = err = 0
for item_id, nombre in ropa_items.items():
    # ML: status=closed cierra definitivamente. status=paused la pausa.
    r = requests.put(f"{API}/items/{item_id}",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        json={"status": "closed"})
    if r.ok:
        ok += 1
        print(f"  CERRADO {item_id}  {nombre[:50]}")
    else:
        err += 1
        print(f"  ERR {item_id}: {r.status_code} {r.text[:150]}")

print(f"\nResumen: {ok} cerrados, {err} errores.")

# Limpiar el state: quitar SKUs que apuntan a items cerrados
ropa_item_ids = set(ropa_items.keys())
state_limpio = {sku: iid for sku, iid in state.items() if iid not in ropa_item_ids}
removidos = len(state) - len(state_limpio)
with open("output_vicsa/ml_state.json", "w", encoding="utf-8") as f:
    json.dump(state_limpio, f, ensure_ascii=False, indent=2)
print(f"State actualizado: {removidos} SKUs removidos.")
