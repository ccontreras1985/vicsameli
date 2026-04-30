"""
actualizar_imagenes.py — Sube las imágenes reales de Vicsa y las asocia a las
publicaciones ML existentes (las que se publicaron antes con foto placeholder).

Uso:
    python actualizar_imagenes.py --json output_vicsa/catalogo_vicsa_20260429_1247.json
"""
import argparse, json, os, sys, time
from pathlib import Path

try:
    import requests
    from PIL import Image
    import io
except ImportError:
    print("pip install requests Pillow"); sys.exit(1)

API     = "https://api.mercadolibre.com"
APP_ID  = os.environ.get("ML_APP_ID", "").strip()
SECRET  = os.environ.get("ML_CLIENT_SECRET", "").strip()
REFRESH = os.environ.get("ML_REFRESH_TOKEN", "").strip()
TOKEN   = os.environ.get("ML_ACCESS_TOKEN", "").strip()

IMG_DIR    = Path("output_vicsa/imagenes")
STATE_FILE = Path("output_vicsa/ml_state.json")
CACHE_FILE = Path("output_vicsa/ml_pictures_cache.json")

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

def headers_json():
    return {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

def headers_auth():
    return {"Authorization": f"Bearer {TOKEN}"}

def preparar_imagen(path: Path, min_size: int = 600) -> bytes:
    """Carga imagen, fuerza fondo blanco y la escala a min_size×min_size si es chica."""
    img = Image.open(path).convert("RGBA")
    w, h = img.size
    # Si tiene transparencia, aplanar sobre fondo blanco
    bg = Image.new("RGB", (w, h), (255, 255, 255))
    bg.paste(img, mask=img.split()[3] if img.mode == "RGBA" else None)
    img = bg
    # Escalar manteniendo aspecto si es chica
    if min(w, h) < min_size:
        ratio = min_size / min(w, h)
        nw, nh = int(w * ratio), int(h * ratio)
        img = img.resize((nw, nh), Image.LANCZOS)
    # Padding cuadrado a fondo blanco para asegurar lados >= min_size
    target = max(img.size[0], img.size[1], min_size)
    canvas = Image.new("RGB", (target, target), (255, 255, 255))
    canvas.paste(img, ((target - img.size[0]) // 2, (target - img.size[1]) // 2))
    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=92)
    buf.seek(0)
    return buf.getvalue()

def upload(path: Path) -> str:
    if not path.exists(): return None
    try:
        data = preparar_imagen(path)
    except Exception as e:
        print(f"  ! Error procesando {path.name}: {e}")
        return None
    name = path.stem + ".jpg"
    files = {"file": (name, data, "image/jpeg")}
    r = requests.post(f"{API}/pictures/items/upload",
        headers=headers_auth(), files=files)
    if r.status_code == 401:
        renovar_token()
        r = requests.post(f"{API}/pictures/items/upload",
            headers=headers_auth(), files=files)
    if r.ok:
        return r.json().get("id")
    print(f"  ! Upload falló: {r.status_code} {r.text[:200]}")
    return None

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", required=True, type=Path)
    args = parser.parse_args()

    if not all([APP_ID, SECRET, REFRESH, TOKEN]):
        print("ERROR: Variables ML_* no definidas"); sys.exit(1)

    renovar_token()  # arranca con token fresco

    state = json.load(open(STATE_FILE, encoding="utf-8"))
    catalogo = json.load(open(args.json, encoding="utf-8"))
    cache = json.load(open(CACHE_FILE, encoding="utf-8")) if CACHE_FILE.exists() else {}

    # Mapa SKU -> producto
    sku_a_prod = {}
    for p in catalogo["productos"]:
        for v in p["variantes"]:
            sku_a_prod[v["sku"]] = p

    # Dedup por item_id: para cada item_id, primer producto encontrado
    item_a_prod = {}
    for sku, item_id in state.items():
        if item_id in item_a_prod: continue
        prod = sku_a_prod.get(sku)
        if prod: item_a_prod[item_id] = prod

    print(f"Items a actualizar con imagen real: {len(item_a_prod)}")
    ok = err = sin_img = 0

    for item_id, prod in item_a_prod.items():
        nombre = prod.get("nombre_base", "")[:50]
        img_name = prod.get("imagen_principal", "")

        if not img_name:
            print(f"  SIN_IMG  {item_id}  {nombre}")
            sin_img += 1
            continue

        # Subir imagen (o reusar del cache)
        if img_name in cache:
            pid = cache[img_name]
        else:
            pid = upload(IMG_DIR / img_name)
            if not pid:
                err += 1
                continue
            cache[img_name] = pid
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)

        # GET el item para obtener los IDs de sus variaciones
        gr = requests.get(f"{API}/items/{item_id}", headers=headers_auth())
        if gr.status_code == 401:
            renovar_token()
            gr = requests.get(f"{API}/items/{item_id}", headers=headers_auth())
        if not gr.ok:
            print(f"  ERR GET  {item_id}: {gr.status_code}"); err += 1; continue
        var_ids = [v["id"] for v in gr.json().get("variations", [])]

        # PUT con pictures + picture_ids en cada variación (ML lo exige)
        payload = {"pictures": [{"id": pid}]}
        if var_ids:
            payload["variations"] = [{"id": vid, "picture_ids": [pid]} for vid in var_ids]
        r = requests.put(f"{API}/items/{item_id}",
            headers=headers_json(), json=payload)
        if r.status_code == 401:
            renovar_token()
            r = requests.put(f"{API}/items/{item_id}",
                headers=headers_json(), json=payload)
        if r.ok:
            ok += 1
            print(f"  OK       {item_id}  {nombre} -> {pid}")
        else:
            err += 1
            print(f"  ERR      {item_id}: {r.status_code} {r.text[:250]}")
        time.sleep(0.4)

    print(f"\nResumen: {ok} actualizados, {err} errores, {sin_img} sin imagen.")

if __name__ == "__main__":
    main()
