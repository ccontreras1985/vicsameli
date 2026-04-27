"""
sync_ml.py — RAI SPA · Sincronizador Vicsa → Mercado Libre
============================================================
Lee el JSON generado por scraper_v13.py y:
  - MODO completo: publica productos nuevos o actualiza existentes en ML
  - MODO delta:    procesa stock_delta_FECHA.json → solo actualiza stock/precio

Requiere variables de entorno:
    ML_APP_ID        App ID de tu aplicación en developers.mercadolibre.com
    ML_CLIENT_SECRET Client Secret de tu aplicación
    ML_ACCESS_TOKEN  Access Token (se obtiene con ml_auth.py la primera vez)
    ML_REFRESH_TOKEN Refresh Token (se renueva automáticamente)
    ML_USER_ID       Tu User ID de ML (se obtiene con ml_auth.py)

Uso:
    python ml_auth.py                                         # Primera vez
    python sync_ml.py --json catalogo_vicsa_FECHA.json --dry-run
    python sync_ml.py --json catalogo_vicsa_FECHA.json --categorias "Calzado de Seguridad"
    python sync_ml.py --delta stock_delta_FECHA.json
"""

import json, os, sys, time, argparse, re
from pathlib import Path
from datetime import datetime

try:
    import requests
except ImportError:
    print("pip install requests"); sys.exit(1)

SITE_ID    = "MLC"
API_BASE   = "https://api.mercadolibre.com"
STATE_FILE = Path("output_vicsa/ml_state.json")
MARGEN     = 1.45        # 45% sobre precio costo Vicsa
MAX_REINTENTOS  = 3
DELAY_ITEMS     = 0.5    # segundos entre publicaciones (rate limit ML)

CATEGORIA_ML = {
    "Calzado de Seguridad":    "MLC1278",
    "Ropa Tecnica":            "MLC1275",
    "Ropa de Trabajo":         "MLC1275",
    "Guantes":                 "MLC1276",
    "Cascos":                  "MLC1277",
    "Lentes de Seguridad":     "MLC430399",
    "Proteccion Auditiva":     "MLC430400",
    "Proteccion Respiratoria": "MLC430401",
    "Proteccion Facial":       "MLC430402",
    "Seguridad Vial":          "MLC3025",
    "Primeros Auxilios":       "MLC3026",
    "Proteccion Solar":        "MLC1280",
    "Ergonomia":               "MLC1279",
}

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def precio_venta(costo: float) -> int:
    return max(1, round(costo * MARGEN))

def limpiar_titulo(nombre: str) -> str:
    titulo = nombre.strip().title()
    titulo = re.sub(r'[|"<>{}[\]\\^~`]', '', titulo)
    titulo = re.sub(r'\s+', ' ', titulo).strip()
    if len(titulo) > 60:
        titulo = titulo[:57].rsplit(' ', 1)[0] + "..."
    return titulo

def construir_descripcion(prod: dict) -> str:
    partes = [prod["nombre_base"].title(), ""]
    if prod.get("especificaciones"):
        partes += ["ESPECIFICACIONES:", prod["especificaciones"][:800], ""]
    if prod.get("certificaciones"):
        partes += ["CERTIFICACIONES:", prod["certificaciones"][:300], ""]
    colores = sorted(set(v["color"] for v in prod["variantes"] if v.get("color")))
    tallas  = sorted(set(v["talla"] for v in prod["variantes"] if v.get("talla")))
    if colores: partes.append(f"Colores: {', '.join(colores)}")
    if tallas:  partes.append(f"Tallas: {', '.join(tallas)}")
    partes += ["", "Distribuidor autorizado VICSA · Concepción", "RAI SpA · RUT 78.365.289-7"]
    return "\n".join(partes)

def construir_atributos(prod: dict) -> list:
    attrs = []
    n = prod["nombre_base"].upper()
    for marca in ["QUEBEC","ALASKA","HARDWORK","HW","STEELPRO"]:
        if marca in n:
            attrs.append({"id": "BRAND", "value_name": marca.title()})
            break
    if any(x in n for x in ["MUJER","FEMME","DAMA"]):
        attrs.append({"id": "GENDER", "value_name": "Mujer"})
    elif "HOMBRE" in n:
        attrs.append({"id": "GENDER", "value_name": "Hombre"})
    else:
        attrs.append({"id": "GENDER", "value_name": "Unisex"})
    return attrs

def construir_variaciones(prod: dict) -> list:
    variaciones = []
    for v in prod["variantes"]:
        if v.get("stock_publicar", 0) < 0:
            continue
        combos = {}
        if v.get("talla"): combos["Talla"] = v["talla"]
        if v.get("color"): combos["Color"] = v["color"]
        var = {
            "attribute_combinations": [
                {"id": k, "value_name": val} for k, val in combos.items()
            ] if combos else [{"id": "Talla", "value_name": "Única"}],
            "price":              precio_venta(v["precio_neto"]),
            "available_quantity": max(0, v.get("stock_publicar", 1)),
            "seller_custom_field": v["sku"],
        }
        variaciones.append(var)
    return variaciones

class MLClient:
    def __init__(self):
        self.app_id        = os.environ.get("ML_APP_ID", "")
        self.client_secret = os.environ.get("ML_CLIENT_SECRET", "")
        self.access_token  = os.environ.get("ML_ACCESS_TOKEN", "")
        self.refresh_token = os.environ.get("ML_REFRESH_TOKEN", "")
        self.user_id       = os.environ.get("ML_USER_ID", "")
        self.s             = requests.Session()
        if not all([self.app_id, self.client_secret, self.access_token, self.user_id]):
            print("\nERROR: Variables de entorno ML no encontradas.")
            print("Ejecuta primero: python ml_auth.py")
            sys.exit(1)

    def _h(self):
        return {"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"}

    def _renovar(self):
        r = self.s.post(f"{API_BASE}/oauth/token", data={
            "grant_type": "refresh_token", "client_id": self.app_id,
            "client_secret": self.client_secret, "refresh_token": self.refresh_token,
        })
        if r.ok:
            d = r.json()
            self.access_token  = d["access_token"]
            self.refresh_token = d["refresh_token"]
            os.environ["ML_ACCESS_TOKEN"]  = self.access_token
            os.environ["ML_REFRESH_TOKEN"] = self.refresh_token
            log("Token renovado ✓")

    def _req(self, metodo, url, **kwargs):
        for i in range(MAX_REINTENTOS):
            r = getattr(self.s, metodo)(f"{API_BASE}{url}", headers=self._h(), **kwargs)
            if r.status_code == 401: self._renovar(); continue
            if r.status_code == 429: time.sleep(2**i); continue
            return r
        return r

    def get(self, url, params=None):   return self._req("get",  url, params=params)
    def post(self, url, data):         return self._req("post", url, json=data)
    def put(self, url, data):          return self._req("put",  url, json=data)

def cargar_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}

def guardar_state(state: dict):
    STATE_FILE.parent.mkdir(exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def publicar_nuevo(prod: dict, ml: MLClient, dry_run: bool):
    variaciones = construir_variaciones(prod)
    if not variaciones:
        return None

    cat_ml    = CATEGORIA_ML.get(prod["categoria"], "MLC1275")
    precio_b  = min(precio_venta(v["precio_neto"]) for v in prod["variantes"] if v["precio_neto"] > 0)
    stock_tot = sum(v.get("stock_publicar", 1) for v in prod["variantes"])

    payload = {
        "title":              limpiar_titulo(prod["nombre_base"]),
        "category_id":        cat_ml,
        "price":              precio_b,
        "currency_id":        "CLP",
        "available_quantity": stock_tot,
        "buying_mode":        "buy_it_now",
        "condition":          "new",
        "listing_type_id":    "gold_special",
        "description":        {"plain_text": construir_descripcion(prod)},
        "attributes":         construir_atributos(prod),
        "shipping":           {"mode": "me2", "local_pick_up": False, "free_shipping": False},
        "sale_terms": [
            {"id": "WARRANTY_TYPE", "value_name": "Garantía del vendedor"},
            {"id": "WARRANTY_TIME", "value_name": "3 meses"},
        ],
    }

    # Variaciones solo si hay más de una o tiene talla/color real
    tiene_attrs = any(
        v["attribute_combinations"][0]["value_name"] != "Única"
        for v in variaciones
    )
    if len(variaciones) > 1 or tiene_attrs:
        payload["variations"] = variaciones
    else:
        payload["available_quantity"] = variaciones[0]["available_quantity"]
        payload["price"]              = variaciones[0]["price"]

    if dry_run:
        log(f"  [DRY] {payload['title'][:50]} | ${precio_b:,} | {stock_tot} uds")
        return "DRY"

    r = ml.post("/items", payload)
    if r.status_code in (200, 201):
        item_id = r.json().get("id")
        log(f"  ✓ {payload['title'][:45]} → {item_id}")
        return item_id
    log(f"  ✗ {prod['nombre_base'][:40]}: {r.status_code} {r.text[:180]}")
    return None

def actualizar_item(sku: str, item_id: str, stock: int, costo: float,
                    ml: MLClient, dry_run: bool) -> bool:
    precio  = precio_venta(costo)
    status  = "active" if stock > 0 else "paused"
    payload = {"available_quantity": stock, "price": precio, "status": status}
    if dry_run:
        log(f"  [DRY] {item_id}: stock={stock} ${precio:,} {status}")
        return True
    r = ml.put(f"/items/{item_id}", payload)
    if r.ok:
        icono = "⏸" if status == "paused" else "✓"
        log(f"  {icono} {item_id} stock={stock} ${precio:,}")
        return True
    log(f"  ✗ {item_id}: {r.status_code} {r.text[:120]}")
    return False

def sync_completo(path: Path, ml: MLClient, state: dict, dry_run: bool, cats: list):
    log(f"=== SYNC COMPLETO: {path.name} ===")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    prods = data["productos"]
    if cats:
        prods = [p for p in prods if p["categoria"] in cats]
    log(f"Productos a procesar: {len(prods)}")

    nuevos = act = skip = err = 0
    for i, prod in enumerate(prods):
        log(f"[{i+1}/{len(prods)}] {prod['nombre_base'][:50]}")
        tiene_stock = any(v.get("stock_publicar", 0) > 0 for v in prod["variantes"])
        ml_id = next((state[v["sku"]] for v in prod["variantes"] if v["sku"] in state), None)

        if ml_id:
            v0 = prod["variantes"][0]
            ok = actualizar_item(v0["sku"], ml_id, v0.get("stock_publicar",0), v0["precio_neto"], ml, dry_run)
            act += 1 if ok else 0
            err += 0 if ok else 1
        elif not tiene_stock:
            log(f"  SKIP sin stock")
            skip += 1
        else:
            item_id = publicar_nuevo(prod, ml, dry_run)
            if item_id and item_id != "DRY":
                for v in prod["variantes"]: state[v["sku"]] = item_id
                guardar_state(state)
                nuevos += 1
            elif item_id == "DRY":
                nuevos += 1
            else:
                err += 1

        time.sleep(DELAY_ITEMS)

    log(f"\n{'='*50}")
    log(f"Nuevos: {nuevos} | Actualizados: {act} | Skip: {skip} | Errores: {err}")

def sync_delta(path: Path, ml: MLClient, state: dict, dry_run: bool):
    log(f"=== SYNC DELTA: {path.name} ===")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    cambios = data.get("cambios", [])
    log(f"Cambios: {len(cambios)}")

    act = sin_pub = err = 0
    for c in cambios:
        sku = c.get("sku")
        if sku not in state:
            sin_pub += 1
            continue
        ok = actualizar_item(sku, state[sku], c.get("stock_publicar",0), c.get("precio_neto",0), ml, dry_run)
        act += 1 if ok else 0
        err += 0 if ok else 1
        time.sleep(DELAY_ITEMS)

    log(f"\n{'='*50}")
    log(f"Actualizados: {act} | Sin publicación ML: {sin_pub} | Errores: {err}")

def main():
    parser = argparse.ArgumentParser()
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--json",  type=Path)
    g.add_argument("--delta", type=Path)
    parser.add_argument("--dry-run",    action="store_true")
    parser.add_argument("--categorias", nargs="+", default=[])
    args = parser.parse_args()

    for p in [args.json, args.delta]:
        if p and not p.exists():
            print(f"ERROR: No existe {p}"); sys.exit(1)

    ml    = MLClient()
    state = cargar_state()
    log(f"State: {len(state)} SKUs con ML item_id")
    if args.dry_run: log("DRY-RUN: no se publica nada")

    if args.json:  sync_completo(args.json,  ml, state, args.dry_run, args.categorias)
    else:          sync_delta(args.delta, ml, state, args.dry_run)

if __name__ == "__main__":
    main()
