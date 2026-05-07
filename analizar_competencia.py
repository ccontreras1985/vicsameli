"""
analizar_competencia.py — Compara tus precios vs los 5 más vendidos en ML para
los modelos de tu top 10 visitas. Sin esto no hay forma de saber si estás caro.
"""
import os, sys, json, requests
from urllib.parse import quote

API = "https://api.mercadolibre.com"
TOKEN = os.environ.get("ML_ACCESS_TOKEN", "").strip()
H = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}

# Top 10 publicaciones tuyas con tráfico (sacar del reporte de rendimiento)
MIS = [
    ("Calzado Seguridad Hw Lunafoam",            11),
    ("Bota Tecnica Hw Bering Hiker Thinsulate",   7),
    ("Calzado Mack New Chicago",                  6),
    ("Calzado Mack New Denver",                   4),
    ("Calzado Hw Magnus",                          2),
    ("Bering Low Red",                             2),
    ("Calzado Quebec Pro Hamilton",                0),
    ("Calzado Quebec Apollo",                      0),
    ("Alaska King",                                0),
    ("Lunafoam",                                  11),
]

def buscar(q, n=5):
    r = requests.get(f"{API}/sites/MLC/search",
        params={"q": q, "limit": n, "sort": "sold_quantity_desc"})
    if not r.ok: return []
    return r.json().get("results", [])

print(f"{'Modelo (mi publicación)':<45} {'Mi visitas':>10}")
print(f"{'  Competidor (más vendido en ML)':<60} {'Precio':>10} {'Vendidos':>10}")
print("="*90)

for nombre, visitas in MIS:
    print(f"\n{nombre:<45} {visitas:>10}")
    items = buscar(nombre)
    if not items:
        print("  (sin resultados)")
        continue
    for it in items[:3]:
        title = it.get("title", "")[:55]
        price = it.get("price", 0)
        sold  = it.get("sold_quantity", 0)
        print(f"  {title:<60} ${price:>7,} {sold:>10}")
