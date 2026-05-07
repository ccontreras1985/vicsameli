"""
aplicar_promo.py — Aplica el descuento de 18% en los items de la campaña
de arranque de reputación. Guarda backup para poder revertir.

Uso:
    python aplicar_promo.py            # aplica el descuento
    python aplicar_promo.py --revertir # restaura los precios desde el backup
"""
import os, sys, json, time, argparse
from pathlib import Path

try:
    import requests
except ImportError:
    print("pip install requests"); sys.exit(1)

sys.path.insert(0, ".")
from sync_ml import MLClient, log

API = "https://api.mercadolibre.com"
BACKUP_FILE = Path("output_vicsa/promo_backup.json")

# Items y descuento de la campaña actual
PROMO = {
    "MLC3919835408": {"nombre": "Calzado Mack New Chicago",        "factor": 0.82},
    "MLC3919796482": {"nombre": "Calzado Mack New Denver",         "factor": 0.82},
    "MLC3919809676": {"nombre": "Calzado Antiperforante Quebec",   "factor": 0.82},
    "MLC3919835412": {"nombre": "Calzado Mack New Denver Pro",     "factor": 0.82},
}

def get_item(ml, item_id):
    r = ml.get(f"/items/{item_id}")
    if r.status_code == 401:
        ml._renovar()
        r = ml.get(f"/items/{item_id}")
    if not r.ok:
        log(f"  ERR GET {item_id}: {r.status_code} {r.text[:200]}")
        return None
    return r.json()

def aplicar_descuento(ml):
    backup = {}
    if BACKUP_FILE.exists():
        backup = json.load(BACKUP_FILE.open(encoding="utf-8"))

    log(f"=== APLICANDO PROMO DE 18% A {len(PROMO)} ITEMS ===")
    ok = err = 0

    for item_id, info in PROMO.items():
        log(f"\n[{info['nombre']}] {item_id}")
        if item_id in backup:
            log(f"  -- ya está en backup, salto (re-aplicar requiere --revertir primero)")
            continue

        item = get_item(ml, item_id)
        if not item: err += 1; continue

        variaciones = item.get("variations", [])
        if not variaciones:
            # Item sin variaciones — actualizar precio a nivel item
            precio_orig = item.get("price", 0)
            precio_nuevo = round(precio_orig * info["factor"])
            payload = {"price": precio_nuevo}
            backup[item_id] = {
                "nombre":      info["nombre"],
                "price_orig":  precio_orig,
                "price_promo": precio_nuevo,
                "variations":  [],
                "factor":      info["factor"],
            }
            log(f"  Sin variaciones: ${precio_orig:,} → ${precio_nuevo:,}")
        else:
            var_updates = []
            backup_vars = []
            for v in variaciones:
                vid = v["id"]
                p_orig = v.get("price", 0)
                p_new  = round(p_orig * info["factor"])
                var_updates.append({"id": vid, "price": p_new})
                backup_vars.append({"id": vid, "price_orig": p_orig, "price_promo": p_new})
            payload = {"variations": var_updates}
            backup[item_id] = {
                "nombre":      info["nombre"],
                "price_orig":  item.get("price", 0),
                "price_promo": min(u["price"] for u in var_updates),
                "variations":  backup_vars,
                "factor":      info["factor"],
            }
            log(f"  {len(var_updates)} variaciones: ${item.get('price',0):,} → ${min(u['price'] for u in var_updates):,}")

        r = ml.put(f"/items/{item_id}", payload)
        if r.status_code == 401:
            ml._renovar()
            r = ml.put(f"/items/{item_id}", payload)
        if r.ok:
            ok += 1
            log(f"  OK aplicado.  https://articulo.mercadolibre.cl/MLC-{item_id[3:]}-_JM")
        else:
            err += 1
            log(f"  ERR PUT: {r.status_code} {r.text[:300]}")
            backup.pop(item_id, None)
        time.sleep(0.6)

    BACKUP_FILE.parent.mkdir(exist_ok=True)
    with open(BACKUP_FILE, "w", encoding="utf-8") as f:
        json.dump(backup, f, ensure_ascii=False, indent=2)
    log(f"\nResumen: {ok} aplicados, {err} errores. Backup en {BACKUP_FILE}")

def revertir(ml):
    if not BACKUP_FILE.exists():
        log("No hay backup."); return
    backup = json.load(BACKUP_FILE.open(encoding="utf-8"))
    log(f"=== REVIRTIENDO {len(backup)} ITEMS A PRECIO ORIGINAL ===")
    ok = err = 0
    for item_id, b in list(backup.items()):
        log(f"\n[{b['nombre']}] {item_id}")
        if b["variations"]:
            payload = {"variations": [{"id": v["id"], "price": v["price_orig"]}
                                       for v in b["variations"]]}
        else:
            payload = {"price": b["price_orig"]}
        r = ml.put(f"/items/{item_id}", payload)
        if r.status_code == 401:
            ml._renovar()
            r = ml.put(f"/items/{item_id}", payload)
        if r.ok:
            ok += 1
            log(f"  OK revertido a ${b['price_orig']:,}")
            backup.pop(item_id)
        else:
            err += 1
            log(f"  ERR: {r.status_code} {r.text[:200]}")
        time.sleep(0.6)

    if backup:
        with open(BACKUP_FILE, "w", encoding="utf-8") as f:
            json.dump(backup, f, ensure_ascii=False, indent=2)
    else:
        BACKUP_FILE.unlink()
    log(f"\nResumen: {ok} revertidos, {err} errores.")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--revertir", action="store_true")
    args = ap.parse_args()
    ml = MLClient()
    if args.revertir:
        revertir(ml)
    else:
        aplicar_descuento(ml)

if __name__ == "__main__":
    main()
