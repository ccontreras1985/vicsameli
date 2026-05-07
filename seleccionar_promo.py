"""
seleccionar_promo.py — Elige los 5 productos óptimos para campaña de
"primeras ventas a costo" usando criterios objetivos sin necesidad de ventas propias.

Score = 50*visitas_norm + 25*stock_norm + 15*tallas_norm + 10*precio_norm
con bonificación por diversidad de tipo (calzado/bota/parka/etc).
"""
import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from pathlib import Path

CATALOGO = "output_vicsa/catalogo_vicsa_20260429_1247.json"
STATE    = "output_vicsa/ml_state.json"

# Items que NO queremos incluir en la promoción (motivos puntuales del usuario)
EXCLUIR = {
    "MLC3919822844",  # Lunafoam — usuario no puede verificar stock fuera de tbpass.cl
}

# Visitas del reporte 30/04 - 07/05 (parcheado a mano del XLSX, con prefijo MLC)
VISITAS = {
    "MLC3919822844": 11,  # Lunafoam
    "MLC3919809714": 7,   # Bering Hiker Negra
    "MLC3919835408": 6,   # Mack New Chicago
    "MLC3919796482": 4,   # Mack New Denver
    "MLC3919809742": 2,   # Magnus
    "MLC3919796494": 2,   # Bering Low Red
    "MLC3940402746": 2,   # Parka Siberia Certificada
    "MLC1949402187": 2,   # Parka Skorpio 3 en 1
    "MLC3919835434": 1,   # Calzado HW Goliat
    "MLC3919809736": 1,   # Bering Low Aqua
    "MLC3940402756": 1,   # Parka Siberia Recycled
    "MLC1949415111": 1,   # Parka Siberia
    "MLC3919835412": 1,   # Mack New Denver Pro
    "MLC1949402167": 1,   # Parka Kodiak
    "MLC3919822850": 1,   # Melbourne Camel
    "MLC1949402147": 1,   # Parka Ottawa
    "MLC3919835428": 1,   # Bering High Navy
    "MLC3919835442": 1,   # Quebec Terrain
    "MLC3919809676": 1,   # Antiperforante
}

state    = json.load(open(STATE, encoding="utf-8"))
catalog  = json.load(open(CATALOGO, encoding="utf-8"))

# Mapear cada item_id ML → producto (uno por item)
item_a_prod = {}
for prod in catalog["productos"]:
    for v in prod["variantes"]:
        if v["sku"] in state:
            iid = state[v["sku"]]
            if iid not in item_a_prod:
                item_a_prod[iid] = prod

# Calcular métricas crudas
filas = []
for item_id, prod in item_a_prod.items():
    visitas = VISITAS.get(item_id, 0)
    stock_total = sum(v.get("stock_publicar", 0) for v in prod["variantes"])
    tallas_stock = sum(1 for v in prod["variantes"] if v.get("stock_publicar", 0) > 0)
    precios = [v["precio_neto"] for v in prod["variantes"] if v["precio_neto"] > 0]
    if not precios: continue
    precio_costo = min(precios)
    precio_venta = round(precio_costo * 1.60)
    nombre = prod["nombre_base"]
    n_up = nombre.upper()
    if any(k in n_up for k in ["CALZADO","ZAPATO","BOTIN"]):
        tipo = "calzado"
    elif "BOTA" in n_up:
        tipo = "bota"
    elif "PARKA" in n_up:
        tipo = "parka"
    elif "POLERA" in n_up:
        tipo = "polera"
    elif "PANTALON" in n_up or "JEAN" in n_up:
        tipo = "pantalon"
    elif "CHALECO" in n_up:
        tipo = "chaleco"
    else:
        tipo = "otro"
    filas.append({
        "item_id": item_id, "nombre": nombre, "tipo": tipo,
        "visitas": visitas, "stock_total": stock_total,
        "tallas_stock": tallas_stock, "precio_venta": precio_venta,
        "precio_costo": precio_costo,
    })

# Normalizar 0-1 cada métrica
def norm(vals):
    if not vals: return [0]*len(vals)
    mx = max(vals); mn = min(vals)
    if mx == mn: return [1.0]*len(vals)
    return [(v - mn) / (mx - mn) for v in vals]

visitas_n = norm([f["visitas"] for f in filas])
stock_n   = norm([f["stock_total"] for f in filas])
tallas_n  = norm([f["tallas_stock"] for f in filas])
# Precio: invertido (más barato = mejor) y solo bonifica entre $15K-$60K
def precio_score(p):
    if p < 15000: return 0.5  # muy barato, low margin abs
    if p > 80000: return 0.2  # muy caro, baja conversión
    if 20000 <= p <= 50000: return 1.0  # sweet spot
    return 0.7
precio_n = [precio_score(f["precio_venta"]) for f in filas]

for i, f in enumerate(filas):
    f["score"] = (50 * visitas_n[i] +
                  25 * stock_n[i] +
                  15 * tallas_n[i] +
                  10 * precio_n[i])

filas.sort(key=lambda f: -f["score"])

# Marcas que se gestionan fuera de Vicsa (stock no validable acá)
MARCAS_EXCLUIDAS = {"HW", "HARDWORK"}

def es_marca_excluida(nombre):
    n = nombre.upper()
    # match por palabra completa para no atrapar "BR" en "BROWN" etc.
    palabras = set(n.replace(",", " ").split())
    return any(m in palabras for m in MARCAS_EXCLUIDAS)

# FILTRO CRÍTICO: solo items con tráfico orgánico real + marcas verificables en Vicsa
con_trafico = [f for f in filas
               if f["visitas"] > 0 and f["stock_total"] >= 5
               and f["item_id"] not in EXCLUIR
               and not es_marca_excluida(f["nombre"])]
con_trafico.sort(key=lambda f: -f["score"])

# Selección con diversidad
elegidos = []
contador_tipo = {}
for f in con_trafico:
    if len(elegidos) >= 5: break
    if contador_tipo.get(f["tipo"], 0) >= 2 and f["tipo"] != "calzado":
        if contador_tipo.get(f["tipo"], 0) >= 3:
            continue
    elegidos.append(f)
    contador_tipo[f["tipo"]] = contador_tipo.get(f["tipo"], 0) + 1

print(f"\nUNIVERSO: {len(filas)} items publicados | Con tráfico (>=1 visita): {len(con_trafico)}")
print(f"\nTOP {len(elegidos)} candidatos PARA promoción de arranque de reputación:")
print("=" * 110)
for i, f in enumerate(elegidos, 1):
    # Descuento 18%: suficiente para subir en ranking ML y ofrecer ahorro real,
    # pero no destruye margen. Margen 60% → ~31% post-descuento (aún positivo
    # después de comisión 5.5% + envío ~7%).
    precio_promo = round(f["precio_venta"] * 0.82)
    descuento_pct = 18
    sacrificio = f["precio_venta"] - precio_promo
    margen_pre  = (f["precio_venta"] - f["precio_costo"]) / f["precio_costo"] * 100
    margen_post = (precio_promo - f["precio_costo"]) / f["precio_costo"] * 100
    print(f"\n{i}. {f['nombre'][:60]}")
    print(f"   item_id: {f['item_id']}   tipo: {f['tipo']}   score: {f['score']:.1f}")
    print(f"   Visitas (7d): {f['visitas']}  |  Stock: {f['stock_total']} ({f['tallas_stock']} tallas)")
    print(f"   Precio actual: ${f['precio_venta']:,}  (margen sobre costo: {margen_pre:.0f}%)")
    print(f"   Precio promo:  ${precio_promo:,}  (-{descuento_pct}%, margen sobre costo: {margen_post:.0f}%)")
    print(f"   Sacrificio por venta: ${sacrificio:,}")

if elegidos:
    total_sac_5u = sum((f["precio_venta"] - round(f["precio_venta"] * 0.82)) for f in elegidos)
    print(f"\n" + "=" * 110)
    print(f"PLAN: descuento 18% por 14 días")
    print(f"  Meta realista: 1 venta por item -> {len(elegidos)} reseñas")
    print(f"  Sacrificio total si se venden TODOS: ${total_sac_5u:,.0f}")
    print(f"  ROI: 5 reseñas + ranking ML mejorado -> conversión orgánica +200-300%")
