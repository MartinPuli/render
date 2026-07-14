#!/usr/bin/env python3
"""
world_scene.py — Arma la escena Blender COMPLETA y "spec-compliant" del spec
world-to-blender a partir de un scene.json, y la deja lista para entregar:

  - geometria coloreada organizada en colecciones nombradas (00_REFERENCE..13_EXPORT)
  - metadata geografica (lat/lon/proyeccion/fuente) en la escena y en un Empty GEO_ORIGIN
  - luces (sol + cielo fisico) y 3 camaras (aerea, oblicua, calle)
  - limpieza de geometria (fusion de vertices, normales) — ya la hace bm_to_object
  - guarda <slug>_world_scene.blend
  - renderiza previews (aerea + oblicua)
  - exporta formatos adicionales (GLB/FBX/OBJ/STL) a 13_EXPORT
  - escribe report.json con conteos de objetos/poligonos/materiales por coleccion

Uso (headless, con el binario de Blender):
  blender -b -P world_scene.py -- scene.json OUT_DIR SLUG \
          [--engine CYCLES|EEVEE] [--samples N] [--export glb,fbx] \
          [--street-heading H --street-x X --street-y Y]

Coordenadas: metros, X=este, Y=norte, Z=arriba. 1 unidad Blender = 1 metro.
"""
import json
import math
import os
import sys
from pathlib import Path

import bpy

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.append(str(HERE))
import blender_build as bb  # noqa: E402


def parse_argv():
    argv = sys.argv
    argv = argv[argv.index("--") + 1:] if "--" in argv else argv[1:]
    if len(argv) < 3:
        sys.exit("Uso: world_scene.py scene.json OUT_DIR SLUG [--engine CYCLES|EEVEE] "
                 "[--samples N] [--export glb,fbx] [--street-heading H --street-x X --street-y Y]")
    o = {"scene": argv[0], "out": argv[1], "slug": argv[2],
         "engine": "CYCLES", "samples": 64, "export": ["glb"],
         "sheading": 0.0, "sx": 0.0, "sy": 0.0, "res": (1600, 1000)}
    i = 3
    while i < len(argv):
        a = argv[i]
        if a == "--engine":
            o["engine"] = argv[i + 1].upper(); i += 1
        elif a == "--samples":
            o["samples"] = int(argv[i + 1]); i += 1
        elif a == "--export":
            o["export"] = [s.strip().lower() for s in argv[i + 1].split(",") if s.strip()]; i += 1
        elif a == "--street-heading":
            o["sheading"] = float(argv[i + 1]); i += 1
        elif a == "--street-x":
            o["sx"] = float(argv[i + 1]); i += 1
        elif a == "--street-y":
            o["sy"] = float(argv[i + 1]); i += 1
        elif a == "--res":
            o["res"] = (int(argv[i + 1]), int(argv[i + 2])); i += 2
        i += 1
    return o


def set_metadata(scene, coll):
    """Guarda lat/lon/proyeccion/fuente como custom props de la escena y en un
    Empty GEO_ORIGIN (00_REFERENCE)."""
    c = scene.get("center", {})
    scn = bpy.context.scene
    meta = {
        "geo_lat": float(c.get("lat", 0.0)),
        "geo_lon": float(c.get("lon", 0.0)),
        "geo_label": str(c.get("label", "")),
        "geo_radius_m": float(scene.get("radius", 0.0)),
        "geo_projection": "equirectangular local (m) alrededor del centro",
        "geo_source": "OpenStreetMap (Overpass) + Google Street View (referencia)",
    }
    for k, v in meta.items():
        scn[k] = v
    empty = bpy.data.objects.new("GEO_ORIGIN", None)
    empty.empty_display_size = 5.0
    empty.empty_display_type = "ARROWS"
    for k, v in meta.items():
        empty[k] = v
    coll.objects.link(empty)
    return meta


def _track(cam, cx, cy, cz):
    tgt = bpy.data.objects.new(cam.name + "_target", None)
    tgt.location = (cx, cy, cz)
    bpy.context.scene.collection.objects.link(tgt)
    con = cam.constraints.new("TRACK_TO")
    con.target = tgt
    con.track_axis = "TRACK_NEGATIVE_Z"
    con.up_axis = "UP_Y"
    return tgt


def make_cameras(cx, cy, R, coll, street=(0.0, 0.0, 0.0)):
    """Crea camara aerea (principal), oblicua y de calle. Devuelve la aerea como
    camara activa. Todas van a coll (12_CAMERAS)."""
    scn = bpy.context.scene

    def cam_at(name, elev_deg, azim_deg, dist, frame_R, tz):
        cd = bpy.data.cameras.new(name)
        ob = bpy.data.objects.new(name, cd)
        coll.objects.link(ob)
        elev, azim = math.radians(elev_deg), math.radians(azim_deg)
        ob.location = (cx + dist * math.cos(elev) * math.sin(azim),
                       cy - dist * math.cos(elev) * math.cos(azim),
                       tz + dist * math.sin(elev))
        cd.clip_end = dist * 6.0
        try:
            cd.sensor_fit = "AUTO"
        except Exception:
            pass
        cd.angle = 2.0 * math.atan((frame_R) / dist)
        _track(ob, cx, cy, tz)
        return ob

    aerial = cam_at("Cam_aerea", 33.0, 135.0, R * 2.3, R * 1.02, min(R * 0.12, 30.0))
    cam_at("Cam_oblicua", 15.0, 205.0, R * 1.7, R * 0.85, min(R * 0.10, 22.0))

    # camara de calle
    sx, sy, sh = street
    cd = bpy.data.cameras.new("Cam_calle")
    ob = bpy.data.objects.new("Cam_calle", cd)
    coll.objects.link(ob)
    ob.location = (sx, sy, 2.5)
    ob.rotation_euler = (math.radians(90.0), 0.0, math.radians(-sh))
    cd.clip_end = 5000.0
    try:
        cd.sensor_fit = "HORIZONTAL"
    except Exception:
        pass
    cd.angle = math.radians(74.0)

    scn.camera = aerial
    return aerial


def setup_lighting(coll):
    """Cielo (HDRI real o Nishita) + sol. Con HDRI, el sol ya viene del mapa, asi
    que no agregamos una luz solar extra (evita doble iluminacion)."""
    bb.setup_world(sky=True)
    if bb.using_hdri():
        return None
    data = bpy.data.lights.new("Sol", "SUN")
    data.energy = 2.2
    try:
        data.angle = 0.06
    except Exception:
        pass
    sun = bpy.data.objects.new("Sol", data)
    sun.rotation_euler = (math.radians(52), math.radians(8), math.radians(150))
    coll.objects.link(sun)
    return sun


def scene_stats():
    """Conteos de objetos/poligonos/materiales, y por coleccion."""
    total_obj = 0
    total_poly = 0
    per_coll = {}
    for coll in bpy.data.collections:
        if coll.name not in bb.COLLECTION_NAMES:
            continue
        n_obj = len(coll.objects)
        n_poly = sum(len(o.data.polygons) for o in coll.objects
                     if o.type == "MESH" and o.data)
        per_coll[coll.name] = {"objects": n_obj, "polygons": n_poly}
    for o in bpy.data.objects:
        total_obj += 1
        if o.type == "MESH" and o.data:
            total_poly += len(o.data.polygons)
    return {
        "total_objects": total_obj,
        "total_polygons": total_poly,
        "total_materials": len(bpy.data.materials),
        "per_collection": per_coll,
    }


def export_formats(out_dir, slug, formats):
    exported = {}
    exp_dir = Path(out_dir) / "export"
    exp_dir.mkdir(parents=True, exist_ok=True)
    for fmt in formats:
        path = str(exp_dir / f"{slug}.{fmt}")
        try:
            if fmt in ("glb", "gltf"):
                bpy.ops.export_scene.gltf(
                    filepath=path,
                    export_format="GLB" if fmt == "glb" else "GLTF_SEPARATE")
            elif fmt == "fbx":
                bpy.ops.export_scene.fbx(filepath=path)
            elif fmt == "obj":
                try:
                    bpy.ops.wm.obj_export(filepath=path)
                except Exception:
                    bpy.ops.export_scene.obj(filepath=path)
            elif fmt == "stl":
                try:
                    bpy.ops.wm.stl_export(filepath=path)
                except Exception:
                    bpy.ops.export_mesh.stl(filepath=path)
            else:
                continue
            exported[fmt] = path
            print(f"[world_scene] export {fmt} -> {path}")
        except Exception as e:
            print(f"[world_scene] export {fmt} FALLO: {e}")
    return exported


def main():
    o = parse_argv()
    with open(o["scene"]) as f:
        scene = json.load(f)

    bpy.ops.wm.read_factory_settings(use_empty=True)  # headless: seguro (sin MCP)

    cx, cy, R = bb.build_scene(scene)
    C = bb.make_collections()
    meta = set_metadata(scene, C["00_REFERENCE"])
    setup_lighting(C["11_LIGHTING"])
    make_cameras(cx, cy, R, C["12_CAMERAS"], street=(o["sx"], o["sy"], o["sheading"]))

    # Render settings
    scn = bpy.context.scene
    if o["engine"] == "EEVEE":
        try:
            scn.render.engine = "BLENDER_EEVEE_NEXT"
        except Exception:
            scn.render.engine = "BLENDER_EEVEE"
    else:
        scn.render.engine = "CYCLES"
        scn.cycles.samples = o["samples"]
        scn.cycles.device = "CPU"
        try:
            scn.cycles.use_denoising = True
        except Exception:
            pass
    scn.render.resolution_x, scn.render.resolution_y = o["res"]
    scn.render.resolution_percentage = 100
    bb.apply_street_view_settings()   # AgX + look + exposicion fotografica
    # Con HDRI la exposicion es uniforme; con cielo Nishita las vistas aereas
    # miran mas al cenit (cielo mas oscuro) y necesitan algo mas de exposicion.
    try:
        scn.view_settings.exposure = bb.STREET_EXPOSURE_HDRI if bb.using_hdri() else -1.7
    except Exception:
        pass
    bb.setup_compositor(scn, haze_end=max(700.0, R * 3.0))  # bruma de distancia + vineta

    out_dir = Path(o["out"])
    out_dir.mkdir(parents=True, exist_ok=True)

    # Empaquetar texturas + HDRI dentro del .blend (self-contained, sin assets faltantes)
    try:
        bpy.ops.file.pack_all()
    except Exception as e:
        print(f"[world_scene] pack_all: {e}")

    # Guardar .blend
    blend_path = str(out_dir / f"{o['slug']}_world_scene.blend")
    try:
        bpy.ops.wm.save_as_mainfile(filepath=blend_path)
        print(f"[world_scene] .blend -> {blend_path}")
    except Exception as e:
        print(f"[world_scene] no pude guardar .blend: {e}")

    # Render previews (aerea + oblicua + calle)
    previews = {}
    for cam_name, tag in (("Cam_aerea", "aerial"), ("Cam_oblicua", "oblique"),
                          ("Cam_calle", "street")):
        cam = bpy.data.objects.get(cam_name)
        if not cam:
            continue
        scn.camera = cam
        p = str(out_dir / f"preview_{tag}.png")
        scn.render.filepath = p
        try:
            bpy.ops.render.render(write_still=True)
            previews[tag] = p
            print(f"[world_scene] preview {tag} -> {p}")
        except Exception as e:
            print(f"[world_scene] preview {tag} FALLO: {e}")

    exported = export_formats(o["out"], o["slug"], o["export"])
    stats = scene_stats()

    report = {
        "location": meta,
        "stats": stats,
        "blend": blend_path,
        "previews": previews,
        "exports": exported,
        "engine": o["engine"],
    }
    rp = str(out_dir / "report.json")
    with open(rp, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"[world_scene] report -> {rp}")
    print("[world_scene] STATS " + json.dumps(stats))


if __name__ == "__main__":
    main()
