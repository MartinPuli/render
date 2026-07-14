"""
live_build.py — construye la escena dentro de un Blender VIVO (via blender-mcp).

Pensado para llamarse desde el tool `execute_blender_code` del blender-mcp:

    import sys; sys.path.append(r"/ruta/al/repo/scripts")
    import importlib, live_build; importlib.reload(live_build)
    info = live_build.build(r"/ruta/al/repo/output/<slug>/scene.json",
                            heading=90, sv_xy=(0, -40))
    print(info)

Reusa blender_build.py (mismas funciones que el render headless), asi lo que
ves en el Blender vivo es idéntico a lo que produce la skill maps-to-3d.
"""
import json

import bpy

import blender_build as bb


def build(scene_path, heading=None, sv_xy=(0.0, 0.0), height=2.5, fov=90.0,
          clear=True, standard_view=True):
    """Construye la escena 3D en el Blender actual.

    scene_path : ruta al scene.json generado por place_to_3d.py
    heading    : si se pasa (0=N, 90=E, ...), pone cámara a nivel de calle
                 mirando a ese rumbo (para comparar con Street View).
                 Si es None, usa la cámara aérea 3/4.
    sv_xy      : posición (x,y en metros) de la cámara street-level.
    clear      : si True, resetea la escena antes de construir.
    """
    with open(scene_path) as f:
        scene = json.load(f)

    if clear:
        bpy.ops.wm.read_factory_settings(use_empty=True)

    cx, cy, R = bb.build_scene(scene)
    street_level = heading is not None
    bb.setup_world(sky=street_level)   # cielo fisico realista a nivel de calle
    if not street_level:
        bb.setup_sun()                 # con cielo fisico el sol ya viene del cielo

    if street_level:
        bb.setup_streetview_camera(float(heading), float(fov), float(height),
                                   float(sv_xy[0]), float(sv_xy[1]))
    else:
        bb.setup_camera(cx, cy, R)

    if standard_view:
        vs = bpy.context.scene.view_settings
        if street_level:
            # Tonemapping fotografico para el alto rango del cielo fisico
            for vt in ("AgX", "Filmic", "Standard"):
                try:
                    vs.view_transform = vt
                    break
                except TypeError:
                    continue
            try:
                vs.exposure = -1.8
            except Exception:
                pass
        else:
            try:
                vs.view_transform = "Standard"
                vs.exposure = bb.EXPOSURE
            except Exception:
                pass

    return {
        "buildings": len(scene.get("buildings", [])),
        "roads": len(scene.get("roads", [])),
        "areas": len(scene.get("areas", [])),
        "radius_m": round(R, 1),
        "center_latlon": [scene["center"]["lat"], scene["center"]["lon"]],
    }


def building_at(scene_path, x=0.0, y=0.0):
    """Devuelve el edificio cuyo footprint contiene (x,y), o None.
    Útil para ubicar un landmark (p.ej. el monumento en el punto geocodificado)
    y reemplazarlo por un modelo detallado (Hyper3D / Sketchfab)."""
    with open(scene_path) as f:
        scene = json.load(f)

    def contains(poly, px, py):
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

    for b in scene.get("buildings", []):
        if contains(b["footprint"], x, y):
            return {"type": b.get("type"), "height": b["height"],
                    "footprint": b["footprint"]}
    return None
