"""
cleanup_ropa_v2.py — Lista todos tus items activos en ML, identifica los que son ropa
por el título, y los cierra.
"""
import json, os, sys, time
try:
    import requests
except ImportError:
    print("pip install requests"); sys.exit(1)

ROPA_KW = ["POLERA","PANTALON","CHAQUETA","PARKA","CHALECO","BUZO",
           "CAMISETA","JEAN","POLAR","CAMISA","SHORT","OVEROL"]

API     = "https://api.mercadolibre.com"
APP_ID  = os.environ.get("ML_APP_ID", "").strip()
SECRET  = os.environ.get("ML_CLIENT_SECRET", "").strip()
REFRESH = os.environ.get("ML_REFRESH_TOKEN", "").strip()
USER_ID = os.environ.get("ML_USER_ID", "").strip()
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
    print("Token renovado.")

def headers():
    return {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# Renovar siempre al iniciar para evitar 401
renovar_token()

# Listar todos mis items activos
print(f"Listando items del usuario {USER_ID}...")
items_ids = []
offset = 0
while True:
    r = requests.get(f"{API}/users/{USER_ID}/items/search",
        headers=headers(),
        params={"limit": 50, "offset": offset})  # sin filtro de status: trae todo
    if not r.ok:
        print(f"ERR listando: {r.status_code} {r.text[:200]}"); break
    d = r.json()
    items_ids.extend(d.get("results", []))
    total = d.get("paging", {}).get("total", 0)
    if offset + 50 >= total: break
    offset += 50
    time.sleep(0.3)
print(f"Total items activos: {len(items_ids)}")

# Traer títulos en lotes de 20 (multiget de items)
def get_titles(ids):
    out = {}
    for i in range(0, len(ids), 20):
        chunk = ids[i:i+20]
        r = requests.get(f"{API}/items",
            headers=headers(),
            params={"ids": ",".join(chunk), "attributes": "id,title,status"})
        if r.ok:
            for it in r.json():
                body = it.get("body", {})
                out[body.get("id")] = body.get("title", "")
        time.sleep(0.2)
    return out

titles = get_titles(items_ids)

# Filtrar ropa
ropa = {iid: t for iid, t in titles.items()
        if any(kw in t.upper() for kw in ROPA_KW)}

print(f"\nItems de ropa detectados: {len(ropa)}")
for iid, t in list(ropa.items())[:20]:
    print(f"  {iid}  {t[:55]}")
if not ropa:
    print("Nada que cerrar."); sys.exit(0)

resp = input(f"\nCerrar {len(ropa)} items en ML? (s/N): ").strip().lower()
if resp != "s":
    print("Cancelado."); sys.exit(0)

ok = err = 0
for iid, t in ropa.items():
    r = requests.put(f"{API}/items/{iid}",
        headers=headers(), json={"status": "closed"})
    if r.ok:
        ok += 1
        print(f"  CERRADO {iid}  {t[:50]}")
    else:
        err += 1
        print(f"  ERR {iid}: {r.status_code} {r.text[:150]}")
        if r.status_code == 401:
            renovar_token()  # por si acaso
    time.sleep(0.3)

print(f"\nResumen: {ok} cerrados, {err} errores.")
