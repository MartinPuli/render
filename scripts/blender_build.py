#!/usr/bin/env python3
"""
blender_build.py  —  Construye la escena 3D en Blender a partir de scene.json y renderiza.

Se puede correr de dos formas:
  A) Con el binario de Blender:
       blender -b -P blender_build.py -- scene.json model.blend render.png
  B) Con el modulo de Python 'bpy' (pip install bpy):
       python3 blender_build.py scene.json model.blend render.png

Coordenadas del scene.json: metros, X=este, Y=norte, Z=arriba. 1 unidad Blender = 1 metro.
"""
import json
import math
import os
import sys

import bpy
import bmesh

# Carpeta con texturas CC0 (asphalt.jpg, pavement.jpg, concrete.jpg). Si esta
# seteada y los archivos existen, se usan texturas fotograficas reales en piso y
# calles. La skill puede bajarlas de PolyHaven y apuntar aca. Si no, colores planos.
TEXTURE_DIR = os.environ.get("MAPS3D_TEXTURES", "")
# HDRI de cielo real (con nubes) para iluminacion + reflejos foto-reales.
HDRI_PATH = os.environ.get("MAPS3D_HDRI", "")


def _tex(name):
    if TEXTURE_DIR:
        p = os.path.join(TEXTURE_DIR, name)
        if os.path.isfile(p):
            return p
    return None


def _texset(base):
    """Devuelve {'diff':path,'rough':path,'nor':path} para un set PBR <base>_*,
    o None si no hay al menos el diffuse. Busca en MAPS3D_TEXTURES."""
    if not TEXTURE_DIR:
        return None
    out = {}
    for suffix in ("diff", "rough", "nor"):
        for ext in ("jpg", "png", "exr"):
            p = os.path.join(TEXTURE_DIR, f"{base}_{suffix}.{ext}")
            if os.path.isfile(p):
                out[suffix] = p
                break
    return out if out.get("diff") else None


def using_hdri():
    return bool(HDRI_PATH and os.path.isfile(HDRI_PATH))

# --- Parametros de render / camara ---
RES_X, RES_Y = 1280, 960
CYCLES_SAMPLES = 48
SUN_ENERGY = 2.4
WORLD_STRENGTH = 0.18   # ambiente bajo => colores mas saturados (no lavados)
EXPOSURE = -0.5         # compensacion de exposicion (evita quemar los blancos)
CAM_ELEV_DEG = 38.0     # elevacion de la camara (vista 3/4 aerea)
CAM_AZIM_DEG = 45.0     # azimut
CAM_DIST_FACTOR = 2.15  # distancia = radio_escena * factor
CAM_FRAME_FACTOR = 1.05  # cuanto margen dejar alrededor de la escena
GROUND_COLOR = (0.30, 0.31, 0.32)   # vereda/plaza de hormigon claro
ASPHALT_COLOR = (0.075, 0.075, 0.085)  # calzada asfaltada (albedo real bajo)
MARKING_COLOR = (0.82, 0.82, 0.78)     # lineas viales (blanco sucio)
SIDEWALK_COLOR = (0.46, 0.45, 0.44)    # vereda de hormigon (mas clara que la calzada)
BRIDGE_MAT_COLOR = (0.46, 0.45, 0.43)  # tablero de puente (hormigon)
WATER_COLOR = (0.020, 0.055, 0.085)    # agua profunda (oscura, refleja el cielo)
ROOF_HOUSE_COLOR = (0.62, 0.30, 0.24)  # teja terracota
ROOF_FLAT_COLOR = (0.44, 0.45, 0.48)   # azotea gris
ROOF_SMALL_MAX_H = 11.0  # edificios mas bajos que esto -> techo a dos aguas
BEVEL = 0.18             # bisel de aristas de edificios (m)

# --- Clasificacion de materiales por altura ---
# En ciudades modernas (p.ej. Puerto Madero) las torres altas son cortina de
# vidrio reflectante; lo intermedio, fachada moderna; lo bajo, mamposteria.
GLASS_MIN_HEIGHT = 36.0     # >= esto -> torre de vidrio reflectante
MODERN_MIN_HEIGHT = 20.0    # entre esto y GLASS -> tambien mayormente vidrio
GLASS_TINTS = [
    (0.030, 0.055, 0.075),  # azul noche
    (0.035, 0.070, 0.080),  # azul verdoso
    (0.045, 0.080, 0.095),  # turquesa oscuro
    (0.030, 0.050, 0.070),  # gris azulado
    (0.050, 0.075, 0.085),  # petroleo
]
MULLION_COLOR = (0.62, 0.65, 0.68)   # perfil de aluminio (parantes/antepechos)

# Tonemapping fotografico para vistas a nivel de calle con cielo fisico.
STREET_EXPOSURE = -3.0        # con cielo fisico Nishita (muy brillante)
STREET_EXPOSURE_HDRI = -0.5   # con HDRI real (calibrado distinto)
HDRI_ROT_DEG = 40.0           # rotacion del HDRI (para orientar el sol)
STREET_LOOKS = ("AgX - High Contrast", "AgX - Punchy", "AgX - Medium High Contrast",
                "High Contrast", "None")


# ---------------------------------------------------------------------------
# Argumentos (tanto bajo 'blender --' como bajo python directo)
# ---------------------------------------------------------------------------

def parse_args():
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = argv[1:]
    if len(argv) < 3:
        sys.exit("Uso: blender_build.py scene.json model.blend render.png")
    return argv[0], argv[1], argv[2]


# ---------------------------------------------------------------------------
# Helpers de geometria
# ---------------------------------------------------------------------------

def dedupe_ring(pts, closed=True, eps=1e-4):
    """Saca puntos consecutivos repetidos y, si es anillo, el ultimo == primero."""
    out = []
    for p in pts:
        if not out or abs(p[0] - out[-1][0]) > eps or abs(p[1] - out[-1][1]) > eps:
            out.append((p[0], p[1]))
    if closed and len(out) > 1 and abs(out[0][0] - out[-1][0]) < eps and abs(out[0][1] - out[-1][1]) < eps:
        out.pop()
    return out


def add_prism(bm, footprint, base_z, top_z):
    """Agrega un edificio (prisma cerrado) al bmesh."""
    pts = dedupe_ring(footprint, closed=True)
    if len(pts) < 3:
        return
    try:
        verts = [bm.verts.new((x, y, base_z)) for (x, y) in pts]
        face = bm.faces.new(verts)
    except ValueError:
        return
    res = bmesh.ops.extrude_face_region(bm, geom=[face])
    top_verts = [g for g in res["geom"] if isinstance(g, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, vec=(0.0, 0.0, top_z - base_z), verts=top_verts)


def add_flat_polygon(bm, poly, z):
    """Agrega un poligono plano (agua / verde) al bmesh."""
    pts = dedupe_ring(poly, closed=True)
    if len(pts) < 3:
        return
    try:
        verts = [bm.verts.new((x, y, z)) for (x, y) in pts]
        bm.faces.new(verts)
    except ValueError:
        pass


def _centroid_bbox(pts):
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    sx = max(p[0] for p in pts) - min(p[0] for p in pts)
    sy = max(p[1] for p in pts) - min(p[1] for p in pts)
    return cx, cy, sx, sy


def add_hip_roof(bm, footprint, top_z):
    """Techo a cuatro aguas (frustum): anillo original + anillo achicado y elevado."""
    pts = dedupe_ring(footprint, closed=True)
    if len(pts) < 3:
        return
    cx, cy, sx, sy = _centroid_bbox(pts)
    roof_h = max(1.5, min(4.5, 0.35 * min(sx, sy)))
    shrink = 0.62
    try:
        base = [bm.verts.new((x, y, top_z)) for (x, y) in pts]
        top = [bm.verts.new((cx + (x - cx) * (1 - shrink),
                             cy + (y - cy) * (1 - shrink),
                             top_z + roof_h)) for (x, y) in pts]
    except ValueError:
        return
    n = len(pts)
    for i in range(n):
        j = (i + 1) % n
        try:
            bm.faces.new((base[i], base[j], top[j], top[i]))
        except ValueError:
            pass
    try:
        bm.faces.new(top)   # tapa superior
    except ValueError:
        pass


def add_rooftop_detail(bm, footprint, top_z):
    """Cajita en la azotea (tanque/ascensor) para dar detalle a las torres."""
    pts = dedupe_ring(footprint, closed=True)
    if len(pts) < 3:
        return
    cx, cy, sx, sy = _centroid_bbox(pts)
    w = max(1.6, min(6.0, 0.32 * min(sx, sy)))
    h = 2.6
    box = [(cx - w / 2, cy - w / 2), (cx + w / 2, cy - w / 2),
           (cx + w / 2, cy + w / 2), (cx - w / 2, cy + w / 2)]
    add_prism(bm, box, top_z, top_z + h)


LANDMARK_TYPES = {"obelisk", "tower", "monument", "mast", "chimney"}


def add_spire(bm, footprint, base_z, top_z):
    """Aguja/torre afinada con punta piramidal (obeliscos, monumentos, mastiles)."""
    pts = dedupe_ring(footprint, closed=True)
    if len(pts) < 3:
        return
    cx, cy, sx, sy = _centroid_bbox(pts)
    shaft_top = base_z + (top_z - base_z) * 0.85
    taper = 0.25  # se afina hacia arriba
    try:
        bottom = [bm.verts.new((x, y, base_z)) for (x, y) in pts]
        ring = [bm.verts.new((cx + (x - cx) * (1 - taper),
                              cy + (y - cy) * (1 - taper), shaft_top))
                for (x, y) in pts]
        apex = bm.verts.new((cx, cy, top_z))
    except ValueError:
        return
    n = len(pts)
    for i in range(n):
        j = (i + 1) % n
        try:
            bm.faces.new((bottom[i], bottom[j], ring[j], ring[i]))  # fuste
        except ValueError:
            pass
        try:
            bm.faces.new((ring[i], ring[j], apex))                  # punta
        except ValueError:
            pass
    try:
        bm.faces.new(bottom)  # base
    except ValueError:
        pass


def _icosphere(bm, radius, subdivisions=2):
    try:
        return bmesh.ops.create_icosphere(bm, subdivisions=subdivisions, radius=radius)
    except TypeError:
        return bmesh.ops.create_icosphere(bm, subdivisions=subdivisions, diameter=radius * 2)


def _hash01(seed):
    n = math.sin(seed * 127.1 + 311.7) * 43758.5453
    return n - math.floor(n)


def add_tree(trunk_bm, fol_bm, cards_bm, x, y, height=6.0):
    """Arbol: tronco fino + nucleo MULTI-LOBULADO (masa opaca) + shell de leaf-cards
    (quads con alpha) que rompe la silueta y deja ver cielo entre las hojas."""
    seed = x * 12.9898 + y * 4.1414
    tw = max(0.16, height * 0.032)
    box = [(x - tw, y - tw), (x + tw, y - tw), (x + tw, y + tw), (x - tw, y + tw)]
    add_prism(trunk_bm, box, 0.0, height * 0.55)
    base_r = height * 0.30
    # Nucleo opaco: lobulos algo mas chicos (los cards extienden el volumen).
    lobes = [(0.0, 0.0, 0.02, 0.85, 2),
             (0.50, 0.18, -0.05, 0.55, 1),
             (-0.35, -0.40, 0.06, 0.50, 1)]
    for li, (ox, oy, zoff, rf, sub) in enumerate(lobes):
        jr = 0.85 + 0.30 * _hash01(seed + li * 3.7)
        res = _icosphere(fol_bm, radius=base_r * rf * jr, subdivisions=sub)
        verts = [v for v in res["verts"]]
        s2 = seed + li * 7.3
        for v in verts:
            n = math.sin(v.co.x * 3.1 + v.co.y * 5.7 + v.co.z * 2.3 + s2) * 43758.5453
            f = 0.74 + 0.40 * (n - math.floor(n))
            v.co.x *= f
            v.co.y *= f
            v.co.z *= f * 0.82
        bmesh.ops.translate(fol_bm, verts=verts,
                            vec=(x + ox * base_r, y + oy * base_r, height * 0.80 + zoff * height))
    # Shell de leaf-cards (quads verticales) sobre la copa: silueta rota + huecos.
    for c in range(40):
        u = _hash01(seed + c * 1.13)
        vv = _hash01(seed + c * 2.71)
        ww = _hash01(seed + c * 3.97)
        theta = u * 6.28318
        phi = math.acos(2.0 * vv - 1.0)
        rr = base_r * (0.80 + 0.55 * ww)
        px = x + rr * math.sin(phi) * math.cos(theta)
        py = y + rr * math.sin(phi) * math.sin(theta)
        pz = height * 0.80 + rr * math.cos(phi) * 0.82
        a1 = _hash01(seed + c * 5.13) * 6.28318
        ax, ay = math.cos(a1), math.sin(a1)
        s = base_r * 0.45
        try:
            v1 = cards_bm.verts.new((px - ax * s, py - ay * s, pz - s))
            v2 = cards_bm.verts.new((px + ax * s, py + ay * s, pz - s))
            v3 = cards_bm.verts.new((px + ax * s, py + ay * s, pz + s))
            v4 = cards_bm.verts.new((px - ax * s, py - ay * s, pz + s))
            cards_bm.faces.new((v1, v2, v3, v4))
        except ValueError:
            pass


def scatter_trees(areas, roads, cap=900):
    """Devuelve (trunk_bm, fol_bm, cards_bm, count): troncos + nucleos opacos +
    shell de leaf-cards, para arboles en plazas y sobre avenidas anchas."""
    trunk_bm = bmesh.new()
    fol_bm = bmesh.new()
    cards_bm = bmesh.new()
    count = 0

    # arboles en areas verdes
    for a in areas:
        if a.get("type") in ("water",) or a["color"][2] > a["color"][1]:
            continue  # no en el agua
        pts = dedupe_ring(a["polygon"], closed=True)
        if len(pts) < 3:
            continue
        cx, cy, sx, sy = _centroid_bbox(pts)
        n = min(10, max(1, int(sx * sy / 500)))
        for i in range(n):
            if count >= cap:
                break
            jx = (((i * 37) % 100) / 100.0 - 0.5) * sx * 0.6
            jy = (((i * 61) % 100) / 100.0 - 0.5) * sy * 0.6
            add_tree(trunk_bm, fol_bm, cards_bm, cx + jx, cy + jy, 5.5 + (i % 3))
            count += 1

    # arboles de vereda a lo largo de avenidas (paso regular en ambas manos)
    spacing = 13.0
    for r in roads:
        if count >= cap:
            break
        if float(r.get("width", 0)) < 7.0 or r.get("z", 0) > 1:
            continue
        pts = dedupe_ring(r["path"], closed=False)
        off = float(r["width"]) / 2 + 3.6
        for i in range(len(pts) - 1):
            x1, y1 = pts[i]
            x2, y2 = pts[i + 1]
            dx, dy = x2 - x1, y2 - y1
            L = math.hypot(dx, dy)
            if L < 6:
                continue
            ux, uy = dx / L, dy / L
            nx, ny = -uy, ux
            d = spacing * 0.5
            while d < L and count < cap:
                # jitter a lo largo del eje para romper la fila metronomica
                dj = d + (_hash01(x1 + y1 + d) - 0.5) * spacing * 0.4
                bx, by = x1 + ux * dj, y1 + uy * dj
                for side in (1, -1):
                    if count >= cap:
                        break
                    if _hash01(bx * 1.7 + by * 2.3 + side) < 0.12:
                        continue   # saltear ~12% -> arboladas no perfectas
                    offj = off + (_hash01(bx * 3.1 + by) - 0.5) * 1.2
                    h = 6.6 + _hash01(bx + by) * 2.6
                    add_tree(trunk_bm, fol_bm, cards_bm, bx + nx * offj * side,
                             by + ny * offj * side, h)
                    count += 1
                d += spacing
    return trunk_bm, fol_bm, cards_bm, count


def scatter_streetlights(roads, cap=140, spacing=32.0, height=9.0):
    """Postes de alumbrado finos a lo largo de las avenidas (street furniture)."""
    bm = bmesh.new()
    count = 0
    for r in roads:
        if count >= cap:
            break
        if float(r.get("width", 0)) < 8.0 or r.get("z", 0) > 1:
            continue
        pts = dedupe_ring(r["path"], closed=False)
        off = float(r["width"]) / 2 + 1.2
        for i in range(len(pts) - 1):
            x1, y1 = pts[i]
            x2, y2 = pts[i + 1]
            dx, dy = x2 - x1, y2 - y1
            L = math.hypot(dx, dy)
            if L < 8:
                continue
            ux, uy = dx / L, dy / L
            nx, ny = -uy, ux
            d = spacing * 0.5
            while d < L and count < cap:
                bx, by = x1 + ux * d, y1 + uy * d
                for side in (1, -1):
                    if count >= cap:
                        break
                    px, py = bx + nx * off * side, by + ny * off * side
                    s = 0.12
                    box = [(px - s, py - s), (px + s, py - s),
                           (px + s, py + s), (px - s, py + s)]
                    add_prism(bm, box, 0.0, height)
                    count += 1
                d += spacing
    return bm


# Colores de auto realistas (mayoria blanco/gris/negro/plata en BA).
CAR_COLORS = [
    (0.72, 0.72, 0.73),  # blanco
    (0.72, 0.72, 0.73),  # blanco
    (0.66, 0.67, 0.69),  # plata
    (0.045, 0.045, 0.05),  # negro
    (0.045, 0.045, 0.05),  # negro
    (0.20, 0.21, 0.23),  # gris oscuro
    (0.32, 0.11, 0.11),  # bordo
    (0.10, 0.16, 0.30),  # azul
]


def _oriented_rect(cx, cy, ux, uy, length, width):
    """4 esquinas de un rectangulo centrado en (cx,cy) alineado a (ux,uy)."""
    px, py = -uy, ux
    hl, hw = length / 2.0, width / 2.0
    return [
        (cx - ux * hl - px * hw, cy - uy * hl - py * hw),
        (cx + ux * hl - px * hw, cy + uy * hl - py * hw),
        (cx + ux * hl + px * hw, cy + uy * hl + py * hw),
        (cx - ux * hl + px * hw, cy - uy * hl + py * hw),
    ]


def add_car(body_bm, glass_bm, cx, cy, ux, uy):
    """Auto: carroceria (color, en body_bm) + cabina vidriada oscura (greenhouse,
    en glass_bm) + 4 ruedas oscuras. El 2-tono es lo que lo hace leer como auto."""
    body = _oriented_rect(cx, cy, ux, uy, 4.2, 1.74)
    add_prism(body_bm, body, 0.24, 0.78)          # carroceria (elevada por ruedas)
    cab = _oriented_rect(cx - ux * 0.15, cy - uy * 0.15, ux, uy, 2.1, 1.55)
    add_prism(glass_bm, cab, 0.78, 1.28)          # cabina vidriada oscura
    for a in (1.25, -1.25):                        # 4 ruedas
        for b in (0.80, -0.80):
            wx = cx + ux * a - uy * b
            wy = cy + uy * a + ux * b
            add_prism(glass_bm, _oriented_rect(wx, wy, ux, uy, 0.7, 0.32), 0.0, 0.34)


def scatter_cars(roads, cap=520, spacing=7.5):
    """Autos estacionados junto al cordon de las avenidas. Devuelve
    (buckets_por_color, glass_bm, count) — carrocerias por color + un bmesh comun
    de cabinas/ruedas oscuras."""
    buckets = {i: bmesh.new() for i in range(len(CAR_COLORS))}
    glass_bm = bmesh.new()
    count = 0
    for r in roads:
        if count >= cap:
            break
        w = float(r.get("width", 0))
        if w < 6.0 or r.get("z", 0) > 1:
            continue
        pts = dedupe_ring(r["path"], closed=False)
        off = w / 2.0 - 1.1   # en la banda de estacionamiento, junto al cordon
        for i in range(len(pts) - 1):
            x1, y1 = pts[i]
            x2, y2 = pts[i + 1]
            dx, dy = x2 - x1, y2 - y1
            L = math.hypot(dx, dy)
            if L < 8:
                continue
            ux, uy = dx / L, dy / L
            nx, ny = -uy, ux
            d = spacing
            while d < L - 3 and count < cap:
                bx, by = x1 + ux * d, y1 + uy * d
                for side in (1, -1):
                    if count >= cap:
                        break
                    key = int(abs(round(bx * 7.0 + by * 3.0)))
                    if key % 3 != 0:      # ~1/3 de los lugares ocupados (no fila llena)
                        continue
                    idx = key % len(CAR_COLORS)
                    ccx = bx + nx * off * side
                    ccy = by + ny * off * side
                    if ccx * ccx + ccy * ccy < 49.0:
                        continue   # despejar ~7m alrededor del origen (camara de calle)
                    add_car(buckets[idx], glass_bm, ccx, ccy, ux, uy)
                    count += 1
                d += spacing
    return buckets, glass_bm, count


def _point_in_poly(poly, px, py):
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > py) != (yj > py)) and \
           (px < (xj - xi) * (py - yi) / ((yj - yi) or 1e-9) + xi):
            inside = not inside
        j = i
    return inside


def _pedestrian_bridge(scene):
    """Encuentra la pasarela peatonal mas larga que cruza agua (Puente de la Mujer):
    primero footways elevados (bridge, z>1); si no, footways cuyo medio cae en agua."""
    roads = scene.get("roads", [])
    waters = [a["polygon"] for a in scene.get("areas", []) if a["color"][2] > a["color"][1]]

    def is_ped(t):
        return any(k in str(t) for k in ("foot", "path", "pedestrian", "cycle", "steps"))

    def plen(pts):
        return sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
                   for i in range(len(pts) - 1))

    elevated = [r for r in roads if float(r.get("z", 0)) > 1.0 and is_ped(r.get("type"))]
    if elevated:
        return max(elevated, key=lambda r: plen(r["path"]))
    # fallback: peatonal cuyo punto medio cae dentro de un agua
    best, bestlen = None, 0.0
    for r in roads:
        if not is_ped(r.get("type")):
            continue
        pts = r["path"]
        if len(pts) < 2:
            continue
        mx = sum(p[0] for p in pts) / len(pts)
        my = sum(p[1] for p in pts) / len(pts)
        if any(_point_in_poly(w, mx, my) for w in waters):
            L = plen(pts)
            if L > bestlen:
                bestlen, best = L, r
    return best


def add_puente_mujer(scene, coll, deck_z=5.0):
    """Puente de la Mujer estilizado (Calatrava): tablero + pilono inclinado blanco
    + cables en abanico, ubicado sobre la pasarela peatonal que cruza el dique."""
    r = _pedestrian_bridge(scene)
    if not r:
        return None
    pts = [(float(p[0]), float(p[1])) for p in r["path"]]
    if len(pts) < 2:
        return None
    seglen = [math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
              for i in range(len(pts) - 1)]
    total = sum(seglen) or 1.0
    half, acc, mi = total / 2.0, 0.0, 0
    for i, L in enumerate(seglen):
        if acc + L >= half:
            mi = i
            break
        acc += L
    ax, ay = pts[mi]
    bx, by = pts[mi + 1]
    t = (half - acc) / (seglen[mi] or 1.0)
    mx, my = ax + (bx - ax) * t, ay + (by - ay) * t
    dl = seglen[mi] or 1.0
    tx, ty = (bx - ax) / dl, (by - ay) / dl      # tangente del puente
    nx, ny = -ty, tx                              # normal

    bm = bmesh.new()
    for i in range(len(pts) - 1):                 # tablero
        add_road_strip(bm, [pts[i], pts[i + 1]], 5.0, deck_z)

    base = (mx + nx * 3.0, my + ny * 3.0, deck_z)
    top = (mx - nx * 11.0, my - ny * 11.0, deck_z + 42.0)  # pilono inclinado

    def box_between(p0, p1, r0, r1):
        vb, vt = [], []
        for sx, sy in ((1, 1), (1, -1), (-1, -1), (-1, 1)):
            vb.append(bm.verts.new((p0[0] + tx * sx * r0 + nx * sy * r0,
                                    p0[1] + ty * sx * r0 + ny * sy * r0, p0[2])))
            vt.append(bm.verts.new((p1[0] + tx * sx * r1 + nx * sy * r1,
                                    p1[1] + ty * sx * r1 + ny * sy * r1, p1[2])))
        for i in range(4):
            j = (i + 1) % 4
            try:
                bm.faces.new((vb[i], vb[j], vt[j], vt[i]))
            except ValueError:
                pass

    box_between(base, top, 1.4, 0.4)              # pilono
    for k in range(1, 9):                          # cables en abanico
        d = k / 9.0
        axp = mx + tx * (d * total * 0.42)
        ayp = my + ty * (d * total * 0.42)
        anch = (axp + nx * 1.5, ayp + ny * 1.5, deck_z + 0.3)
        w = 0.11
        try:
            c1 = bm.verts.new((top[0] - nx * w, top[1] - ny * w, top[2]))
            c2 = bm.verts.new((top[0] + nx * w, top[1] + ny * w, top[2]))
            c3 = bm.verts.new((anch[0] + nx * w, anch[1] + ny * w, anch[2]))
            c4 = bm.verts.new((anch[0] - nx * w, anch[1] - ny * w, anch[2]))
            bm.faces.new((c1, c2, c3, c4))
        except ValueError:
            pass
    bm_to_object(bm, "Puente_de_la_Mujer",
                 make_material("puente_mujer", (0.93, 0.93, 0.91),
                               roughness=0.32, specular=0.5),
                 coll=coll, merge=0.0)
    return True


def _add_boundary(coll, minx, miny, maxx, maxy, z=0.2):
    """Marco (loop de aristas) que delimita el area pedida, en 00_REFERENCE."""
    me = bpy.data.meshes.new("Area_boundary")
    verts = [(minx, miny, z), (maxx, miny, z), (maxx, maxy, z), (minx, maxy, z)]
    edges = [(0, 1), (1, 2), (2, 3), (3, 0)]
    me.from_pydata(verts, edges, [])
    me.update()
    ob = bpy.data.objects.new("Area_boundary", me)
    coll.objects.link(ob)
    return ob


def add_bridge_piers(bm, path, deck_z, spacing=18.0, size=1.4):
    """Agrega pilares (columnas) desde el piso hasta el tablero de un puente elevado."""
    pts = dedupe_ring(path, closed=False)
    s = size / 2.0
    placed = 0
    for i in range(len(pts) - 1):
        x1, y1 = pts[i]
        x2, y2 = pts[i + 1]
        dx, dy = x2 - x1, y2 - y1
        L = math.hypot(dx, dy)
        if L < 1e-6:
            continue
        d = 0.0
        while d <= L and placed < 40:
            t = d / L
            cx, cy = x1 + dx * t, y1 + dy * t
            box = [(cx - s, cy - s), (cx + s, cy - s), (cx + s, cy + s), (cx - s, cy + s)]
            add_prism(bm, box, 0.0, deck_z)
            placed += 1
            d += spacing


def add_lane_markings(bm, path, width, z, dash=3.5, gap=5.0, mw=0.16):
    """Linea de eje punteada (blanca) a lo largo de la calzada."""
    pts = dedupe_ring(path, closed=False)
    zz = z + 0.02
    for i in range(len(pts) - 1):
        x1, y1 = pts[i]
        x2, y2 = pts[i + 1]
        dx, dy = x2 - x1, y2 - y1
        L = math.hypot(dx, dy)
        if L < 1e-6:
            continue
        ux, uy = dx / L, dy / L
        nx, ny = -uy * mw, ux * mw
        d = 0.0
        while d < L:
            a, bpos = d, min(L, d + dash)
            ax, ay = x1 + ux * a, y1 + uy * a
            bx, by = x1 + ux * bpos, y1 + uy * bpos
            try:
                v1 = bm.verts.new((ax + nx, ay + ny, zz))
                v2 = bm.verts.new((ax - nx, ay - ny, zz))
                v3 = bm.verts.new((bx - nx, by - ny, zz))
                v4 = bm.verts.new((bx + nx, by + ny, zz))
                bm.faces.new((v1, v2, v3, v4))
            except ValueError:
                pass
            d += dash + gap


def add_sidewalks(bm, path, road_width, z, sw=3.6, curb_h=0.15):
    """Vereda ELEVADA (hormigon) a los lados de la calzada, con cara de cordon
    vertical en el borde interno: da un escalon real que apoya autos y arboles."""
    pts = dedupe_ring(path, closed=False)
    inner = road_width / 2.0
    outer = inner + sw
    top = z + curb_h
    for i in range(len(pts) - 1):
        x1, y1 = pts[i]
        x2, y2 = pts[i + 1]
        dx, dy = x2 - x1, y2 - y1
        L = math.hypot(dx, dy)
        if L < 1e-6:
            continue
        nx, ny = -dy / L, dx / L
        for side in (1, -1):
            iax, iay = x1 + nx * inner * side, y1 + ny * inner * side
            ibx, iby = x2 + nx * inner * side, y2 + ny * inner * side
            oax, oay = x1 + nx * outer * side, y1 + ny * outer * side
            obx, oby = x2 + nx * outer * side, y2 + ny * outer * side
            try:
                # cara superior de la vereda
                a = bm.verts.new((iax, iay, top))
                b = bm.verts.new((oax, oay, top))
                c = bm.verts.new((obx, oby, top))
                d = bm.verts.new((ibx, iby, top))
                bm.faces.new((a, b, c, d))
                # cara vertical del cordon (borde interno, de z a top)
                e = bm.verts.new((iax, iay, z))
                f = bm.verts.new((ibx, iby, z))
                bm.faces.new((e, f, d, a))
            except ValueError:
                pass


def add_road_strip(bm, path, width, z):
    """Agrega una calle como tira de quads a lo largo de la polilinea."""
    hw = width / 2.0
    pts = dedupe_ring(path, closed=False)
    for i in range(len(pts) - 1):
        x1, y1 = pts[i]
        x2, y2 = pts[i + 1]
        dx, dy = x2 - x1, y2 - y1
        L = math.hypot(dx, dy)
        if L < 1e-6:
            continue
        nx, ny = -dy / L * hw, dx / L * hw
        try:
            v1 = bm.verts.new((x1 + nx, y1 + ny, z))
            v2 = bm.verts.new((x1 - nx, y1 - ny, z))
            v3 = bm.verts.new((x2 - nx, y2 - ny, z))
            v4 = bm.verts.new((x2 + nx, y2 + ny, z))
            bm.faces.new((v1, v2, v3, v4))
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# Materiales / objetos
# ---------------------------------------------------------------------------

def make_material(name, rgb, roughness=0.85, metallic=0.0, specular=0.15, coat=0.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
        if "Roughness" in bsdf.inputs:
            bsdf.inputs["Roughness"].default_value = roughness
        if "Metallic" in bsdf.inputs:
            bsdf.inputs["Metallic"].default_value = metallic
        for spec in ("Specular IOR Level", "Specular"):
            if spec in bsdf.inputs:
                bsdf.inputs[spec].default_value = specular
                break
        if coat > 0:  # clearcoat (pintura de auto: reflejo glossy del cielo)
            for ck in ("Coat Weight", "Coat"):
                if ck in bsdf.inputs:
                    bsdf.inputs[ck].default_value = coat
                    break
            if "Coat Roughness" in bsdf.inputs:
                bsdf.inputs["Coat Roughness"].default_value = 0.05
    else:
        mat.diffuse_color = (rgb[0], rgb[1], rgb[2], 1.0)
    return mat


def make_windowed_material(name, wall_rgb):
    """Material de fachada con ventanas procedurales (grilla de vidrio en la pared).
    Usa coordenadas del mundo (metros) para que las ventanas queden a escala real."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    if not bsdf:
        return mat
    try:
        geom = nt.nodes.new("ShaderNodeNewGeometry")
        sep = nt.nodes.new("ShaderNodeSeparateXYZ")
        addn = nt.nodes.new("ShaderNodeMath"); addn.operation = "ADD"
        comb = nt.nodes.new("ShaderNodeCombineXYZ")
        brick = nt.nodes.new("ShaderNodeTexBrick")
        # Coordenada de fachada: X_horizontal = X+Y (avanza sobre cualquier pared),
        # Y_vertical = Z. Asi el patron es una grilla en la pared, no rayas.
        nt.links.new(geom.outputs["Position"], sep.inputs["Vector"])
        nt.links.new(sep.outputs["X"], addn.inputs[0])
        nt.links.new(sep.outputs["Y"], addn.inputs[1])
        nt.links.new(addn.outputs["Value"], comb.inputs["X"])
        nt.links.new(sep.outputs["Z"], comb.inputs["Y"])
        nt.links.new(comb.outputs["Vector"], brick.inputs["Vector"])
        # ventanas = vidrio oscuro azulado; "mortar" = color de la pared
        brick.inputs["Color1"].default_value = (0.10, 0.13, 0.18, 1.0)
        brick.inputs["Color2"].default_value = (0.14, 0.18, 0.25, 1.0)
        brick.inputs["Mortar"].default_value = (wall_rgb[0], wall_rgb[1], wall_rgb[2], 1.0)
        params = {"Scale": 1.0, "Mortar Size": 0.28, "Mortar Smooth": 0.1,
                  "Bias": 0.0, "Brick Width": 3.4, "Row Height": 3.0}
        for nm, val in params.items():
            if nm in brick.inputs:
                brick.inputs[nm].default_value = val
        # escurrido vertical (grime): rayas oscuras que ensucian la fachada,
        # rompiendo el albedo plano panel-a-panel.
        strk = nt.nodes.new("ShaderNodeTexNoise")
        smap = nt.nodes.new("ShaderNodeMapping")
        smap.inputs["Scale"].default_value = (2.5, 0.05, 1.0)  # estira vertical -> rayas
        if "Scale" in strk.inputs:
            strk.inputs["Scale"].default_value = 5.0
        if "Detail" in strk.inputs:
            strk.inputs["Detail"].default_value = 4.0
        sfac = nt.nodes.new("ShaderNodeMapRange")
        sfac.inputs["To Min"].default_value = 0.0
        sfac.inputs["To Max"].default_value = 0.28
        gmix, gf, gA, gB, gout = _mix_rgb_node(nt)
        gB.default_value = (wall_rgb[0] * 0.45, wall_rgb[1] * 0.45, wall_rgb[2] * 0.42, 1.0)
        nt.links.new(comb.outputs["Vector"], smap.inputs["Vector"])
        nt.links.new(smap.outputs["Vector"], strk.inputs["Vector"])
        nt.links.new(strk.outputs["Fac"], sfac.inputs["Value"])
        nt.links.new(brick.outputs["Color"], gA)
        nt.links.new(sfac.outputs["Result"], gf)
        nt.links.new(gout, bsdf.inputs["Base Color"])
        if "Roughness" in bsdf.inputs:
            bsdf.inputs["Roughness"].default_value = 0.55
        # profundidad: las ventanas se hunden respecto de la pared (bump del grid)
        try:
            bump = nt.nodes.new("ShaderNodeBump")
            bump.inputs["Strength"].default_value = 0.30
            bump.inputs["Distance"].default_value = 0.25
            fout = brick.outputs.get("Fac") or brick.outputs["Color"]
            nt.links.new(fout, bump.inputs["Height"])
            nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
        except Exception:
            pass
    except Exception:
        bsdf.inputs["Base Color"].default_value = (wall_rgb[0], wall_rgb[1], wall_rgb[2], 1.0)
    return mat


def _set_spec(bsdf, val):
    for spec in ("Specular IOR Level", "Specular"):
        if spec in bsdf.inputs:
            bsdf.inputs[spec].default_value = val
            return


def make_glass_material(name, tint, mullion=MULLION_COLOR):
    """Cortina de vidrio reflectante con grilla de perfiles (antepechos horizontales
    + parantes verticales). El vidrio es oscuro y muy poco rugoso: refleja el cielo,
    asi que a plena luz se ve azul/espejado, como las torres de Puerto Madero."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    if not bsdf:
        return mat
    try:
        geom = nt.nodes.new("ShaderNodeNewGeometry")
        sep = nt.nodes.new("ShaderNodeSeparateXYZ")
        addn = nt.nodes.new("ShaderNodeMath"); addn.operation = "ADD"
        comb = nt.nodes.new("ShaderNodeCombineXYZ")
        brick = nt.nodes.new("ShaderNodeTexBrick")
        # Coordenada de fachada: X_horizontal = X+Y, Y_vertical = Z (grilla en la pared)
        nt.links.new(geom.outputs["Position"], sep.inputs["Vector"])
        nt.links.new(sep.outputs["X"], addn.inputs[0])
        nt.links.new(sep.outputs["Y"], addn.inputs[1])
        nt.links.new(addn.outputs["Value"], comb.inputs["X"])
        nt.links.new(sep.outputs["Z"], comb.inputs["Y"])
        nt.links.new(comb.outputs["Vector"], brick.inputs["Vector"])
        t2 = (min(1.0, tint[0] * 1.5 + 0.02), min(1.0, tint[1] * 1.4 + 0.02),
              min(1.0, tint[2] * 1.4 + 0.03))
        brick.inputs["Color1"].default_value = (tint[0], tint[1], tint[2], 1.0)
        brick.inputs["Color2"].default_value = (t2[0], t2[1], t2[2], 1.0)
        brick.inputs["Mortar"].default_value = (mullion[0], mullion[1], mullion[2], 1.0)
        params = {"Scale": 1.0, "Mortar Size": 0.055, "Mortar Smooth": 0.05,
                  "Bias": 0.0, "Brick Width": 1.75, "Row Height": 3.6}
        for nm, val in params.items():
            if nm in brick.inputs:
                brick.inputs[nm].default_value = val
        nt.links.new(brick.outputs["Color"], bsdf.inputs["Base Color"])
        # perfiles recesados (mullions con profundidad) via bump del grid
        gbump = nt.nodes.new("ShaderNodeBump")
        gbump.inputs["Strength"].default_value = 0.4
        gbump.inputs["Distance"].default_value = 0.15
        fout = brick.outputs.get("Fac") or brick.outputs["Color"]
        nt.links.new(fout, gbump.inputs["Height"])
        nt.links.new(gbump.outputs["Normal"], bsdf.inputs["Normal"])
        # variacion por panel: rugosidad distinta panel a panel (algunos brillan mas)
        vor = nt.nodes.new("ShaderNodeTexVoronoi")
        if "Scale" in vor.inputs:
            vor.inputs["Scale"].default_value = 0.5
        rr = nt.nodes.new("ShaderNodeMapRange")
        rr.inputs["To Min"].default_value = 0.02
        rr.inputs["To Max"].default_value = 0.11
        nt.links.new(comb.outputs["Vector"], vor.inputs["Vector"])
        vd = vor.outputs.get("Distance") or vor.outputs[0]
        nt.links.new(vd, rr.inputs["Value"])
        if "Roughness" in bsdf.inputs:
            nt.links.new(rr.outputs["Result"], bsdf.inputs["Roughness"])
    except Exception:
        bsdf.inputs["Base Color"].default_value = (tint[0], tint[1], tint[2], 1.0)
        if "Roughness" in bsdf.inputs:
            bsdf.inputs["Roughness"].default_value = 0.05
    if "Metallic" in bsdf.inputs:
        bsdf.inputs["Metallic"].default_value = 0.0
    _set_spec(bsdf, 0.7)
    return mat


def make_water_material(name, tint=WATER_COLOR):
    """Agua realista: oscura y muy poco rugosa (refleja el cielo) con micro-oleaje
    por bump de ruido. En los diques de Puerto Madero se ve azul espejado."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    if not bsdf:
        return mat
    bsdf.inputs["Base Color"].default_value = (tint[0], tint[1], tint[2], 1.0)
    if "Roughness" in bsdf.inputs:
        bsdf.inputs["Roughness"].default_value = 0.05
    if "Metallic" in bsdf.inputs:
        bsdf.inputs["Metallic"].default_value = 0.0
    _set_spec(bsdf, 0.7)
    try:
        noise = nt.nodes.new("ShaderNodeTexNoise")
        bump = nt.nodes.new("ShaderNodeBump")
        geom = nt.nodes.new("ShaderNodeNewGeometry")
        mapping = nt.nodes.new("ShaderNodeMapping")
        mapping.inputs["Scale"].default_value = (0.35, 0.35, 0.35)
        nt.links.new(geom.outputs["Position"], mapping.inputs["Vector"])
        nt.links.new(mapping.outputs["Vector"], noise.inputs["Vector"])
        if "Scale" in noise.inputs:
            noise.inputs["Scale"].default_value = 2.5
        if "Detail" in noise.inputs:
            noise.inputs["Detail"].default_value = 3.0
        nt.links.new(noise.outputs["Fac"], bump.inputs["Height"])
        bump.inputs["Strength"].default_value = 0.10
        nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    except Exception:
        pass
    return mat


def make_foliage_material(name, base=(0.18, 0.29, 0.14)):
    """Follaje con variacion de verde por ruido (no un verde plano de plastico).
    Mezcla un verde oscuro y uno claro segun un Noise, con algo de rugosidad."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    if not bsdf:
        return mat
    try:
        geom = nt.nodes.new("ShaderNodeNewGeometry")
        mapping = nt.nodes.new("ShaderNodeMapping")
        mapping.inputs["Scale"].default_value = (0.5, 0.5, 0.5)
        noise = nt.nodes.new("ShaderNodeTexNoise")
        if "Scale" in noise.inputs:
            noise.inputs["Scale"].default_value = 3.5
        if "Detail" in noise.inputs:
            noise.inputs["Detail"].default_value = 6.0
        ramp = nt.nodes.new("ShaderNodeValToRGB")
        e = ramp.color_ramp.elements
        e[0].position = 0.35
        e[0].color = (base[0] * 0.55, base[1] * 0.6, base[2] * 0.5, 1.0)
        e[1].position = 0.78
        e[1].color = (min(1, base[0] * 1.6 + 0.05), min(1, base[1] * 1.4 + 0.07),
                      min(1, base[2] * 1.2 + 0.02), 1.0)
        nt.links.new(geom.outputs["Position"], mapping.inputs["Vector"])
        nt.links.new(mapping.outputs["Vector"], noise.inputs["Vector"])
        nt.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
        nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
        # micro-relieve suave de follaje (bump) para romper el sheen plano
        # (sin alpha: los huecos revelaban el interior oscuro y se veia moteado)
        fnoise = nt.nodes.new("ShaderNodeTexNoise")
        if "Scale" in fnoise.inputs:
            fnoise.inputs["Scale"].default_value = 9.0
        if "Detail" in fnoise.inputs:
            fnoise.inputs["Detail"].default_value = 6.0
        fbump = nt.nodes.new("ShaderNodeBump")
        fbump.inputs["Strength"].default_value = 0.18
        nt.links.new(mapping.outputs["Vector"], fnoise.inputs["Vector"])
        nt.links.new(fnoise.outputs["Fac"], fbump.inputs["Height"])
        nt.links.new(fbump.outputs["Normal"], bsdf.inputs["Normal"])
    except Exception:
        bsdf.inputs["Base Color"].default_value = (base[0], base[1], base[2], 1.0)
    if "Roughness" in bsdf.inputs:
        bsdf.inputs["Roughness"].default_value = 0.5   # brillo ceroso de hoja
    # Translucidez: las hojas dejan pasar algo de luz (glow retroiluminado),
    # el mayor delator "plastico" del follaje si falta.
    if "Subsurface Weight" in bsdf.inputs:
        bsdf.inputs["Subsurface Weight"].default_value = 0.18
        if "Subsurface Radius" in bsdf.inputs:
            bsdf.inputs["Subsurface Radius"].default_value = (0.30, 0.55, 0.20)
    return mat


def make_leafcard_material(name, base=(0.20, 0.30, 0.15)):
    """Follaje tipo leaf-card: verde con variacion + ALPHA irregular (huecos de
    cielo, silueta rota) + translucidez. Para el shell de cards de la copa."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    if not bsdf:
        return mat
    try:
        geom = nt.nodes.new("ShaderNodeNewGeometry")
        mapping = nt.nodes.new("ShaderNodeMapping")
        mapping.inputs["Scale"].default_value = (0.6, 0.6, 0.6)
        noise = nt.nodes.new("ShaderNodeTexNoise")
        if "Scale" in noise.inputs:
            noise.inputs["Scale"].default_value = 4.0
        if "Detail" in noise.inputs:
            noise.inputs["Detail"].default_value = 6.0
        ramp = nt.nodes.new("ShaderNodeValToRGB")
        e = ramp.color_ramp.elements
        e[0].position = 0.35
        e[0].color = (base[0] * 0.5, base[1] * 0.55, base[2] * 0.45, 1.0)
        e[1].position = 0.80
        e[1].color = (min(1, base[0] * 1.7 + 0.06), min(1, base[1] * 1.4 + 0.08),
                      min(1, base[2] * 1.2 + 0.02), 1.0)
        nt.links.new(geom.outputs["Position"], mapping.inputs["Vector"])
        nt.links.new(mapping.outputs["Vector"], noise.inputs["Vector"])
        nt.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
        nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
        # ALPHA irregular -> rompe la silueta y deja ver cielo entre las hojas
        anoise = nt.nodes.new("ShaderNodeTexNoise")
        if "Scale" in anoise.inputs:
            anoise.inputs["Scale"].default_value = 7.0
        if "Detail" in anoise.inputs:
            anoise.inputs["Detail"].default_value = 8.0
        aramp = nt.nodes.new("ShaderNodeValToRGB")
        ae = aramp.color_ramp.elements
        ae[0].position = 0.44
        ae[0].color = (0.0, 0.0, 0.0, 1.0)
        ae[1].position = 0.56
        ae[1].color = (1.0, 1.0, 1.0, 1.0)
        nt.links.new(mapping.outputs["Vector"], anoise.inputs["Vector"])
        nt.links.new(anoise.outputs["Fac"], aramp.inputs["Fac"])
        if "Alpha" in bsdf.inputs:
            nt.links.new(aramp.outputs["Color"], bsdf.inputs["Alpha"])
    except Exception:
        bsdf.inputs["Base Color"].default_value = (base[0], base[1], base[2], 1.0)
    if "Roughness" in bsdf.inputs:
        bsdf.inputs["Roughness"].default_value = 0.55
    if "Subsurface Weight" in bsdf.inputs:
        bsdf.inputs["Subsurface Weight"].default_value = 0.2
        if "Subsurface Radius" in bsdf.inputs:
            bsdf.inputs["Subsurface Radius"].default_value = (0.30, 0.55, 0.20)
    return mat


def make_asphalt_material(name, base=ASPHALT_COLOR):
    """Asfalto procedural: variacion de color (parches de alquitran), grano fino
    de bump y rugosidad variable. Rompe el gris plano plastico."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    if not bsdf:
        return mat
    try:
        geo = nt.nodes.new("ShaderNodeNewGeometry")
        mp = nt.nodes.new("ShaderNodeMapping")
        mp.inputs["Scale"].default_value = (0.25, 0.25, 0.25)
        n1 = nt.nodes.new("ShaderNodeTexNoise")
        for k, v in (("Scale", 1.4), ("Detail", 4.0)):
            if k in n1.inputs:
                n1.inputs[k].default_value = v
        ramp = nt.nodes.new("ShaderNodeValToRGB")
        e = ramp.color_ramp.elements
        e[0].position = 0.30
        e[0].color = (base[0] * 0.55, base[1] * 0.55, base[2] * 0.55, 1.0)
        e[1].position = 0.75
        e[1].color = (min(1, base[0] * 1.35 + 0.01), min(1, base[1] * 1.35 + 0.01),
                      min(1, base[2] * 1.35 + 0.02), 1.0)
        n2 = nt.nodes.new("ShaderNodeTexNoise")
        for k, v in (("Scale", 45.0), ("Detail", 6.0)):
            if k in n2.inputs:
                n2.inputs[k].default_value = v
        bump = nt.nodes.new("ShaderNodeBump")
        bump.inputs["Strength"].default_value = 0.14
        rr = nt.nodes.new("ShaderNodeMapRange")
        rr.inputs["To Min"].default_value = 0.72
        rr.inputs["To Max"].default_value = 0.95
        nt.links.new(geo.outputs["Position"], mp.inputs["Vector"])
        nt.links.new(mp.outputs["Vector"], n1.inputs["Vector"])
        nt.links.new(mp.outputs["Vector"], n2.inputs["Vector"])
        nt.links.new(n1.outputs["Fac"], ramp.inputs["Fac"])
        nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
        nt.links.new(n2.outputs["Fac"], bump.inputs["Height"])
        nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
        nt.links.new(n1.outputs["Fac"], rr.inputs["Value"])
        if "Roughness" in bsdf.inputs:
            nt.links.new(rr.outputs["Result"], bsdf.inputs["Roughness"])
    except Exception:
        bsdf.inputs["Base Color"].default_value = (base[0], base[1], base[2], 1.0)
        if "Roughness" in bsdf.inputs:
            bsdf.inputs["Roughness"].default_value = 0.85
    _set_spec(bsdf, 0.2)
    return mat


def make_concrete_material(name, base=SIDEWALK_COLOR):
    """Hormigon procedural (vereda/plaza): paneles por Voronoi + grano de bump +
    variacion de color. Rompe el plano gris uniforme."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    if not bsdf:
        return mat
    try:
        geo = nt.nodes.new("ShaderNodeNewGeometry")
        mp = nt.nodes.new("ShaderNodeMapping")
        mp.inputs["Scale"].default_value = (0.18, 0.18, 0.18)
        vor = nt.nodes.new("ShaderNodeTexVoronoi")
        if "Scale" in vor.inputs:
            vor.inputs["Scale"].default_value = 0.5
        ng = nt.nodes.new("ShaderNodeTexNoise")
        for k, v in (("Scale", 8.0), ("Detail", 5.0)):
            if k in ng.inputs:
                ng.inputs[k].default_value = v
        mixn, f_in, cA, cB, out = _mix_rgb_node(nt)
        cA.default_value = (base[0] * 0.82, base[1] * 0.82, base[2] * 0.8, 1.0)
        cB.default_value = (min(1, base[0] * 1.1), min(1, base[1] * 1.1),
                            min(1, base[2] * 1.1), 1.0)
        bump = nt.nodes.new("ShaderNodeBump")
        bump.inputs["Strength"].default_value = 0.06
        nt.links.new(geo.outputs["Position"], mp.inputs["Vector"])
        nt.links.new(mp.outputs["Vector"], vor.inputs["Vector"])
        nt.links.new(mp.outputs["Vector"], ng.inputs["Vector"])
        vout = vor.outputs.get("Distance") or vor.outputs[0]
        nt.links.new(vout, f_in)
        nt.links.new(out, bsdf.inputs["Base Color"])
        nt.links.new(ng.outputs["Fac"], bump.inputs["Height"])
        nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    except Exception:
        bsdf.inputs["Base Color"].default_value = (base[0], base[1], base[2], 1.0)
    if "Roughness" in bsdf.inputs:
        bsdf.inputs["Roughness"].default_value = 0.9
    _set_spec(bsdf, 0.1)
    return mat


def apply_street_view_settings(scn=None):
    """Tonemapping fotografico (AgX + look punchy + exposicion) para que la vista a
    nivel de calle con cielo fisico tenga contraste y cielo azul, no lavado."""
    scn = scn or bpy.context.scene
    vs = scn.view_settings
    for vt in ("AgX", "Filmic", "Standard"):
        try:
            vs.view_transform = vt
            break
        except TypeError:
            continue
    for lk in STREET_LOOKS:
        try:
            vs.look = lk
            break
        except TypeError:
            continue
    try:
        vs.exposure = STREET_EXPOSURE_HDRI if using_hdri() else STREET_EXPOSURE
    except Exception:
        pass


def setup_compositor(scn=None, haze_color=(0.60, 0.70, 0.84), haze_start=70.0,
                     haze_end=700.0, haze_strength=0.55, vignette=0.20):
    """Post en el compositor: bruma de distancia (aerial perspective) por el pase
    de profundidad + una vineta suave. Le da caracter fotografico y hace que las
    torres/arboles lejanos se desvanezcan en azul (no se corten planos)."""
    scn = scn or bpy.context.scene
    try:
        scn.use_nodes = True
        nt = scn.node_tree
        for n in list(nt.nodes):
            nt.nodes.remove(n)
        rl = nt.nodes.new("CompositorNodeRLayers")
        comp = nt.nodes.new("CompositorNodeComposite")
        last = rl.outputs["Image"]
        depth = rl.outputs.get("Depth") or rl.outputs.get("Z")
        if depth is not None and haze_strength > 0:
            mr = nt.nodes.new("CompositorNodeMapRange")
            mr.inputs["From Min"].default_value = haze_start
            mr.inputs["From Max"].default_value = haze_end
            mr.inputs["To Min"].default_value = 0.0
            mr.inputs["To Max"].default_value = haze_strength
            try:
                mr.use_clamp = True
            except Exception:
                pass
            nt.links.new(depth, mr.inputs["Value"])
            mix = nt.nodes.new("CompositorNodeMixRGB")
            mix.blend_type = "MIX"
            mix.inputs[2].default_value = (haze_color[0], haze_color[1], haze_color[2], 1.0)
            nt.links.new(mr.outputs["Value"], mix.inputs[0])
            nt.links.new(last, mix.inputs[1])
            last = mix.outputs["Image"]
        if vignette > 0:
            mask = nt.nodes.new("CompositorNodeEllipseMask")
            try:
                mask.width = 1.15
                mask.height = 1.15
            except Exception:
                pass
            blur = nt.nodes.new("CompositorNodeBlur")
            try:
                blur.size_x = 160
                blur.size_y = 160
                blur.use_relative = False
            except Exception:
                pass
            nt.links.new(mask.outputs[0], blur.inputs["Image"])
            vmix = nt.nodes.new("CompositorNodeMixRGB")
            vmix.blend_type = "MULTIPLY"
            vmix.inputs[0].default_value = vignette
            nt.links.new(last, vmix.inputs[1])
            nt.links.new(blur.outputs["Image"], vmix.inputs[2])
            last = vmix.outputs["Image"]
        # Glare (bloom) sutil en los highlights brillantes (vidrio/cielo) -> foto
        try:
            glare = nt.nodes.new("CompositorNodeGlare")
            for gt in ("BLOOM", "FOG_GLOW"):
                try:
                    glare.glare_type = gt
                    break
                except Exception:
                    continue
            try:
                glare.mix = -0.72
            except Exception:
                pass
            try:
                glare.threshold = 1.0
            except Exception:
                pass
            nt.links.new(last, glare.inputs["Image"])
            last = glare.outputs["Image"]
        except Exception:
            pass
        nt.links.new(last, comp.inputs["Image"])
    except Exception:
        try:
            for n in list(nt.nodes):
                nt.nodes.remove(n)
            rl = nt.nodes.new("CompositorNodeRLayers")
            comp = nt.nodes.new("CompositorNodeComposite")
            nt.links.new(rl.outputs["Image"], comp.inputs["Image"])
        except Exception:
            scn.use_nodes = False


def make_textured_material(name, image_path, tile_m=4.0, roughness=0.9):
    """Material con textura de imagen tileada a escala real (Position del mundo)."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    if not bsdf:
        return mat
    try:
        img = bpy.data.images.load(image_path, check_existing=True)
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = img
        tex.extension = "REPEAT"
        geom = nt.nodes.new("ShaderNodeNewGeometry")
        mapping = nt.nodes.new("ShaderNodeMapping")
        s = 1.0 / max(0.5, tile_m)
        mapping.inputs["Scale"].default_value = (s, s, s)
        nt.links.new(geom.outputs["Position"], mapping.inputs["Vector"])
        nt.links.new(mapping.outputs["Vector"], tex.inputs["Vector"])
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        if "Roughness" in bsdf.inputs:
            bsdf.inputs["Roughness"].default_value = roughness
    except Exception:
        bsdf.inputs["Base Color"].default_value = (0.5, 0.5, 0.5, 1.0)
    return mat


def make_pbr_material(name, base, tile_m=6.0, rough_boost=1.0):
    """Material PBR real desde un set de texturas CC0 <base>_diff/_rough/_nor
    (color + rugosidad + normal), tileado a escala real por Position del mundo.
    Devuelve None si no hay texturas (para caer a un material procedural)."""
    ts = _texset(base)
    if not ts:
        return None
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    if not bsdf:
        return mat
    try:
        geo = nt.nodes.new("ShaderNodeNewGeometry")
        mp = nt.nodes.new("ShaderNodeMapping")
        s = 1.0 / max(0.25, tile_m)
        mp.inputs["Scale"].default_value = (s, s, s)
        nt.links.new(geo.outputs["Position"], mp.inputs["Vector"])

        def img(path, colorspace):
            t = nt.nodes.new("ShaderNodeTexImage")
            t.image = bpy.data.images.load(path, check_existing=True)
            t.extension = "REPEAT"
            try:
                t.image.colorspace_settings.name = colorspace
            except Exception:
                pass
            nt.links.new(mp.outputs["Vector"], t.inputs["Vector"])
            return t

        d = img(ts["diff"], "sRGB")
        nt.links.new(d.outputs["Color"], bsdf.inputs["Base Color"])
        if ts.get("rough") and "Roughness" in bsdf.inputs:
            r = img(ts["rough"], "Non-Color")
            if rough_boost != 1.0:
                mul = nt.nodes.new("ShaderNodeMath")
                mul.operation = "MULTIPLY"
                mul.inputs[1].default_value = rough_boost
                nt.links.new(r.outputs["Color"], mul.inputs[0])
                nt.links.new(mul.outputs["Value"], bsdf.inputs["Roughness"])
            else:
                nt.links.new(r.outputs["Color"], bsdf.inputs["Roughness"])
        if ts.get("nor"):
            n = img(ts["nor"], "Non-Color")
            nmap = nt.nodes.new("ShaderNodeNormalMap")
            nt.links.new(n.outputs["Color"], nmap.inputs["Color"])
            nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
    except Exception:
        bsdf.inputs["Base Color"].default_value = (0.5, 0.5, 0.5, 1.0)
    return mat


def bm_to_object(bm, name, mat, coll=None, merge=0.01, smooth=False):
    """Convierte un bmesh en objeto, limpiando geometria (fusiona vertices
    duplicados y recalcula normales) y lo cuelga de la coleccion `coll`
    (o de la master si es None). smooth=True aplica sombreado suave (para copas
    de arboles, autos: evita el look facetado sin sumar poligonos)."""
    if merge and len(bm.verts) > 0:
        try:
            bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=merge)
        except Exception:
            pass
    try:
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    except Exception:
        pass
    me = bpy.data.meshes.new(name)
    bm.normal_update()
    bm.to_mesh(me)
    bm.free()
    if smooth:
        for p in me.polygons:
            p.use_smooth = True
    ob = bpy.data.objects.new(name, me)
    if mat:
        ob.data.materials.append(mat)
    (coll or bpy.context.scene.collection).objects.link(ob)
    return ob


# Nombres de colecciones del spec world-to-blender.
COLLECTION_NAMES = [
    "00_REFERENCE", "01_TERRAIN", "02_BUILDINGS", "03_ROADS", "04_SIDEWALKS",
    "05_VEGETATION", "06_WATER", "07_LANDMARKS", "08_BRIDGES", "09_RAILWAYS",
    "10_STREET_FURNITURE", "11_LIGHTING", "12_CAMERAS", "13_EXPORT",
]


def clear_scene():
    """Vacia la escena SIN `read_factory_settings` (que bajo blender-mcp resetea
    Blender y desregistra el addon, matando el servidor MCP). Borra objetos y
    datos, dejando el addon/servidor intactos. Usar siempre en el Blender vivo."""
    for ob in list(bpy.data.objects):
        try:
            bpy.data.objects.remove(ob, do_unlink=True)
        except Exception:
            pass
    for coll in list(bpy.data.collections):
        try:
            bpy.data.collections.remove(coll)
        except Exception:
            pass
    for block in (bpy.data.meshes, bpy.data.materials, bpy.data.lights,
                  bpy.data.cameras, bpy.data.images, bpy.data.worlds,
                  bpy.data.curves, bpy.data.node_groups):
        for b in list(block):
            if getattr(b, "users", 0) == 0:
                try:
                    block.remove(b)
                except Exception:
                    pass


def make_collections():
    """Crea (o devuelve) las colecciones nombradas del spec, colgadas de la escena.
    Devuelve un dict nombre -> Collection. Reutilizable en headless y en MCP vivo."""
    master = bpy.context.scene.collection
    existing = {ch.name for ch in master.children}
    out = {}
    for n in COLLECTION_NAMES:
        c = bpy.data.collections.get(n) or bpy.data.collections.new(n)
        if n not in existing:
            try:
                master.children.link(c)
            except Exception:
                pass
        out[n] = c
    return out


def color_key(rgb):
    return (round(rgb[0], 3), round(rgb[1], 3), round(rgb[2], 3))


# ---------------------------------------------------------------------------
# Construccion de la escena
# ---------------------------------------------------------------------------

def build_scene(scene):
    buildings = scene.get("buildings", [])
    roads = scene.get("roads", [])
    areas = scene.get("areas", [])
    C = make_collections()

    # --- Edificios: paredes agrupadas por color + techos (dos aguas / azotea) ---
    def _bevel(bm):
        if BEVEL > 0:
            try:
                bmesh.ops.bevel(bm, geom=list(bm.edges) + list(bm.verts),
                                offset=BEVEL, segments=1, affect="EDGES",
                                clamp_overlap=True)
            except Exception:
                pass

    # Clasificar: torres/edificios altos = cortina de vidrio reflectante;
    # el resto = mamposteria con ventanas; los landmarks = agujas.
    glass_groups = {}    # tint_idx -> [b]
    masonry_groups = {}  # color_key -> [b]
    spires = []
    for b in buildings:
        base = float(b.get("min_height", 0.0))
        top = float(b["height"])
        if b.get("type") in LANDMARK_TYPES:
            spires.append(b)
        elif (top - base) >= MODERN_MIN_HEIGHT:
            fp = b["footprint"][0]  # tono de vidrio estable por posicion
            idx = int(abs(round(fp[0] * 3.1 + fp[1] * 5.7))) % len(GLASS_TINTS)
            glass_groups.setdefault(idx, []).append(b)
        else:
            masonry_groups.setdefault(color_key(b["color"]), []).append(b)

    roof_house_bm = bmesh.new()  # techos de teja (casas bajas)
    roof_flat_bm = bmesh.new()   # detalle de azotea (torres)

    # Torres de vidrio
    for idx, items in glass_groups.items():
        bm = bmesh.new()
        for b in items:
            base = float(b.get("min_height", 0.0))
            top = float(b["height"])
            add_prism(bm, b["footprint"], base, top)
            add_rooftop_detail(roof_flat_bm, b["footprint"], top)
        _bevel(bm)
        bm_to_object(bm, f"Torre_vidrio_{idx}",
                     make_glass_material(f"glass_{idx}", GLASS_TINTS[idx]),
                     coll=C["02_BUILDINGS"])

    # Mamposteria (fachada con ventanas) + techos
    for i, (k, items) in enumerate(masonry_groups.items()):
        bm = bmesh.new()
        for b in items:
            base = float(b.get("min_height", 0.0))
            top = float(b["height"])
            add_prism(bm, b["footprint"], base, top)
            if (top - base) <= ROOF_SMALL_MAX_H:
                add_hip_roof(roof_house_bm, b["footprint"], top)
            else:
                add_rooftop_detail(roof_flat_bm, b["footprint"], top)
        _bevel(bm)
        bm_to_object(bm, f"Edificio_{i}", make_windowed_material(f"bld_{i}", k),
                     coll=C["02_BUILDINGS"])

    C = make_collections()

    # Landmarks verticales (obeliscos/agujas): piedra clara -> 07
    if spires:
        bm = bmesh.new()
        for b in spires:
            add_spire(bm, b["footprint"], float(b.get("min_height", 0.0)),
                      float(b["height"]))
        bm_to_object(bm, "Landmark_agujas",
                     make_material("landmark", (0.86, 0.84, 0.80),
                                   roughness=0.6, specular=0.2),
                     coll=C["07_LANDMARKS"])
    if len(roof_house_bm.faces) > 0:
        bm_to_object(roof_house_bm, "Techos_teja",
                     make_material("techo_teja", ROOF_HOUSE_COLOR, roughness=0.7, specular=0.15),
                     coll=C["02_BUILDINGS"])
    else:
        roof_house_bm.free()
    if len(roof_flat_bm.faces) > 0:
        bm_to_object(roof_flat_bm, "Azoteas",
                     make_material("azotea", ROOF_FLAT_COLOR, roughness=0.85, specular=0.1),
                     coll=C["02_BUILDINGS"])
    else:
        roof_flat_bm.free()

    # --- Areas de agua (-> 06) y verde (-> 05) ---
    area_groups = {}
    for a in areas:
        k = color_key(a["color"])
        area_groups.setdefault(k, []).append(a)
    for i, (k, items) in enumerate(area_groups.items()):
        bm = bmesh.new()
        for a in items:
            add_flat_polygon(bm, a["polygon"], float(a.get("z", 0.03)))
        is_water = k[2] > k[1]  # azulado
        if is_water:
            bm_to_object(bm, f"Agua_{i}", make_water_material(f"agua_{i}"),
                         coll=C["06_WATER"], merge=0.0)
        else:
            bm_to_object(bm, f"Verde_{i}",
                         make_material(f"verde_{i}", k, roughness=0.9, specular=0.1),
                         coll=C["05_VEGETATION"], merge=0.0)

    # --- Calles: calzada+lineas -> 03, veredas -> 04, rieles -> 09, puentes -> 08 ---
    veh_bm = bmesh.new()      # calzada asfaltada
    mark_bm = bmesh.new()     # lineas de eje (blanco)
    side_bm = bmesh.new()     # veredas de hormigon
    ped_groups = {}           # peatonal / footway
    rail_groups = {}          # vias de tren
    bridge_items = []         # puentes elevados
    for r in roads:
        k = color_key(r["color"])
        z = float(r.get("z", 0.06))
        p0 = r["path"][0]
        jitter = 0.001 * (int(abs(p0[0] * 5.1 + p0[1] * 9.7)) % 40)
        width = float(r["width"])
        rtype = str(r.get("type", ""))
        if z > 1.0:
            bridge_items.append((r, z + jitter))
            continue
        if rtype.startswith("rail"):
            rail_groups.setdefault(k, []).append((r, z + jitter))
            continue
        greyish = (max(k) - min(k) < 0.06 and sum(k) / 3 < 0.4)
        if greyish and width >= 4.5:   # vehicular
            add_road_strip(veh_bm, r["path"], width, z + jitter)
            if width >= 7.0:
                add_lane_markings(mark_bm, r["path"], width, z + jitter)
                add_sidewalks(side_bm, r["path"], width, 0.05)
        else:                           # peatonal / vereda / ciclovia
            ped_groups.setdefault(k, []).append((r, z + jitter))
    if len(side_bm.faces) > 0:
        bm_to_object(side_bm, "Veredas",
                     make_pbr_material("vereda", "concrete", tile_m=4.0)
                     or make_concrete_material("vereda", SIDEWALK_COLOR),
                     coll=C["04_SIDEWALKS"], smooth=False)
    else:
        side_bm.free()
    if len(veh_bm.faces) > 0:
        bm_to_object(veh_bm, "Calzada",
                     make_pbr_material("asfalto", "asphalt", tile_m=8.0)
                     or make_asphalt_material("asfalto"),
                     coll=C["03_ROADS"])
    else:
        veh_bm.free()
    if len(mark_bm.faces) > 0:
        bm_to_object(mark_bm, "Lineas_viales",
                     make_material("lineas", MARKING_COLOR, roughness=0.7, specular=0.1),
                     coll=C["03_ROADS"])
    else:
        mark_bm.free()
    for i, (k, items) in enumerate(ped_groups.items()):
        bm = bmesh.new()
        for r, zz in items:
            add_road_strip(bm, r["path"], float(r["width"]), zz)
        bm_to_object(bm, f"Peatonal_{i}",
                     make_material(f"peatonal_{i}", k, roughness=0.9, specular=0.06),
                     coll=C["04_SIDEWALKS"])
    for i, (k, items) in enumerate(rail_groups.items()):
        bm = bmesh.new()
        for r, zz in items:
            add_road_strip(bm, r["path"], float(r["width"]), zz)
        bm_to_object(bm, f"Vias_{i}",
                     make_material(f"via_{i}", k, roughness=0.85, specular=0.1),
                     coll=C["09_RAILWAYS"])
    if bridge_items:
        bm = bmesh.new()
        for r, zz in bridge_items:
            add_road_strip(bm, r["path"], float(r["width"]), zz)
            add_bridge_piers(bm, r["path"], zz)
        bm_to_object(bm, "Puentes",
                     make_material("puente", BRIDGE_MAT_COLOR, roughness=0.8, specular=0.1),
                     coll=C["08_BRIDGES"])

    # --- Arboles (plazas + veredas de avenidas) -> 05 ---
    trunk_bm, fol_bm, cards_bm, ntrees = scatter_trees(areas, roads)
    if len(trunk_bm.faces) > 0:
        bm_to_object(trunk_bm, "Troncos",
                     make_pbr_material("corteza", "bark", tile_m=2.2)
                     or make_material("tronco", (0.34, 0.23, 0.15), roughness=0.95),
                     coll=C["05_VEGETATION"])
    else:
        trunk_bm.free()
    if len(fol_bm.faces) > 0:
        bm_to_object(fol_bm, "Copas_nucleo", make_foliage_material("copa"),
                     coll=C["05_VEGETATION"], smooth=True)
    else:
        fol_bm.free()
    if len(cards_bm.faces) > 0:
        bm_to_object(cards_bm, "Hojas", make_leafcard_material("hojas"),
                     coll=C["05_VEGETATION"], merge=0.0)
    else:
        cards_bm.free()

    # --- Farolas -> 10 (street furniture) ---
    lamp_bm = scatter_streetlights(roads)
    if lamp_bm and len(lamp_bm.faces) > 0:
        bm_to_object(lamp_bm, "Farolas",
                     make_material("farola", (0.22, 0.22, 0.24), roughness=0.5, metallic=0.6),
                     coll=C["10_STREET_FURNITURE"])
    elif lamp_bm:
        lamp_bm.free()

    # --- Autos estacionados -> 10 (carroceria por color + vidrios/ruedas oscuros) ---
    car_buckets, car_glass, ncars = scatter_cars(roads)
    for idx, cbm in car_buckets.items():
        if len(cbm.faces) > 0:
            col = CAR_COLORS[idx]
            metal = 0.35 if max(col) > 0.4 else 0.5
            bm_to_object(cbm, f"Autos_carroceria_{idx}",
                         make_material(f"auto_{idx}", col, roughness=0.30,
                                       metallic=metal, specular=0.6, coat=1.0),
                         coll=C["10_STREET_FURNITURE"])
        else:
            cbm.free()
    if car_glass and len(car_glass.faces) > 0:
        bm_to_object(car_glass, "Autos_vidrios",
                     make_material("auto_glass", (0.045, 0.05, 0.07),
                                   roughness=0.18, metallic=0.0, specular=0.6),
                     coll=C["10_STREET_FURNITURE"])
    elif car_glass:
        car_glass.free()

    # --- Bounds / centro ---
    bnd = scene.get("bounds", {})
    minx = bnd.get("minx", -scene.get("radius", 250))
    miny = bnd.get("miny", -scene.get("radius", 250))
    maxx = bnd.get("maxx", scene.get("radius", 250))
    maxy = bnd.get("maxy", scene.get("radius", 250))
    cx = (minx + maxx) / 2.0
    cy = (miny + maxy) / 2.0
    R = max(0.5 * max(maxx - minx, maxy - miny), 50.0)

    # --- Terreno -> 01 ---
    margin = R * 0.15
    bm = bmesh.new()
    v1 = bm.verts.new((minx - margin, miny - margin, 0.0))
    v2 = bm.verts.new((maxx + margin, miny - margin, 0.0))
    v3 = bm.verts.new((maxx + margin, maxy + margin, 0.0))
    v4 = bm.verts.new((minx - margin, maxy + margin, 0.0))
    bm.faces.new((v1, v2, v3, v4))
    piso_mat = (make_pbr_material("Terreno", "concrete", tile_m=8.0)
                or make_concrete_material("Terreno", GROUND_COLOR))
    bm_to_object(bm, "Terreno", piso_mat, coll=C["01_TERRAIN"], merge=0.0)

    # --- Landmark: Puente de la Mujer (si hay pasarela sobre el dique) -> 07 ---
    try:
        add_puente_mujer(scene, C["07_LANDMARKS"])
    except Exception as e:
        print(f"[build] Puente de la Mujer: {e}")

    # --- Marco del area pedida -> 00_REFERENCE ---
    _add_boundary(C["00_REFERENCE"], minx, miny, maxx, maxy)

    return cx, cy, R


# ---------------------------------------------------------------------------
# Luz, mundo, camara, render
# ---------------------------------------------------------------------------

def _mix_rgb_node(nt):
    """Crea un nodo de mezcla RGB robusto entre versiones de Blender.
    Devuelve (node, fac_input, colorA_input, colorB_input, out_socket)."""
    try:
        m = nt.nodes.new("ShaderNodeMixRGB")
        return m, m.inputs["Fac"], m.inputs["Color1"], m.inputs["Color2"], m.outputs["Color"]
    except Exception:
        m = nt.nodes.new("ShaderNodeMix")
        try:
            m.data_type = "RGBA"
        except Exception:
            pass
        # ShaderNodeMix: Factor(float)=inputs[0], A(color)/B(color) por nombre duplicado
        col_ins = [s for s in m.inputs if s.type == "RGBA"]
        out = [s for s in m.outputs if s.type == "RGBA"][0]
        return m, m.inputs[0], col_ins[0], col_ins[1], out


def _add_clouds(nt, sky_node):
    """Nubes cirrus procedurales mezcladas sobre el cielo fisico (solo hemisferio
    superior). Devuelve el socket de color a conectar al Background."""
    try:
        geo = nt.nodes.new("ShaderNodeNewGeometry")
        mapping = nt.nodes.new("ShaderNodeMapping")
        mapping.inputs["Scale"].default_value = (2.0, 2.0, 5.0)
        noise = nt.nodes.new("ShaderNodeTexNoise")
        for nm, val in (("Scale", 2.2), ("Detail", 7.0), ("Roughness", 0.72)):
            if nm in noise.inputs:
                noise.inputs[nm].default_value = val
        cmask = nt.nodes.new("ShaderNodeMapRange")
        cmask.inputs["From Min"].default_value = 0.52
        cmask.inputs["From Max"].default_value = 0.72
        hemi = nt.nodes.new("ShaderNodeSeparateXYZ")
        hmax = nt.nodes.new("ShaderNodeMath"); hmax.operation = "MAXIMUM"
        hmax.inputs[1].default_value = 0.0
        hamp = nt.nodes.new("ShaderNodeMath"); hamp.operation = "MULTIPLY"
        hamp.inputs[1].default_value = 5.0
        fac = nt.nodes.new("ShaderNodeMath"); fac.operation = "MULTIPLY"
        m, f_in, cA, cB, out = _mix_rgb_node(nt)
        cB.default_value = (1.25, 1.27, 1.35, 1.0)   # nube algo brillante
        nt.links.new(geo.outputs["Incoming"], mapping.inputs["Vector"])
        nt.links.new(mapping.outputs["Vector"], noise.inputs["Vector"])
        nt.links.new(noise.outputs["Fac"], cmask.inputs["Value"])
        nt.links.new(geo.outputs["Incoming"], hemi.inputs["Vector"])
        nt.links.new(hemi.outputs["Z"], hmax.inputs[0])
        nt.links.new(hmax.outputs["Value"], hamp.inputs[0])
        nt.links.new(cmask.outputs["Result"], fac.inputs[0])
        nt.links.new(hamp.outputs["Value"], fac.inputs[1])
        nt.links.new(fac.outputs["Value"], f_in)
        nt.links.new(sky_node.outputs[0], cA)
        return out
    except Exception:
        return sky_node.outputs[0]


def setup_world(sky=False, clouds=True):
    """sky=True usa un cielo fisico (Nishita/Scattering) con nubes cirrus
    procedurales (realista, para vistas a nivel de calle y para reflejos en
    vidrio/agua). sky=False deja un cielo plano azul (maqueta aérea)."""
    scn = bpy.context.scene
    world = bpy.data.worlds.new("Cielo")
    scn.world = world
    world.use_nodes = True
    nt = world.node_tree
    bg = nt.nodes.get("Background")
    if not bg:
        return
    # 1) HDRI real (con nubes) -> lo mas foto-real para iluminacion y reflejos
    if sky and using_hdri():
        try:
            env = nt.nodes.new("ShaderNodeTexEnvironment")
            env.image = bpy.data.images.load(HDRI_PATH, check_existing=True)
            tc = nt.nodes.new("ShaderNodeTexCoord")
            mpp = nt.nodes.new("ShaderNodeMapping")
            try:
                mpp.inputs["Rotation"].default_value = (0.0, 0.0, math.radians(HDRI_ROT_DEG))
            except Exception:
                pass
            nt.links.new(tc.outputs["Generated"], mpp.inputs["Vector"])
            nt.links.new(mpp.outputs["Vector"], env.inputs["Vector"])
            nt.links.new(env.outputs["Color"], bg.inputs[0])
            bg.inputs[1].default_value = 1.0
            return
        except Exception:
            pass
    # 2) Cielo fisico Nishita + nubes procedurales
    if sky:
        try:
            sky_node = nt.nodes.new("ShaderNodeTexSky")
            for stype in ("MULTIPLE_SCATTERING", "NISHITA", "SINGLE_SCATTERING",
                          "HOSEK_WILKIE"):
                try:
                    sky_node.sky_type = stype
                    break
                except TypeError:
                    continue
            for attr, val in (("sun_elevation", math.radians(62)),  # mediodia de verano BA
                              ("sun_rotation", math.radians(150)),
                              ("sun_intensity", 1.0),
                              ("sun_size", math.radians(0.7)),
                              ("altitude", 120.0),
                              ("air_density", 1.0),
                              ("dust_density", 0.4)):
                if hasattr(sky_node, attr):
                    try:
                        setattr(sky_node, attr, val)
                    except Exception:
                        pass
            out = _add_clouds(nt, sky_node) if clouds else sky_node.outputs[0]
            nt.links.new(out, bg.inputs[0])
            bg.inputs[1].default_value = 1.0   # cielo fisico a intensidad plena
            return
        except Exception:
            pass
    bg.inputs[0].default_value = (0.62, 0.74, 0.92, 1.0)
    bg.inputs[1].default_value = WORLD_STRENGTH


def setup_sun():
    data = bpy.data.lights.new("Sol", "SUN")
    data.energy = SUN_ENERGY
    try:
        data.angle = 0.09  # sombras suaves
    except Exception:
        pass
    sun = bpy.data.objects.new("Sol", data)
    sun.rotation_euler = (math.radians(52), math.radians(6), math.radians(40))
    bpy.context.scene.collection.objects.link(sun)


def setup_camera(cx, cy, R):
    scn = bpy.context.scene
    cam_data = bpy.data.cameras.new("Camara")
    cam = bpy.data.objects.new("Camara", cam_data)
    scn.collection.objects.link(cam)
    scn.camera = cam

    d = R * CAM_DIST_FACTOR
    elev = math.radians(CAM_ELEV_DEG)
    azim = math.radians(CAM_AZIM_DEG)
    cam.location = (
        cx + d * math.cos(elev) * math.sin(azim),
        cy - d * math.cos(elev) * math.cos(azim),
        d * math.sin(elev),
    )
    cam_data.clip_end = d * 5.0
    try:
        cam_data.sensor_fit = "VERTICAL"
    except Exception:
        pass
    cam_data.angle = 2.0 * math.atan((R * CAM_FRAME_FACTOR) / d)

    target = bpy.data.objects.new("Target", None)
    target.location = (cx, cy, min(R * 0.12, 25.0))
    scn.collection.objects.link(target)
    con = cam.constraints.new("TRACK_TO")
    con.target = target
    con.track_axis = "TRACK_NEGATIVE_Z"
    con.up_axis = "UP_Y"


def setup_streetview_camera(heading_deg=0.0, fov_deg=90.0, height=2.5, cx=0.0, cy=0.0):
    """Camara a nivel de calle en (cx,cy) mirando hacia 'heading' (0=norte, 90=este).

    Sirve para comparar el render contra una imagen de Google Street View tomada
    en el mismo punto y con el mismo rumbo/FOV.
    """
    scn = bpy.context.scene
    cam_data = bpy.data.cameras.new("CamCalle")
    cam = bpy.data.objects.new("CamCalle", cam_data)
    scn.collection.objects.link(cam)
    scn.camera = cam
    cam.location = (cx, cy, height)
    # heading: 0=norte(+Y), 90=este(+X). En Blender la camara mira hacia -Z;
    # con rot X=90 mira al horizonte (+Y = norte). rot Z gira el rumbo (horario).
    cam.rotation_euler = (math.radians(90.0), 0.0, math.radians(-heading_deg))
    cam_data.clip_end = 5000.0
    try:
        cam_data.sensor_fit = "HORIZONTAL"
    except Exception:
        pass
    cam_data.angle = math.radians(fov_deg)
    return cam


def setup_render(render_path):
    scn = bpy.context.scene
    try:
        scn.render.engine = "CYCLES"
        scn.cycles.samples = CYCLES_SAMPLES
        scn.cycles.device = "CPU"
        try:
            scn.cycles.use_denoising = True
            scn.cycles.denoiser = "OPENIMAGEDENOISE"
        except Exception:
            pass
    except Exception:
        scn.render.engine = "BLENDER_EEVEE_NEXT"
    scn.render.resolution_x = RES_X
    scn.render.resolution_y = RES_Y
    scn.render.resolution_percentage = 100
    scn.render.film_transparent = False
    # Colores "planos" y saturados (mejor para un mapa 3D con colores)
    try:
        scn.view_settings.view_transform = "Standard"
        scn.view_settings.exposure = EXPOSURE
    except Exception:
        pass
    scn.render.filepath = render_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    scene_path, blend_path, render_path = parse_args()
    with open(scene_path) as f:
        scene = json.load(f)

    # Escena vacia (sin cubo/camara/luz por defecto)
    bpy.ops.wm.read_factory_settings(use_empty=True)

    cx, cy, R = build_scene(scene)
    setup_world()
    setup_sun()
    setup_camera(cx, cy, R)
    setup_render(render_path)

    n_obj = len(bpy.context.scene.collection.objects)
    print(f"[blender] escena: {len(scene.get('buildings', []))} edificios, "
          f"{len(scene.get('roads', []))} calles, {len(scene.get('areas', []))} areas, "
          f"{n_obj} objetos, radio {R:.0f} m")

    try:
        bpy.ops.wm.save_as_mainfile(filepath=blend_path)
        print(f"[blender] guardado: {blend_path}")
    except Exception as e:
        print(f"[blender] no pude guardar .blend: {e}")

    print("[blender] renderizando...")
    bpy.ops.render.render(write_still=True)
    print(f"[blender] render: {render_path}")


if __name__ == "__main__":
    main()
