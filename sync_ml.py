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
MARGEN     = 1.60        # 60% sobre precio costo Vicsa
MAX_REINTENTOS  = 3
DELAY_ITEMS     = 0.5    # segundos entre publicaciones (rate limit ML)

CATEGORIA_ML = {
    "Calzado de Seguridad":    "MLC179382",
    "Ropa Tecnica":            "MLC440795",
    "Ropa de Trabajo":         "MLC440795",
    "Guantes":                 "MLC3633",
    "Cascos":                  "MLC3632",
    "Lentes de Seguridad":     "MLC430399",
    "Proteccion Auditiva":     "MLC430400",
    "Proteccion Respiratoria": "MLC430401",
    "Proteccion Facial":       "MLC430402",
    "Seguridad Vial":          "MLC3025",
    "Primeros Auxilios":       "MLC3026",
    "Proteccion Solar":        "MLC1280",
    "Ergonomia":               "MLC1279",
}

# Subcategorías para ropa según tipo de prenda
ROPA_SUBCAT = {
    "PANTALON": "MLC417961",   # Pantalones de trabajo
    "JEAN":     "MLC417961",
    "CHAQUETA": "MLC433707",   # Camperas de trabajo
    "PARKA":    "MLC433707",
    "CHALECO":  "MLC433707",
    "BUZO":     "MLC440795",
    "POLERA":   "MLC440795",
    "CAMISETA": "MLC440795",
}

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True, file=open(1, 'w', encoding='utf-8', closefd=False))

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

def detectar_categoria_ropa(nombre: str) -> str:
    n = nombre.upper()
    for kw, cat in ROPA_SUBCAT.items():
        if kw in n:
            return cat
    return "MLC440795"  # Poleras por defecto

def es_calzado_real(nombre: str) -> bool:
    """Detecta calzado por el nombre del producto, no por la categoría de Vicsa
    (Vicsa lista poleras/buzos/duchas en la misma página de Calzado)."""
    n = nombre.upper()
    return any(kw in n for kw in ["CALZADO", "BOTA", "BOTIN", "BOTÍN", "ZAPATO"])

def detectar_tipo_producto(nombre: str) -> str:
    """Devuelve 'calzado', 'ropa', o 'otro' según el nombre del producto."""
    n = nombre.upper()
    if es_calzado_real(nombre):
        return "calzado"
    if any(kw in n for kw in ["POLERA","PANTALON","CHAQUETA","PARKA","CHALECO","BUZO",
                                "CAMISETA","JEAN","POLAR","CAMISA","SHORT","OVEROL"]):
        return "ropa"
    return "otro"

SIZE_GRID_ID = "5193042"
# Mapeo verificado: chart actual cubre tallas 35-45 (filas 1-11).
# Tallas 34, 46-48 se omitirán hasta crear un chart nuevo en ML.
SIZE_GRID_ROWS = {
    "35": "5193042:1",  "36": "5193042:2",  "37": "5193042:3",
    "38": "5193042:4",  "39": "5193042:5",  "40": "5193042:6",
    "41": "5193042:7",  "42": "5193042:8",  "43": "5193042:9",
    "44": "5193042:10", "45": "5193042:11",
}
FOTO_ID = "847653-MLC110957882757_042026"  # placeholder fallback
IMG_DIR = Path("output_vicsa/imagenes")
PICTURE_CACHE_FILE = Path("output_vicsa/ml_pictures_cache.json")

def construir_atributos(prod: dict) -> list:
    attrs = []
    n = prod["nombre_base"].upper()
    marca = "Quebec"
    for m in ["QUEBEC","ALASKA","HARDWORK","HW","STEELPRO"]:
        if m in n:
            marca = m.title()
            break
    attrs.append({"id": "BRAND", "value_name": marca})
    attrs.append({"id": "MODEL", "value_name": prod["nombre_base"].title()[:50]})
    if any(x in n for x in ["MUJER","FEMME","DAMA"]):
        gender_id, gender_name = "339665", "Mujer"
    elif "HOMBRE" in n:
        gender_id, gender_name = "339666", "Hombre"
    else:
        gender_id, gender_name = "110461", "Sin género"
    attrs.append({"id": "GENDER", "value_id": gender_id, "value_name": gender_name})
    if es_calzado_real(prod.get("nombre_base", "")):
        attrs.append({"id": "SIZE_GRID_ID", "value_name": SIZE_GRID_ID})
    return attrs

COLOR_KEYWORDS = {
    "BROWN": "Café", "BLACK": "Negro", "WHITE": "Blanco", "GREY": "Gris",
    "GRAY": "Gris", "DARK": "Negro", "LIGHT": "Beige", "BEIGE": "Beige",
    "TERRACOTA": "Café", "OIL": "Negro", "TX": "Negro",
    "CAFE": "Café", "NEGRO": "Negro", "BLANCO": "Blanco", "GRIS": "Gris",
    "AZUL": "Azul", "ROJO": "Rojo", "VERDE": "Verde", "AMARILLO": "Amarillo",
    "NARANJA": "Naranja", "NARANJO": "Naranja",
}

def inferir_color(nombre: str, color_actual: str = "") -> str:
    """Si no hay color detectado, lo infiere del nombre del producto."""
    if color_actual:
        return color_actual
    n = nombre.upper()
    for kw, color in COLOR_KEYWORDS.items():
        if kw in n.split():
            return color
    for kw, color in COLOR_KEYWORDS.items():
        if kw in n:
            return color
    return "Negro"  # Default seguro para EPP

def construir_variaciones(prod: dict, picture_id: str = None) -> list:
    """Construye todas las variaciones de un producto (una por talla/color).
    Deduplica (talla, color) sumando stocks; ML rechaza combos duplicados."""
    pid = picture_id or FOTO_ID
    es_calzado = es_calzado_real(prod.get("nombre_base", ""))
    color_inferido = inferir_color(prod.get("nombre_base", ""))

    # Agrupar por (talla, color) y sumar stock
    grupos = {}
    for v in prod["variantes"]:
        if v.get("stock_publicar", 0) <= 0:
            continue
        talla = str(v["talla"]) if v.get("talla") else None
        if es_calzado and (not talla or talla not in SIZE_GRID_ROWS):
            continue
        color_v = v.get("color") or color_inferido
        key = (talla or "", color_v)
        if key not in grupos:
            grupos[key] = {"stock": 0, "precios": [], "skus": []}
        grupos[key]["stock"]   += v.get("stock_publicar", 0)
        grupos[key]["precios"].append(v["precio_neto"])
        grupos[key]["skus"].append(v["sku"])

    variaciones = []
    for (talla, color_v), g in grupos.items():
        combos   = []
        var_attrs = []
        if talla:
            row_id = SIZE_GRID_ROWS.get(talla) if es_calzado else None
            combo_size = {"id": "SIZE", "value_name": talla}
            if es_calzado and row_id:
                combo_size["value_id"] = row_id.split(":")[-1]
            combos.append(combo_size)
            if es_calzado and row_id:
                var_attrs.append({"id": "SIZE_GRID_ROW_ID", "value_name": row_id})
        combos.append({"id": "COLOR", "value_name": color_v})
        if len(combos) == 1 and combos[0]["id"] == "COLOR":
            combos.insert(0, {"id": "SIZE", "value_name": "Único"})

        var = {
            "attribute_combinations": combos,
            "price":               precio_venta(min(g["precios"])),
            "available_quantity":  g["stock"],
            "seller_custom_field": g["skus"][0],
            "picture_ids":         [pid],
        }
        if var_attrs:
            var["attributes"] = var_attrs
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

    def upload_picture(self, image_path: Path) -> str:
        """Sube una imagen local a ML y devuelve el picture_id. None si falla."""
        if not image_path.exists():
            return None
        for i in range(MAX_REINTENTOS):
            with open(image_path, "rb") as f:
                files = {"file": (image_path.name, f, "image/png")}
                r = self.s.post(
                    f"{API_BASE}/pictures/items/upload",
                    headers={"Authorization": f"Bearer {self.access_token}"},
                    files=files,
                )
            if r.status_code == 401:
                self._renovar(); continue
            if r.status_code == 429:
                time.sleep(2**i); continue
            if r.ok:
                return r.json().get("id")
            log(f"  ! Upload imagen falló: {r.status_code} {r.text[:200]}")
            return None
        return None

# Cache de picture_ids por nombre de archivo (evita re-subir la misma imagen)
def cargar_picture_cache() -> dict:
    if PICTURE_CACHE_FILE.exists():
        with open(PICTURE_CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}

def guardar_picture_cache(cache: dict):
    PICTURE_CACHE_FILE.parent.mkdir(exist_ok=True)
    with open(PICTURE_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

def obtener_picture_id(prod: dict, ml: 'MLClient', cache: dict) -> str:
    """Devuelve el picture_id real del producto. Sube la imagen si no está cacheada.
    Si no hay imagen disponible, devuelve el placeholder FOTO_ID."""
    img_name = prod.get("imagen_principal", "")
    if not img_name:
        return FOTO_ID
    if img_name in cache:
        return cache[img_name]
    img_path = IMG_DIR / img_name
    pid = ml.upload_picture(img_path)
    if not pid:
        log(f"  ! Sin imagen, usando placeholder para {prod['nombre_base'][:40]}")
        return FOTO_ID
    cache[img_name] = pid
    guardar_picture_cache(cache)
    return pid

def cargar_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}

def guardar_state(state: dict):
    STATE_FILE.parent.mkdir(exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def publicar_nuevo(prod: dict, ml: MLClient, dry_run: bool, pic_cache: dict = None):
    """Publica 1 item ML con todas las tallas disponibles como variaciones."""
    tipo = detectar_tipo_producto(prod.get("nombre_base", ""))
    if tipo == "otro":
        log(f"  SKIP no clasificable como calzado/ropa")
        return "SKIP"
    if tipo == "ropa":
        log(f"  SKIP ropa pendiente (falta SIZE_GRID para categoría textil)")
        return "SKIP"

    # Subir imagen real (o usar placeholder)
    pid = FOTO_ID
    if not dry_run and pic_cache is not None:
        pid = obtener_picture_id(prod, ml, pic_cache)

    variaciones = construir_variaciones(prod, picture_id=pid)
    if not variaciones:
        log(f"  SKIP sin variantes válidas (tallas fuera del chart o sin stock)")
        return "SKIP"

    if tipo == "calzado":
        cat_ml = "MLC179382"
    else:
        cat_ml = detectar_categoria_ropa(prod["nombre_base"])

    precios_validos = [v["price"] for v in variaciones]
    if not precios_validos:
        return None
    precio_b  = min(precios_validos)
    stock_tot = sum(v["available_quantity"] for v in variaciones)

    payload = {
        "title":              limpiar_titulo(prod["nombre_base"]),
        "category_id":        cat_ml,
        "price":              precio_b,
        "currency_id":        "CLP",
        "available_quantity": stock_tot,
        "buying_mode":        "buy_it_now",
        "condition":          "new",
        "listing_type_id":    "gold_special",
        "pictures":           [{"id": pid}],
        "description":        {"plain_text": construir_descripcion(prod)},
        "attributes":         construir_atributos(prod),
        "variations":         variaciones,
        "shipping":           {"mode": "me2", "local_pick_up": False, "free_shipping": True},
        "sale_terms": [
            {"id": "WARRANTY_TYPE", "value_name": "Garantía del vendedor"},
            {"id": "WARRANTY_TIME", "value_name": "3 meses"},
        ],
    }

    if dry_run:
        tallas = [v["attribute_combinations"][0]["value_name"] for v in variaciones]
        log(f"  [DRY] {payload['title'][:50]} | ${precio_b:,} | {stock_tot} uds | tallas: {tallas}")
        return "DRY"

    r = ml.post("/items", payload)
    if r.status_code in (200, 201):
        item_id = r.json().get("id")
        log(f"  OK {payload['title'][:45]} -> {item_id} ({len(variaciones)} tallas)")
        return item_id
    log(f"  ERR {prod['nombre_base'][:40]}: {r.status_code} {r.text[:600]}")
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
    pic_cache = cargar_picture_cache()
    log(f"Picture cache: {len(pic_cache)} imágenes ya subidas")

    nuevos = act = skip = err = 0
    for i, prod in enumerate(prods):
        log(f"[{i+1}/{len(prods)}] {prod['nombre_base'][:50]}")
        tiene_stock = any(v.get("stock_publicar", 0) > 0 for v in prod["variantes"])
        ml_id = next((state[v["sku"]] for v in prod["variantes"] if v["sku"] in state), None)

        if ml_id:
            # Actualizar stock/precio de la primera variante publicada
            v0 = prod["variantes"][0]
            ok = actualizar_item(v0["sku"], ml_id, v0.get("stock_publicar", 0),
                                 v0["precio_neto"], ml, dry_run)
            act += 1 if ok else 0
            err += 0 if ok else 1
        elif not tiene_stock:
            log(f"  SKIP sin stock")
            skip += 1
        else:
            item_id = publicar_nuevo(prod, ml, dry_run, pic_cache)
            if item_id == "SKIP":
                skip += 1
            elif item_id == "DRY":
                nuevos += 1
            elif item_id:
                for v in prod["variantes"]:
                    state[v["sku"]] = item_id
                guardar_state(state)
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
