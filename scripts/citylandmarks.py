"""Registro OPCIONAL de landmarks externos (modulo puro, sin bpy).

El core no contiene ciudades, nombres ni coordenadas concretas. Los landmarks
normales se extraen de tags OSM en ``place_to_3d.parse_special_features``. Esta
API queda para packs privados/opcionales que el caller pase explicitamente.
"""
import math

# Deliberadamente vacio: no agregar lugares concretos al core.
LANDMARKS = []


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def landmarks_for_center(lat, lon, landmarks=None):
    """Filtra un pack explicito por geofence. Sin pack devuelve vacio."""
    out = []
    for lm in (LANDMARKS if landmarks is None else landmarks):
        if haversine_m(lat, lon, lm["lat"], lm["lon"]) <= lm["radius_m"]:
            out.append(lm)
    return out


def name_matches(lm, osm_name):
    """True si el nombre OSM matchea el landmark (o si el landmark no exige nombre)."""
    wanted = [w.lower() for w in lm.get("match_names", [])]
    if not wanted:
        return True
    return bool(osm_name) and osm_name.strip().lower() in wanted
