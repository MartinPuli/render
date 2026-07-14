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
import sys

import bpy
import bmesh

# --- Parametros de render / camara ---
RES_X, RES_Y = 1280, 960
CYCLES_SAMPLES = 48
SUN_ENERGY = 2.0
WORLD_STRENGTH = 0.30
EXPOSURE = -0.6         # compensacion de exposicion (evita quemar los blancos)
CAM_ELEV_DEG = 38.0     # elevacion de la camara (vista 3/4 aerea)
CAM_AZIM_DEG = 45.0     # azimut
CAM_DIST_FACTOR = 2.15  # distancia = radio_escena * factor
CAM_FRAME_FACTOR = 1.05  # cuanto margen dejar alrededor de la escena
GROUND_COLOR = (0.58, 0.59, 0.55)


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

    # --- Edificios: agrupados por color para minimizar objetos/materiales ---
    groups = {}
    for b in buildings:
        k = color_key(b["color"])
        groups.setdefault(k, []).append(b)
    for i, (k, items) in enumerate(groups.items()):
        bm = bmesh.new()
        for b in items:
            add_prism(bm, b["footprint"], float(b.get("min_height", 0.0)), float(b["height"]))
        mat = make_material(f"bld_{i}", k, roughness=0.82, specular=0.12)
        bm_to_object(bm, f"Edificios_{i}", mat)

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
        mat = make_material(f"road_{i}", k, roughness=0.9, specular=0.08)
        bm_to_object(bm, f"Calles_{i}", mat)

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
    bm_to_object(bm, "Piso", make_material("Piso", GROUND_COLOR, roughness=0.95, specular=0.05))

    return cx, cy, R


# ---------------------------------------------------------------------------
# Luz, mundo, camara, render
# ---------------------------------------------------------------------------

def setup_world():
    scn = bpy.context.scene
    world = bpy.data.worlds.new("Cielo")
    scn.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
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
