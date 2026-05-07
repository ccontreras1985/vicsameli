"""
enriquecer_items.py — Re-enriquece todos los items ya publicados en ML con:
  - Título optimizado para SEO (keywords + diferenciador)
  - Descripción rica estructurada
  - Garantía 12 meses
  - Sale terms completos

Uso:
    python enriquecer_items.py --json output_vicsa/catalogo_vicsa_YYYYMMDD_HHMM.json
"""
import os, sys, json, time, argparse
from pathlib import Path

try:
    import requests
except ImportError:
    print("pip install requests"); sys.exit(1)

# Importar helpers de sync_ml
sys.path.insert(0, ".")
from sync_ml import (construir_titulo_seo, construir_descripcion,
                     construir_descripcion_html, STATE_FILE, MLClient, log)

API = "https://api.mercadolibre.com"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True, type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.json.exists():
        print(f"ERROR: no existe {args.json}"); sys.exit(1)

    if not STATE_FILE.exists():
        print(f"ERROR: no existe {STATE_FILE}"); sys.exit(1)

    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    log(f"State: {len(state)} SKUs en {len(set(state.values()))} items ML")

    catalog = json.loads(args.json.read_text(encoding="utf-8"))

    # Mapear cada item_id ML → producto (primer producto cuyo SKU está en state apuntando ahí)
    # Luego para cada producto único, hacemos UN PUT al item con título y descripción.
    item_to_prod = {}
    for prod in catalog["productos"]:
        for v in prod["variantes"]:
            if v["sku"] in state:
                iid = state[v["sku"]]
                if iid not in item_to_prod:
                    item_to_prod[iid] = prod

    log(f"Items a enriquecer: {len(item_to_prod)}")

    ml = MLClient()
    ok = err = 0

    titulo_locked = sale_locked = desc_ok = 0
    for i, (item_id, prod) in enumerate(item_to_prod.items(), 1):
        nuevo_titulo = construir_titulo_seo(prod)
        desc_plain   = construir_descripcion(prod)
        desc_html    = construir_descripcion_html(prod)

        log(f"[{i}/{len(item_to_prod)}] {item_id}  {nuevo_titulo[:55]}")

        if args.dry_run:
            log(f"  [DRY] titulo={len(nuevo_titulo)}c, plain={len(desc_plain)}c, html={len(desc_html)}c")
            ok += 1
            continue

        algo_ok = False

        # 1) Intentar título solo
        r = ml.put(f"/items/{item_id}", {"title": nuevo_titulo})
        if r.ok:
            algo_ok = True
        elif "not_modifiable" in r.text:
            titulo_locked += 1
            log(f"  -- título bloqueado por ML (item con visitas/antigüedad)")
        else:
            log(f"  ERR titulo: {r.status_code} {r.text[:200]}")

        # 2) Intentar sale_terms solo
        sale_payload = {"sale_terms": [
            {"id": "WARRANTY_TYPE", "value_name": "Garantía del vendedor"},
            {"id": "WARRANTY_TIME", "value_name": "12 meses"},
        ]}
        r = ml.put(f"/items/{item_id}", sale_payload)
        if r.ok:
            algo_ok = True
        elif "not_modifiable" in r.text or "field_not_updatable" in r.text:
            sale_locked += 1
        else:
            log(f"  ERR sale_terms: {r.status_code} {r.text[:200]}")

        # 3) Descripción (con manejo de 429 + fallback HTML)
        def put_desc(body):
            return ml.s.put(f"{API}/items/{item_id}/description",
                headers={"Authorization": f"Bearer {ml.access_token}",
                         "Content-Type": "application/json"},
                json=body)
        for attempt in range(3):
            r = put_desc({"plain_text": desc_plain})
            if r.status_code == 401:
                ml._renovar(); continue
            if r.status_code == 429:
                time.sleep(2 + attempt * 2); continue
            break
        if not r.ok and "PLAIN_TEXT_NOT_ALLOWED" in r.text:
            for attempt in range(3):
                r = put_desc({"text": desc_html})
                if r.status_code == 429:
                    time.sleep(2 + attempt * 2); continue
                break
        if r.ok:
            desc_ok += 1
            algo_ok = True
        else:
            log(f"  ERR descripcion: {r.status_code} {r.text[:200]}")

        if algo_ok:
            ok += 1
        else:
            err += 1
        time.sleep(0.8)  # rate limit menos agresivo

    log(f"\n  Items con título bloqueado por ML: {titulo_locked}")
    log(f"  Items con sale_terms bloqueado:     {sale_locked}")
    log(f"  Descripciones actualizadas OK:      {desc_ok}")

    log(f"\nResumen: {ok} enriquecidos, {err} errores.")

if __name__ == "__main__":
    main()
