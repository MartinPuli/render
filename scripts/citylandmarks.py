"""
citylandmarks.py — registro de landmarks con GEOFENCING (modulo puro, sin bpy).

Un landmark solo se genera si el centro de la escena cae dentro de su geofence
(y, opcionalmente, si matchea nombre/tipo OSM). Evita las "reglas globales" que
disparaban landmarks falsos: p.ej. cualquier pasarela elevada NO es el Puente de
la Mujer — solo lo es en Puerto Madero. Asi Villa 31 no genera un puente falso.

Para agregar un landmark: sumar una entrada a LANDMARKS con su geofence y el
nombre de la funcion `builder` (definida en blender_build.py).
"""
import math

LANDMARKS = [
    {
        "key": "puente_de_la_mujer",
        "name": "Puente de la Mujer",
        "lat": -34.60840,
        "lon": -58.36380,
        "radius_m": 300.0,
        "builder": "add_puente_mujer",     # funcion en blender_build.py
        "match_names": ["puente de la mujer"],  # opcional: verificar nombre OSM
    },
]


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def landmarks_for_center(lat, lon):
    """Landmarks cuyo geofence contiene (lat, lon). Vacio si ninguno aplica."""
    out = []
    for lm in LANDMARKS:
        if haversine_m(lat, lon, lm["lat"], lm["lon"]) <= lm["radius_m"]:
            out.append(lm)
    return out


def name_matches(lm, osm_name):
    """True si el nombre OSM matchea el landmark (o si el landmark no exige nombre)."""
    wanted = [w.lower() for w in lm.get("match_names", [])]
    if not wanted:
        return True
    return bool(osm_name) and osm_name.strip().lower() in wanted
