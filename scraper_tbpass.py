"""
scraper_tbpass.py — Scraper de tbpass.cl (B2B VTEX) para productos HW.

Descubre productos HW por la marca, llama el GraphQL Product por cada slug,
extrae stock+precio reales por SKU y genera un JSON compatible con el
catalogo_vicsa para que sync_ml.py lo consuma.

Uso:
    $env:TBPASS_USER = "..."
    $env:TBPASS_PASS = "..."
    python scraper_tbpass.py
"""
import asyncio, json, os, re, sys, base64
from datetime import datetime
from pathlib import Path

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("pip install playwright && playwright install chromium"); sys.exit(1)

BASE      = "https://www.tbpass.cl"
GRAPHQL   = f"{BASE}/_v/segment/graphql/v1"
OUT_DIR   = Path("output_vicsa")
STORAGE   = OUT_DIR / "tbpass_storage.json"

# Persisted query hashes (sha256) capturados desde el browser
HASH_PRODUCT      = "52d9b0148a0bef719a2cc9426bcc76dc61fcd4c38dc3ce4e874e0c088aec7409"
HASH_PRODUCT_SEARCH = None  # Se actualiza en runtime al observar la red

def b64(obj): return base64.b64encode(json.dumps(obj).encode()).decode()

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

async def login_si_falta(ctx, page):
    """Si la sesión guardada no está válida, abre browser visible para login manual."""
    user = os.environ.get("TBPASS_USER", "").strip()
    pwd  = os.environ.get("TBPASS_PASS", "").strip()
    if not user or not pwd:
        log("ERROR: define TBPASS_USER y TBPASS_PASS")
        sys.exit(1)

    await page.goto(BASE, wait_until="domcontentloaded")
    await asyncio.sleep(2)
    # Probar login programático tipo VTEX ID
    # POST /api/vtexid/pub/authentication/start  → obtiene authentication_token
    # POST /api/vtexid/pub/authentication/classic/validate
    log("Login programático VTEX ID...")
    try:
        r = await page.request.post(f"{BASE}/api/vtexid/pub/authentication/start",
            data={"appStart": "true", "scope": "tecnobogab2b"})
        if not r.ok:
            log(f"VTEX start fail: {r.status}")
        else:
            d = await r.json()
            auth_tok = d.get("authenticationToken")
            if not auth_tok:
                log("Sin authenticationToken en respuesta")
                return False
            r2 = await page.request.post(
                f"{BASE}/api/vtexid/pub/authentication/classic/validate",
                form={"login": user, "password": pwd,
                      "authenticationToken": auth_tok})
            if r2.ok:
                d2 = await r2.json()
                if d2.get("authStatus") == "Success":
                    log("Login VTEX ID OK")
                    return True
                else:
                    log(f"Login VTEX failed: {d2}")
            else:
                log(f"VTEX validate fail: {r2.status}")
    except Exception as e:
        log(f"Login programático excepción: {e}")
    return False

async def listar_slugs_hw(page) -> list:
    """Busca productos HW probando varias URLs típicas de VTEX."""
    candidatos_url = [
        f"{BASE}/hardwork",
        f"{BASE}/Hardwork",
        f"{BASE}/marca/hardwork",
        f"{BASE}/?fq=B%3a2000006",  # filter por brandId 2000006 (HW)
        f"{BASE}/?_q=hw&map=ft",
        f"{BASE}/calzado-de-seguridad",
        f"{BASE}/botines",
    ]
    slugs = []
    for url in candidatos_url:
        log(f"Probando: {url}")
        await page.goto(url, wait_until="domcontentloaded")
        await asyncio.sleep(4)
        html = await page.content()
        # Patrón VTEX típico para tarjetas de producto
        for pat in [r'href="/([a-z0-9\-]+)/p"', r'linkText["\']:\s*["\']([a-z0-9\-]+)["\']']:
            nuevos = set(re.findall(pat, html, re.IGNORECASE)) - set(slugs)
            if nuevos:
                slugs.extend(sorted(nuevos))
                log(f"  {len(nuevos)} slugs NUEVOS (total: {len(slugs)})")
        # Dump 1 página para inspección si nada
        if not slugs:
            (OUT_DIR / f"tbpass_html_{url.split('/')[-1] or 'home'}.html").write_text(html[:200000], encoding="utf-8")
        # No seguir si ya tenemos varios (evita doble conteo)
        if len(slugs) > 30:
            break
    return slugs

async def query_product(page, slug, product_id=None):
    """Llama el endpoint GraphQL Product con persisted query."""
    variables = {
        "skipCategoryTree": True,
        "slug": slug,
        "identifier": {"field": "slug", "value": slug},
    }
    extensions = {
        "persistedQuery": {
            "version": 1,
            "sha256Hash": HASH_PRODUCT,
            "sender": "vtex.store-resources@0.x",
            "provider": "vtex.search-graphql@0.x",
        },
        "variables": b64(variables),
    }
    url = (f"{GRAPHQL}?workspace=master&maxAge=short&appsEtag=remove"
           f"&domain=store&locale=es-CL&operationName=Product"
           f"&variables=%7B%7D&extensions={b64(extensions)}".replace("+","%2B"))
    # Mejor armarlo limpio:
    from urllib.parse import quote
    url = (f"{GRAPHQL}?workspace=master&maxAge=short&appsEtag=remove"
           f"&domain=store&locale=es-CL&operationName=Product"
           f"&variables=%7B%7D&extensions={quote(json.dumps(extensions))}")
    r = await page.request.get(url)
    if not r.ok:
        return None
    return await r.json()

def extraer_variantes(prod_data: dict) -> dict:
    """Convierte la respuesta GraphQL Product a estructura nuestra."""
    p = prod_data.get("data", {}).get("product")
    if not p:
        return None
    nombre = p["productName"]
    items = p.get("items", [])
    variantes = []
    for it in items:
        sellers = it.get("sellers", [])
        if not sellers: continue
        co = sellers[0].get("commertialOffer", {})
        # Talla viene en variations o en name
        talla = it.get("name", "")
        for v in it.get("variations", []) or []:
            if v.get("name", "").lower() in ["talla", "tallas", "size"]:
                vals = v.get("values", [])
                if vals: talla = vals[0]
        # SKU = referenceId con Key=RefId
        sku = ""
        for r in it.get("referenceId", []) or []:
            if r.get("Key") == "RefId":
                sku = r.get("Value", "")
        if not sku: sku = it.get("itemId", "")
        stock = co.get("AvailableQuantity", 0)
        precio = co.get("Price", 0)
        variantes.append({
            "sku": sku,
            "talla": str(talla),
            "color": "",  # tbpass no lo expone como variation aquí
            "precio_neto": precio,
            "stock_vicsa": stock,
            "stock_publicar": min(max(1, stock // 2), 10) if stock > 0 else 0,
            "imagen": "",
            "url": f"{BASE}/{p.get('linkText','')}/p",
            "actualizado": datetime.now().isoformat(),
        })
    return {
        "nombre_base":    nombre,
        "categoria":      "Calzado de Seguridad",  # ajustable
        "marca":          p.get("brand", ""),
        "especificaciones": p.get("description","")[:1000],
        "ficha_tecnica_url": "",
        "certificaciones":   "",
        "imagen_principal":  "",
        "variantes":        variantes,
    }

async def main():
    OUT_DIR.mkdir(exist_ok=True)
    headless = os.environ.get("TBPASS_HEADLESS", "true").lower() != "false"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        ctx_kwargs = {"locale": "es-CL", "viewport": {"width":1280,"height":900}}
        if STORAGE.exists():
            ctx_kwargs["storage_state"] = str(STORAGE)
            log("Usando storage_state previo")
        ctx = await browser.new_context(**ctx_kwargs)
        page = await ctx.new_page()

        # Verificar sesión
        await page.goto(BASE, wait_until="domcontentloaded")
        await asyncio.sleep(2)
        html = await page.content()
        logged_in = "Cerrar sesión" in html or "Mi cuenta" in html or "logout" in html.lower()
        if not logged_in:
            log("Sin sesión válida, intentando login programático...")
            ok = await login_si_falta(ctx, page)
            if not ok:
                if headless:
                    log("ERROR: login programático falló y estamos en headless (CI).")
                    log("Para debug local: $env:TBPASS_HEADLESS='false'")
                    await browser.close()
                    sys.exit(1)
                log("\n" + "="*60)
                log("Logueate MANUALMENTE en el browser. 60s.")
                log("="*60)
                for i in range(60, 0, -10):
                    log(f"  {i}s..."); await asyncio.sleep(10)
            await ctx.storage_state(path=str(STORAGE))
            log("Storage_state guardado")

        # 1) Listar slugs HW
        slugs = await listar_slugs_hw(page)
        log(f"\nTotal slugs HW: {len(slugs)}")
        (OUT_DIR / "tbpass_slugs.json").write_text(
            json.dumps(slugs, indent=2), encoding="utf-8")

        # 2) Obtener detalle de cada producto
        productos = []
        for i, slug in enumerate(slugs):
            log(f"[{i+1}/{len(slugs)}] {slug}")
            try:
                data = await query_product(page, slug)
                prod = extraer_variantes(data) if data else None
                if prod and prod["variantes"]:
                    productos.append(prod)
                    n_stock = sum(1 for v in prod["variantes"] if v["stock_publicar"] > 0)
                    log(f"  OK {prod['nombre_base'][:50]}: {len(prod['variantes'])} vars, {n_stock} con stock")
                else:
                    log(f"  -- sin items o producto no extraíble")
            except Exception as e:
                log(f"  ERR: {e}")
            await asyncio.sleep(0.5)

        # 3) Guardar JSON
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        out = OUT_DIR / f"catalogo_tbpass_{ts}.json"
        out.write_text(json.dumps({
            "generado":         datetime.now().isoformat(),
            "fuente":           "tbpass.cl",
            "total_productos":  len(productos),
            "productos":        productos,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"\nGuardado: {out}")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
