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


def _tex(name):
    if TEXTURE_DIR:
        p = os.path.join(TEXTURE_DIR, name)
        if os.path.isfile(p):
            return p
    return None

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
GROUND_COLOR = (0.55, 0.63, 0.44)   # verde/tierra (no gris)
ROOF_HOUSE_COLOR = (0.62, 0.30, 0.24)  # teja terracota
ROOF_FLAT_COLOR = (0.44, 0.45, 0.48)   # azotea gris
ROOF_SMALL_MAX_H = 11.0  # edificios mas bajos que esto -> techo a dos aguas
BEVEL = 0.18             # bisel de aristas de edificios (m)


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


def _icosphere(bm, radius):
    try:
        return bmesh.ops.create_icosphere(bm, subdivisions=1, radius=radius)
    except TypeError:
        return bmesh.ops.create_icosphere(bm, subdivisions=1, diameter=radius * 2)


def add_tree(trunk_bm, fol_bm, x, y, height=6.0):
    """Arbol simple: tronco (caja fina) + copa (icoesfera)."""
    tw = max(0.25, height * 0.05)
    box = [(x - tw, y - tw), (x + tw, y - tw), (x + tw, y + tw), (x - tw, y + tw)]
    add_prism(trunk_bm, box, 0.0, height * 0.5)
    res = _icosphere(fol_bm, radius=height * 0.34)
    verts = [v for v in res["verts"]]
    bmesh.ops.translate(fol_bm, verts=verts, vec=(x, y, height * 0.72))


def scatter_trees(areas, roads, cap=280):
    """Devuelve (trunk_bm, fol_bm) con arboles en plazas y sobre avenidas anchas."""
    trunk_bm = bmesh.new()
    fol_bm = bmesh.new()
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
            add_tree(trunk_bm, fol_bm, cx + jx, cy + jy, 5.5 + (i % 3))
            count += 1

    # arboles de vereda sobre avenidas (calles anchas)
    for r in roads:
        if count >= cap:
            break
        if float(r.get("width", 0)) < 7.0 or r.get("z", 0) > 1:
            continue
        pts = dedupe_ring(r["path"], closed=False)
        for i in range(len(pts) - 1):
            x1, y1 = pts[i]
            x2, y2 = pts[i + 1]
            dx, dy = x2 - x1, y2 - y1
            L = math.hypot(dx, dy)
            if L < 8:
                continue
            nx, ny = -dy / L, dx / L
            off = float(r["width"]) / 2 + 2.5
            for side in (1, -1):
                if count >= cap:
                    break
                mx = (x1 + x2) / 2 + nx * off * side
                my = (y1 + y2) / 2 + ny * off * side
                add_tree(trunk_bm, fol_bm, mx, my, 6.0)
                count += 1
    return trunk_bm, fol_bm, count


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

def make_material(name, rgb, roughness=0.85, metallic=0.0, specular=0.15):
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
        nt.links.new(brick.outputs["Color"], bsdf.inputs["Base Color"])
        if "Roughness" in bsdf.inputs:
            bsdf.inputs["Roughness"].default_value = 0.6
    except Exception:
        bsdf.inputs["Base Color"].default_value = (wall_rgb[0], wall_rgb[1], wall_rgb[2], 1.0)
    return mat


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


def bm_to_object(bm, name, mat):
    try:
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    except Exception:
        pass
    me = bpy.data.meshes.new(name)
    bm.normal_update()
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    if mat:
        ob.data.materials.append(mat)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def color_key(rgb):
    return (round(rgb[0], 3), round(rgb[1], 3), round(rgb[2], 3))


# ---------------------------------------------------------------------------
# Construccion de la escena
# ---------------------------------------------------------------------------

def build_scene(scene):
    buildings = scene.get("buildings", [])
    roads = scene.get("roads", [])
    areas = scene.get("areas", [])

    # --- Edificios: paredes agrupadas por color + techos (dos aguas / azotea) ---
    groups = {}
    for b in buildings:
        k = color_key(b["color"])
        groups.setdefault(k, []).append(b)
    roof_house_bm = bmesh.new()  # techos de teja (casas bajas)
    roof_flat_bm = bmesh.new()   # detalle de azotea (torres)
    for i, (k, items) in enumerate(groups.items()):
        bm = bmesh.new()
        for b in items:
            base = float(b.get("min_height", 0.0))
            top = float(b["height"])
            if b.get("type") in LANDMARK_TYPES:
                add_spire(bm, b["footprint"], base, top)   # aguja, sin techo de casa
                continue
            add_prism(bm, b["footprint"], base, top)
            if (top - base) <= ROOF_SMALL_MAX_H:
                add_hip_roof(roof_house_bm, b["footprint"], top)
            else:
                add_rooftop_detail(roof_flat_bm, b["footprint"], top)
        # bisel de aristas -> menos "caja plana"
        if BEVEL > 0:
            try:
                bmesh.ops.bevel(bm, geom=list(bm.edges) + list(bm.verts),
                                offset=BEVEL, segments=1, affect="EDGES",
                                clamp_overlap=True)
            except Exception:
                pass
        mat = make_windowed_material(f"bld_{i}", k)   # fachada con ventanas
        bm_to_object(bm, f"Edificios_{i}", mat)
    if len(roof_house_bm.faces) > 0:
        bm_to_object(roof_house_bm, "Techos_teja",
                     make_material("techo_teja", ROOF_HOUSE_COLOR, roughness=0.7, specular=0.15))
    else:
        roof_house_bm.free()
    if len(roof_flat_bm.faces) > 0:
        bm_to_object(roof_flat_bm, "Azoteas",
                     make_material("azotea", ROOF_FLAT_COLOR, roughness=0.85, specular=0.1))
    else:
        roof_flat_bm.free()

    # --- Areas de agua y verde ---
    area_groups = {}
    for a in areas:
        k = color_key(a["color"])
        area_groups.setdefault(k, []).append(a)
    for i, (k, items) in enumerate(area_groups.items()):
        bm = bmesh.new()
        for a in items:
            add_flat_polygon(bm, a["polygon"], float(a.get("z", 0.03)))
        is_water = k[2] > k[1]  # azulado
        mat = make_material(f"area_{i}", k, roughness=0.25 if is_water else 0.9,
                            specular=0.5 if is_water else 0.1)
        bm_to_object(bm, f"Area_{i}", mat)

    # --- Calles ---
    road_groups = {}
    for r in roads:
        k = color_key(r["color"])
        road_groups.setdefault(k, []).append(r)
    for i, (k, items) in enumerate(road_groups.items()):
        bm = bmesh.new()
        for r in items:
            z = float(r.get("z", 0.06))
            # z levemente distinto por calle => evita z-fighting entre calles superpuestas
            p0 = r["path"][0]
            jitter = 0.001 * (int(abs(p0[0] * 5.1 + p0[1] * 9.7)) % 40)
            add_road_strip(bm, r["path"], float(r["width"]), z + jitter)
            if z > 1.0:  # es un puente elevado -> agregar pilares hasta el piso
                add_bridge_piers(bm, r["path"], z)
        greyish = (max(k) - min(k) < 0.06 and sum(k) / 3 < 0.4)
        asphalt = _tex("asphalt.jpg")
        if greyish and asphalt:
            mat = make_textured_material(f"road_{i}", asphalt, tile_m=6.0, roughness=0.85)
        else:
            mat = make_material(f"road_{i}", k, roughness=0.9, specular=0.08)
        bm_to_object(bm, f"Calles_{i}", mat)

    # --- Arboles (plazas + veredas de avenidas) ---
    trunk_bm, fol_bm, ntrees = scatter_trees(areas, roads)
    if len(trunk_bm.faces) > 0:
        bm_to_object(trunk_bm, "Troncos",
                     make_material("tronco", (0.34, 0.23, 0.15), roughness=0.95))
    else:
        trunk_bm.free()
    if len(fol_bm.faces) > 0:
        bm_to_object(fol_bm, "Copas",
                     make_material("copa", (0.28, 0.44, 0.22), roughness=0.95))
    else:
        fol_bm.free()

    # --- Bounds / centro ---
    bnd = scene.get("bounds", {})
    minx = bnd.get("minx", -scene.get("radius", 250))
    miny = bnd.get("miny", -scene.get("radius", 250))
    maxx = bnd.get("maxx", scene.get("radius", 250))
    maxy = bnd.get("maxy", scene.get("radius", 250))
    cx = (minx + maxx) / 2.0
    cy = (miny + maxy) / 2.0
    R = max(0.5 * max(maxx - minx, maxy - miny), 50.0)

    # --- Piso ---
    margin = R * 0.15
    bm = bmesh.new()
    v1 = bm.verts.new((minx - margin, miny - margin, 0.0))
    v2 = bm.verts.new((maxx + margin, miny - margin, 0.0))
    v3 = bm.verts.new((maxx + margin, maxy + margin, 0.0))
    v4 = bm.verts.new((minx - margin, maxy + margin, 0.0))
    bm.faces.new((v1, v2, v3, v4))
    pav = _tex("pavement.jpg")
    if pav:
        piso_mat = make_textured_material("Piso", pav, tile_m=3.0, roughness=0.92)
    else:
        piso_mat = make_material("Piso", GROUND_COLOR, roughness=0.95, specular=0.05)
    bm_to_object(bm, "Piso", piso_mat)

    return cx, cy, R


# ---------------------------------------------------------------------------
# Luz, mundo, camara, render
# ---------------------------------------------------------------------------

def setup_world(sky=False):
    """sky=True usa un cielo procedural Nishita (realista, para vistas a nivel de
    calle). sky=False deja un cielo plano azul (mejor para la maqueta aérea)."""
    scn = bpy.context.scene
    world = bpy.data.worlds.new("Cielo")
    scn.world = world
    world.use_nodes = True
    nt = world.node_tree
    bg = nt.nodes.get("Background")
    if not bg:
        return
    if sky:
        try:
            sky_node = nt.nodes.new("ShaderNodeTexSky")
            # El nombre del cielo atmosferico cambia entre versiones de Blender
            # (Nishita en 4.x; Multiple/Single Scattering en 5.x).
            for stype in ("MULTIPLE_SCATTERING", "NISHITA", "SINGLE_SCATTERING",
                          "HOSEK_WILKIE"):
                try:
                    sky_node.sky_type = stype
                    break
                except TypeError:
                    continue
            for attr, val in (("sun_elevation", math.radians(38)),
                              ("sun_rotation", math.radians(40)),
                              ("altitude", 300.0)):
                if hasattr(sky_node, attr):
                    try:
                        setattr(sky_node, attr, val)
                    except Exception:
                        pass
            nt.links.new(sky_node.outputs[0], bg.inputs[0])
            bg.inputs[1].default_value = 0.35
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
