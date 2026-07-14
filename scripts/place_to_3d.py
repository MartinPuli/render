#!/usr/bin/env python3
"""
place_to_3d.py  —  De un lugar de Google Maps a un modelo 3D en Blender.

Pipeline:
  1. Resuelve el input (link de Google Maps / coordenadas / nombre) a lat,lng.
  2. Baja la geometria de la zona desde OpenStreetMap (Overpass API, gratis, sin key):
     edificios (con alturas), calles, agua, parques/verde.
  3. Opcional: baja imagenes de Street View + fotos del lugar (Google, necesita API key)
     para que Claude "vea" el lugar real y ajuste los colores.
  4. Proyecta todo a metros y genera un scene.json.
  5. Llama a blender_build.py (Blender) que construye el 3D con colores y renderiza un PNG.

Uso:
  python3 scripts/place_to_3d.py "<link de Maps | lat,lng | nombre>" [opciones]

Opciones:
  --radius M       Radio de la zona a reconstruir, en metros (default 250)
  --out DIR        Carpeta de salida (default output/<slug>)
  --no-streetview  No bajar imagenes de Google (util si no tenes API key)
  --no-render      Solo bajar datos y generar scene.json, sin abrir Blender
  --blender PATH   Ruta al binario de Blender (si no, usa el modulo python 'bpy')

Env:
  GOOGLE_MAPS_API_KEY   API key de Google Maps Platform (Street View Static + Places + Geocoding)
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("Falta 'requests'. Instalalo con: python3 -m pip install requests")

HERE = Path(__file__).resolve().parent
BLENDER_BUILD = HERE / "blender_build.py"

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.osm.jp/api/interpreter",
]
USER_AGENT = "maps-to-3d-blender-skill/1.0 (OSM data; educational)"

# ---------------------------------------------------------------------------
# 1) Resolver el lugar -> (lat, lon, etiqueta)
# ---------------------------------------------------------------------------

COORD_RE = re.compile(r"(-?\d{1,3}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)")


def _valid(lat, lon):
    return -90 <= lat <= 90 and -180 <= lon <= 180


def _extract_coords_from_url(url):
    """Devuelve (lat, lon) del texto de una URL de Google Maps, o None."""
    # El pin real del lugar: !3d<lat>!4d<lon>  (mas preciso que @)
    m = re.search(r"!3d(-?\d{1,3}\.\d+)!4d(-?\d{1,3}\.\d+)", url)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if _valid(lat, lon):
            return lat, lon
    # Centro del viewport: @lat,lon,zoom
    m = re.search(r"@(-?\d{1,3}\.\d+),(-?\d{1,3}\.\d+)", url)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if _valid(lat, lon):
            return lat, lon
    # Parametros query: q= / ll= / query= / center=
    for key in ("q", "query", "ll", "center", "sll", "daddr"):
        m = re.search(rf"[?&]{key}=(-?\d{{1,3}}\.\d+)[,%2C]+(-?\d{{1,3}}\.\d+)", url)
        if m:
            lat, lon = float(m.group(1)), float(m.group(2))
            if _valid(lat, lon):
                return lat, lon
    return None


def _geocode(query, api_key):
    """Ultimo recurso: geocodificar un nombre con la API de Google (necesita key)."""
    r = requests.get(
        "https://maps.googleapis.com/maps/api/geocode/json",
        params={"address": query, "key": api_key},
        timeout=30,
    )
    data = r.json()
    if data.get("status") == "OK" and data.get("results"):
        loc = data["results"][0]["geometry"]["location"]
        label = data["results"][0].get("formatted_address", query)
        return loc["lat"], loc["lng"], label
    raise SystemExit(f"No pude geocodificar '{query}': {data.get('status')} {data.get('error_message','')}")


def resolve_location(text, api_key=None):
    """text -> (lat, lon, label)."""
    text = text.strip()

    # Caso 1: coordenadas directas "lat,lng"
    if COORD_RE.fullmatch(text):
        lat, lon = map(float, COORD_RE.fullmatch(text).groups())
        if _valid(lat, lon):
            return lat, lon, f"{lat:.5f},{lon:.5f}"

    # Caso 2: es una URL
    if text.startswith("http://") or text.startswith("https://"):
        # Los links cortos (maps.app.goo.gl / goo.gl/maps) hay que seguirlos.
        final_url = text
        try:
            resp = requests.get(
                text, allow_redirects=True, timeout=30,
                headers={"User-Agent": "Mozilla/5.0 (compatible; maps-to-3d/1.0)"},
            )
            final_url = resp.url
            body = resp.text
        except requests.RequestException:
            body = ""
        coords = _extract_coords_from_url(final_url)
        if not coords and body:
            coords = _extract_coords_from_url(body)
        if coords:
            return coords[0], coords[1], "google-maps-link"
        # No hay coords en la URL: intentar geocodificar el nombre del place
        m = re.search(r"/place/([^/@]+)", final_url)
        if m and api_key:
            name = requests.utils.unquote(m.group(1)).replace("+", " ")
            return _geocode(name, api_key)
        raise SystemExit(
            "No pude sacar coordenadas del link. Pegá el link completo (con @lat,lng) "
            "o pasá 'lat,lng' directo. Si es un nombre, configurá GOOGLE_MAPS_API_KEY."
        )

    # Caso 3: coordenadas embebidas en cualquier texto
    m = COORD_RE.search(text)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if _valid(lat, lon):
            return lat, lon, text[:40]

    # Caso 4: nombre libre -> geocodificar
    if api_key:
        return _geocode(text, api_key)
    raise SystemExit(
        f"No entendí '{text}'. Pasá un link de Google Maps, 'lat,lng', o configurá "
        "GOOGLE_MAPS_API_KEY para buscar por nombre."
    )


# ---------------------------------------------------------------------------
# 2) Proyeccion lat/lon -> metros locales (equirectangular alrededor del centro)
# ---------------------------------------------------------------------------

def make_projector(lat0, lon0):
    m_per_deg_lat = 111320.0
    m_per_deg_lon = 111320.0 * math.cos(math.radians(lat0))

    def project(lat, lon):
        x = (lon - lon0) * m_per_deg_lon  # este (+)
        y = (lat - lat0) * m_per_deg_lat  # norte (+)
        return (x, y)

    return project


def _intersect_axis(a, b, axis, val):
    """Punto donde el segmento a-b cruza la recta (coord[axis] == val)."""
    other = 1 - axis
    denom = b[axis] - a[axis]
    t = 0.0 if abs(denom) < 1e-12 else (val - a[axis]) / denom
    o = a[other] + t * (b[other] - a[other])
    return (val, o) if axis == 0 else (o, val)


def clip_polygon(poly, H):
    """Sutherland-Hodgman: recorta un poligono al cuadrado [-H,H]^2."""
    pts = poly[:]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    edges = [
        (lambda p: p[0] >= -H, 0, -H),
        (lambda p: p[0] <= H, 0, H),
        (lambda p: p[1] >= -H, 1, -H),
        (lambda p: p[1] <= H, 1, H),
    ]
    for inside, axis, val in edges:
        if not pts:
            return []
        out = []
        n = len(pts)
        for i in range(n):
            a, b = pts[i], pts[(i + 1) % n]
            ina, inb = inside(a), inside(b)
            if ina:
                out.append(a)
                if not inb:
                    out.append(_intersect_axis(a, b, axis, val))
            elif inb:
                out.append(_intersect_axis(a, b, axis, val))
        pts = out
    return pts


def clip_segment(a, b, H):
    """Liang-Barsky: recorta el segmento a-b al cuadrado [-H,H]^2. None si queda afuera."""
    x1, y1 = a
    x2, y2 = b
    dx, dy = x2 - x1, y2 - y1
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x1 + H), (dx, H - x1), (-dy, y1 + H), (dy, H - y1)):
        if abs(p) < 1e-12:
            if q < 0:
                return None
            continue
        r = q / p
        if p < 0:
            if r > t1:
                return None
            if r > t0:
                t0 = r
        else:
            if r < t0:
                return None
            if r < t1:
                t1 = r
    return ((x1 + t0 * dx, y1 + t0 * dy), (x1 + t1 * dx, y1 + t1 * dy))


def clip_scene(buildings, roads, areas, H):
    """Recorta toda la geometria al cuadrado del radio pedido."""
    b_out = []
    for b in buildings:
        fp = clip_polygon(b["footprint"], H)
        if len(fp) >= 3:
            b2 = dict(b); b2["footprint"] = fp
            b_out.append(b2)

    a_out = []
    for a in areas:
        pg = clip_polygon(a["polygon"], H)
        if len(pg) >= 3:
            a2 = dict(a); a2["polygon"] = pg
            a_out.append(a2)

    r_out = []
    for r in roads:
        path = r["path"]
        for i in range(len(path) - 1):
            seg = clip_segment(path[i], path[i + 1], H)
            if seg:
                r2 = dict(r); r2["path"] = [list(seg[0]), list(seg[1])]
                r_out.append(r2)
    return b_out, r_out, a_out


def bbox_around(lat0, lon0, radius_m):
    dlat = radius_m / 111320.0
    dlon = radius_m / (111320.0 * math.cos(math.radians(lat0)))
    south, north = lat0 - dlat, lat0 + dlat
    west, east = lon0 - dlon, lon0 + dlon
    return south, west, north, east


# ---------------------------------------------------------------------------
# 3) OpenStreetMap: alturas, colores y parseo
# ---------------------------------------------------------------------------

# Colores base por tipo de edificio (RGB 0..1, gamma sRGB). Paleta colorida.
BUILDING_COLORS = {
    "house":        (0.87, 0.62, 0.45),  # terracota / adobe
    "detached":     (0.85, 0.67, 0.49),
    "residential":  (0.82, 0.69, 0.53),
    "apartments":   (0.60, 0.69, 0.80),  # azul empolvado
    "commercial":   (0.46, 0.67, 0.66),  # verde azulado
    "retail":       (0.88, 0.57, 0.49),  # coral
    "office":       (0.54, 0.60, 0.76),  # azul pizarra
    "industrial":   (0.68, 0.64, 0.58),
    "warehouse":    (0.68, 0.64, 0.58),
    "hotel":        (0.73, 0.55, 0.71),  # malva
    "church":       (0.90, 0.83, 0.62),  # crema dorado
    "cathedral":    (0.90, 0.83, 0.62),
    "school":       (0.90, 0.68, 0.44),  # naranja calido
    "university":   (0.87, 0.71, 0.50),
    "hospital":     (0.88, 0.75, 0.72),
    "public":       (0.74, 0.72, 0.82),
    "garage":       (0.66, 0.64, 0.60),
    "roof":         (0.70, 0.48, 0.38),
    "obelisk":      (0.86, 0.84, 0.80),  # piedra clara
    "tower":        (0.72, 0.70, 0.68),
    "monument":     (0.84, 0.82, 0.78),
    "_default":     (0.82, 0.72, 0.58),
}

# Paleta colorida para edificios sin tipo (la mayoria). Tonos medios y variados.
VARIED_NEUTRALS = [
    (0.87, 0.58, 0.46),  # terracota
    (0.90, 0.75, 0.45),  # ocre
    (0.60, 0.73, 0.55),  # salvia
    (0.55, 0.67, 0.80),  # celeste
    (0.83, 0.64, 0.69),  # rosa viejo
    (0.47, 0.69, 0.67),  # verde azulado
    (0.74, 0.68, 0.85),  # lavanda
    (0.91, 0.83, 0.61),  # crema
]


def varied_default(x, y):
    idx = int(abs(round(x * 7.3 + y * 13.7))) % len(VARIED_NEUTRALS)
    return VARIED_NEUTRALS[idx]

# Alturas default por tipo (metros) cuando no hay height ni levels
DEFAULT_HEIGHT = {
    "house": 6.0, "detached": 6.0, "garage": 3.0, "roof": 4.0,
    "apartments": 18.0, "residential": 9.0, "commercial": 12.0,
    "retail": 8.0, "office": 20.0, "industrial": 9.0, "warehouse": 9.0,
    "hotel": 22.0, "church": 15.0, "cathedral": 25.0, "hospital": 20.0,
    "school": 9.0, "university": 12.0, "_default": 9.0,
    # landmarks verticales (se construyen como agujas, no cajas)
    "obelisk": 67.0, "tower": 45.0, "monument": 28.0, "mast": 40.0, "chimney": 35.0,
}

# Tipos que se construyen como aguja/torre afinada en vez de caja con techo
LANDMARK_TYPES = {"obelisk", "tower", "monument", "mast", "chimney"}

LEVEL_HEIGHT = 3.2  # metros por piso

ROAD_WIDTH = {
    "motorway": 14, "trunk": 12, "primary": 10, "secondary": 8,
    "tertiary": 7, "residential": 5.5, "living_street": 5, "unclassified": 5.5,
    "service": 3.5, "pedestrian": 4, "footway": 2, "path": 1.5,
    "cycleway": 2, "track": 3, "_default": 5,
}
ROAD_COLOR = (0.28, 0.28, 0.30)
PED_COLOR = (0.55, 0.52, 0.48)
RAIL_COLOR = (0.32, 0.24, 0.19)   # vias de tren (marron oxido)
BRIDGE_COLOR = (0.42, 0.40, 0.40)  # tablero del puente
BRIDGE_Z = 5.0                     # altura a la que se eleva un puente (bridge=yes)


def _hex_to_rgb(s):
    s = s.strip().lstrip("#")
    named = {
        "white": (0.9, 0.9, 0.9), "black": (0.1, 0.1, 0.1), "red": (0.7, 0.2, 0.2),
        "blue": (0.3, 0.4, 0.7), "green": (0.3, 0.6, 0.3), "yellow": (0.85, 0.8, 0.4),
        "grey": (0.6, 0.6, 0.6), "gray": (0.6, 0.6, 0.6), "brown": (0.55, 0.4, 0.3),
        "beige": (0.85, 0.8, 0.68), "orange": (0.85, 0.55, 0.3), "sandstone": (0.82, 0.72, 0.55),
    }
    if s.lower() in named:
        return named[s.lower()]
    if re.fullmatch(r"[0-9a-fA-F]{6}", s):
        return tuple(int(s[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return None


def parse_height(tags):
    """Devuelve (height_m, min_height_m)."""
    h = None
    if "height" in tags:
        m = re.search(r"(-?\d+(?:\.\d+)?)", tags["height"])
        if m:
            h = float(m.group(1))
    if h is None and "building:levels" in tags:
        m = re.search(r"(\d+(?:\.\d+)?)", tags["building:levels"])
        if m:
            h = float(m.group(1)) * LEVEL_HEIGHT
    if h is None:
        btype = tags.get("building", "_default")
        if btype in ("yes", "true", "1"):
            btype = "_default"
        h = DEFAULT_HEIGHT.get(btype, DEFAULT_HEIGHT["_default"])

    min_h = 0.0
    if "min_height" in tags:
        m = re.search(r"(-?\d+(?:\.\d+)?)", tags["min_height"])
        if m:
            min_h = float(m.group(1))
    elif "building:min_level" in tags:
        m = re.search(r"(\d+(?:\.\d+)?)", tags["building:min_level"])
        if m:
            min_h = float(m.group(1)) * LEVEL_HEIGHT
    h = max(h, min_h + 2.0)
    return h, min_h


def height_source(tags):
    """De donde salio la altura: 'explicit' (tag height), 'levels' (building:levels)
    o 'default' (estimada por tipo). Sirve para saber cuanto confiar en la altura."""
    if "height" in tags and re.search(r"\d", tags["height"]):
        return "explicit"
    if "building:levels" in tags and re.search(r"\d", tags["building:levels"]):
        return "levels"
    return "default"


def building_confidence(tags):
    """Confianza [0..1] en la reconstruccion del edificio segun la calidad del dato
    OSM: altura explicita > por niveles > estimada; con nombre suma un poco."""
    src = height_source(tags)
    base = {"explicit": 0.9, "levels": 0.75, "default": 0.4}[src]
    if tags.get("name"):
        base = min(1.0, base + 0.05)
    return round(base, 2)


def building_color(tags):
    for key in ("building:colour", "building:color"):
        if key in tags:
            c = _hex_to_rgb(tags[key])
            if c:
                return c
    btype = tags.get("building", "_default")
    if btype in ("yes", "true", "1"):
        btype = tags.get("amenity", "_default")
    if btype in BUILDING_COLORS:
        return BUILDING_COLORS[btype]
    for key in ("amenity", "shop", "office", "tourism"):
        if key in tags:
            return BUILDING_COLORS.get(tags[key], BUILDING_COLORS["_default"])
    return BUILDING_COLORS["_default"]


def build_overpass_query(south, west, north, east):
    b = f"{south},{west},{north},{east}"
    return f"""
[out:json][timeout:60];
(
  way["building"]({b});
  relation["building"]["type"="multipolygon"]({b});
  way["highway"]({b});
  way["railway"~"^(rail|light_rail|subway|tram|narrow_gauge|monorail)$"]({b});
  way["natural"="water"]({b});
  relation["natural"="water"]({b});
  way["waterway"="riverbank"]({b});
  way["leisure"~"park|garden|pitch|playground"]({b});
  way["landuse"~"grass|forest|meadow|recreation_ground|village_green"]({b});
  way["natural"~"wood|scrub|grassland"]({b});
);
out geom;
""".strip()


def fetch_overpass(query):
    last_err = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            r = requests.post(
                endpoint, data={"data": query},
                headers={"User-Agent": USER_AGENT}, timeout=90,
            )
            if r.status_code == 200:
                return r.json()
            last_err = f"HTTP {r.status_code} en {endpoint}"
            print(f"   (Overpass {last_err}, probando otro servidor...)")
        except requests.RequestException as e:
            last_err = f"{type(e).__name__} en {endpoint}"
            print(f"   (Overpass falló en {endpoint}, probando otro...)")
    raise SystemExit(
        f"No pude consultar Overpass/OSM ({last_err}). Los servidores publicos de "
        "OSM estan saturados; reintentá en un rato o pasá otro endpoint."
    )


def _ring_from_geometry(geom):
    return [(p["lon"], p["lat"]) for p in geom if "lat" in p and "lon" in p]


def parse_osm(data, project):
    """Convierte la respuesta de Overpass en el dict de escena (coordenadas en metros)."""
    buildings, roads, areas = [], [], []

    def to_xy(ring_lonlat):
        return [project(lat, lon) for (lon, lat) in ring_lonlat]

    def add_building(tags, ring_lonlat, osm_id=None, osm_type=None):
        pts = to_xy(ring_lonlat)
        if len(pts) < 4:  # necesita cerrar (>=3 unicos + repetido)
            return
        h, min_h = parse_height(tags)
        color = building_color(tags)
        if tuple(round(c, 3) for c in color) == tuple(round(c, 3) for c in BUILDING_COLORS["_default"]):
            color = varied_default(pts[0][0], pts[0][1])  # tono calido variado
        buildings.append({
            "footprint": pts,
            "height": round(h, 2),
            "min_height": round(min_h, 2),
            "color": [round(c, 3) for c in color],
            "type": tags.get("building", "yes"),
            # --- F2a: procedencia + confianza por edificio ---
            "osm_id": osm_id,
            "osm_type": osm_type,
            "name": tags.get("name"),
            "roof_shape": tags.get("roof:shape"),
            "height_source": height_source(tags),
            "confidence": building_confidence(tags),
            "source": "osm",
        })

    def add_road(tags, ring_lonlat):
        pts = to_xy(ring_lonlat)
        if len(pts) < 2:
            return
        is_bridge = tags.get("bridge") in ("yes", "viaduct", "true", "1")
        if "railway" in tags:
            width, color, rtype, z = 4.5, RAIL_COLOR, "rail:" + tags["railway"], 0.05
        else:
            hw = tags.get("highway", "_default")
            width = ROAD_WIDTH.get(hw, ROAD_WIDTH["_default"])
            color = PED_COLOR if hw in ("footway", "path", "pedestrian", "cycleway", "track") else ROAD_COLOR
            rtype, z = hw, 0.06
        if is_bridge:
            z = BRIDGE_Z                       # elevar el tablero del puente
            color = BRIDGE_COLOR
            width = max(width, 8.0)
        roads.append({"path": pts, "width": width, "color": list(color), "type": rtype, "z": z})

    def add_area(tags, ring_lonlat, kind):
        pts = to_xy(ring_lonlat)
        if len(pts) < 4:
            return
        if kind == "water":
            color, z = [0.30, 0.48, 0.62], 0.04
        else:  # verde
            color, z = [0.42, 0.58, 0.36], 0.03
        areas.append({"polygon": pts, "z": z, "color": color, "type": tags.get("leisure") or tags.get("landuse") or tags.get("natural") or kind})

    for el in data.get("elements", []):
        tags = el.get("tags", {}) or {}
        etype = el.get("type")

        if "building" in tags:
            oid = el.get("id")
            if etype == "way" and el.get("geometry"):
                add_building(tags, _ring_from_geometry(el["geometry"]), oid, etype)
            elif etype == "relation":
                for mem in el.get("members", []):
                    if mem.get("role") == "outer" and mem.get("geometry"):
                        add_building(tags, _ring_from_geometry(mem["geometry"]), oid, etype)
            continue

        if ("highway" in tags or "railway" in tags) and etype == "way" and el.get("geometry"):
            add_road(tags, _ring_from_geometry(el["geometry"]))
            continue

        is_water = tags.get("natural") == "water" or tags.get("waterway") == "riverbank"
        is_green = (tags.get("leisure") in ("park", "garden", "pitch", "playground")
                    or tags.get("landuse") in ("grass", "forest", "meadow", "recreation_ground", "village_green")
                    or tags.get("natural") in ("wood", "scrub", "grassland"))
        if is_water or is_green:
            kind = "water" if is_water else "green"
            if etype == "way" and el.get("geometry"):
                add_area(tags, _ring_from_geometry(el["geometry"]), kind)
            elif etype == "relation":
                for mem in el.get("members", []):
                    if mem.get("role") == "outer" and mem.get("geometry"):
                        add_area(tags, _ring_from_geometry(mem["geometry"]), kind)

    return buildings, roads, areas


# ---------------------------------------------------------------------------
# 4) Google Street View + fotos (opcional, necesita API key)
# ---------------------------------------------------------------------------

def download_streetview(lat, lon, out_dir, api_key):
    saved = []
    meta = requests.get(
        "https://maps.googleapis.com/maps/api/streetview/metadata",
        params={"location": f"{lat},{lon}", "key": api_key}, timeout=30,
    ).json()
    if meta.get("status") != "OK":
        print(f"  Street View: sin cobertura acá ({meta.get('status')}). Salteando.")
        return saved
    sv_dir = out_dir / "streetview"
    sv_dir.mkdir(parents=True, exist_ok=True)
    for heading in (0, 90, 180, 270):
        r = requests.get(
            "https://maps.googleapis.com/maps/api/streetview",
            params={"size": "640x640", "location": f"{lat},{lon}",
                    "heading": heading, "pitch": 8, "fov": 90, "key": api_key},
            timeout=30,
        )
        if r.status_code == 200 and r.content[:3] != b"<ht":
            p = sv_dir / f"heading_{heading:03d}.jpg"
            p.write_bytes(r.content)
            saved.append(str(p))
    print(f"  Street View: {len(saved)} imagenes guardadas en {sv_dir}")
    return saved


def download_place_photos(lat, lon, out_dir, api_key, max_photos=4):
    saved = []
    try:
        nearby = requests.get(
            "https://maps.googleapis.com/maps/api/place/nearbysearch/json",
            params={"location": f"{lat},{lon}", "radius": 60, "key": api_key},
            timeout=30,
        ).json()
    except requests.RequestException:
        return saved
    if nearby.get("status") not in ("OK", "ZERO_RESULTS"):
        return saved
    refs = []
    for res in nearby.get("results", []):
        for ph in res.get("photos", []) or []:
            refs.append(ph["photo_reference"])
            if len(refs) >= max_photos:
                break
        if len(refs) >= max_photos:
            break
    if not refs:
        return saved
    ph_dir = out_dir / "photos"
    ph_dir.mkdir(parents=True, exist_ok=True)
    for i, ref in enumerate(refs):
        r = requests.get(
            "https://maps.googleapis.com/maps/api/place/photo",
            params={"maxwidth": 800, "photo_reference": ref, "key": api_key},
            timeout=30, allow_redirects=True,
        )
        if r.status_code == 200 and r.content[:3] != b"<ht":
            p = ph_dir / f"photo_{i:02d}.jpg"
            p.write_bytes(r.content)
            saved.append(str(p))
    print(f"  Fotos del lugar: {len(saved)} guardadas en {ph_dir}")
    return saved


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def slugify(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    text = re.sub(r"[-\s]+", "-", text)
    return text[:40] or "lugar"


def run_blender(scene_path, blend_path, render_path, blender_bin):
    if blender_bin:
        cmd = [blender_bin, "-b", "-P", str(BLENDER_BUILD), "--",
               str(scene_path), str(blend_path), str(render_path)]
    else:
        cmd = [sys.executable, str(BLENDER_BUILD),
               str(scene_path), str(blend_path), str(render_path)]
    print(f"\n▶ Blender: {' '.join(cmd)}\n")
    return subprocess.run(cmd).returncode


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _extract_place(argv):
    """Separa el 'place' de las flags, para permitir coords negativas como '-34.6,-58.3'."""
    value_flags = {"--radius", "--out", "--blender"}
    place, rest = None, []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in value_flags:
            rest.append(a)
            if i + 1 < len(argv):
                rest.append(argv[i + 1])
                i += 1
        elif a.startswith("--") or a in ("-h",):
            rest.append(a)
        elif place is None:
            place = a           # primer token suelto = el lugar (coord/URL/nombre)
        else:
            rest.append(a)
        i += 1
    return place, rest


def main():
    ap = argparse.ArgumentParser(description="Google Maps -> modelo 3D en Blender")
    ap.add_argument("--radius", type=float, default=250, help="Radio en metros (default 250)")
    ap.add_argument("--out", default=None, help="Carpeta de salida")
    ap.add_argument("--no-streetview", action="store_true", help="No bajar imagenes de Google")
    ap.add_argument("--no-render", action="store_true", help="No correr Blender (solo datos)")
    ap.add_argument("--blender", default=os.environ.get("BLENDER_BIN"), help="Ruta al binario de Blender")

    place, rest = _extract_place(sys.argv[1:])
    args = ap.parse_args(rest)
    if not place:
        ap.error("Falta el lugar. Ej: place_to_3d.py \"-34.6037,-58.3816\" --radius 200")
    args.place = place

    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")

    print(f"➊ Resolviendo lugar: {args.place!r}")
    lat, lon, label = resolve_location(args.place, api_key)
    print(f"   -> {lat:.6f}, {lon:.6f}  ({label})")

    out_dir = Path(args.out) if args.out else HERE.parent / "output" / slugify(label)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"➋ Bajando geometria de OpenStreetMap (radio {args.radius:.0f} m)...")
    south, west, north, east = bbox_around(lat, lon, args.radius)
    project = make_projector(lat, lon)
    osm = fetch_overpass(build_overpass_query(south, west, north, east))
    buildings, roads, areas = parse_osm(osm, project)
    # Recortar todo al radio pedido (Overpass devuelve calles largas enteras)
    buildings, roads, areas = clip_scene(buildings, roads, areas, args.radius)
    print(f"   -> {len(buildings)} edificios, {len(roads)} calles, {len(areas)} areas (agua/verde)")

    if not buildings and not roads:
        print("   ⚠ OSM no devolvió nada en esta zona. Probá otro lugar o más radio.")

    xs = [p[0] for b in buildings for p in b["footprint"]] + [p[0] for r in roads for p in r["path"]]
    ys = [p[1] for b in buildings for p in b["footprint"]] + [p[1] for r in roads for p in r["path"]]
    if xs and ys:
        bounds = {"minx": min(xs), "miny": min(ys), "maxx": max(xs), "maxy": max(ys)}
    else:
        bounds = {"minx": -args.radius, "miny": -args.radius, "maxx": args.radius, "maxy": args.radius}

    streetview, photos = [], []
    if api_key and not args.no_streetview:
        print("➌ Bajando Street View + fotos de Google...")
        try:
            streetview = download_streetview(lat, lon, out_dir, api_key)
            photos = download_place_photos(lat, lon, out_dir, api_key)
        except requests.RequestException as e:
            print(f"   (Google falló: {e})")
    elif not api_key and not args.no_streetview:
        print("➌ (Sin GOOGLE_MAPS_API_KEY: salteo Street View/fotos)")

    scene = {
        "center": {"lat": lat, "lon": lon, "label": label},
        "radius": args.radius,
        "bounds": bounds,
        "buildings": buildings,
        "roads": roads,
        "areas": areas,
        "streetview": streetview,
        "photos": photos,
    }
    scene_path = out_dir / "scene.json"
    scene_path.write_text(json.dumps(scene))
    print(f"➍ scene.json -> {scene_path}  ({scene_path.stat().st_size // 1024} KB)")

    blend_path = out_dir / "model.blend"
    render_path = out_dir / "render.png"

    if args.no_render:
        print("   (--no-render: no abro Blender)")
        print(f"\n✅ Datos listos en {out_dir}")
        return

    print("➎ Construyendo y renderizando en Blender...")
    rc = run_blender(scene_path, blend_path, render_path, args.blender)
    if rc != 0:
        sys.exit(f"Blender terminó con código {rc}")

    print(f"\n✅ Listo:")
    print(f"   Render : {render_path}")
    print(f"   Blend  : {blend_path}")
    if streetview:
        print(f"   Street View: {out_dir / 'streetview'}")
    if photos:
        print(f"   Fotos  : {out_dir / 'photos'}")


if __name__ == "__main__":
    main()
