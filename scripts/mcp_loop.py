"""
mcp_loop.py — helpers PUROS (sin bpy) que DEVUELVEN los strings de codigo Python
para enviar por el tool `execute_blender_code` del blender-mcp en cada paso del
loop vivo (ver docs/MCP_LOOP.md). No importa bpy ni tiene efectos al importarse:
solo genera texto. El codigo generado es el que corre DENTRO del Blender vivo.

Publico:
    build_payload(scene_path, heading=None, sv_xy=(0,0), height=2.5, fov=78.0,
                  scripts_dir=None) -> str
    render_payload(out_png, engine="BLENDER_EEVEE_NEXT", samples=None,
                   res=None) -> str
    save_payload(blend_path) -> str
    inspect_payload() -> str
    evaluate_payload() -> str
    correct_payload(sun_energy=None, exposure=None) -> str
    loop_plan(scene_path, reference=None, ...) -> list[dict]  # {step, purpose, code}

Idea clave del loop: inspect (get_scene_info) -> SAFE clear (bb.clear_scene, NUNCA
bpy.ops.wm.read_factory_settings, que desregistra el addon y mata el servidor MCP)
-> build_scene -> world/sun/camera -> render/screenshot -> evaluar vs foto de
referencia -> corregir -> repetir 3-6x -> guardar .blend + render PNG + reporte.
"""
import os


# ---------------------------------------------------------------------------
# Utilidades internas (puras)
# ---------------------------------------------------------------------------

def _scripts_dir(scripts_dir=None):
    """Directorio scripts/ a insertar en sys.path del Blender vivo. Por defecto,
    el propio directorio de este modulo (que ES scripts/)."""
    if scripts_dir:
        return os.path.abspath(str(scripts_dir))
    return os.path.dirname(os.path.abspath(__file__))


def _lit(s):
    """String literal Python seguro (maneja backslashes/comillas en rutas)."""
    return repr(str(s))


# ---------------------------------------------------------------------------
# Payloads: cada funcion devuelve un string de codigo Python listo para
# execute_blender_code. Sin efectos secundarios.
# ---------------------------------------------------------------------------

def inspect_payload():
    """get_scene_info / backup: enumera la escena actual ANTES de limpiar.
    No modifica nada; solo imprime un resumen para poder revertir/comparar."""
    return (
        "import bpy\n"
        "print('[mcp_loop] backup: objects=', len(bpy.data.objects),\n"
        "      'meshes=', len(bpy.data.meshes),\n"
        "      'materials=', len(bpy.data.materials),\n"
        "      'lights=', len(bpy.data.lights))\n"
        "for _o in list(bpy.data.objects)[:40]:\n"
        "    print('  -', _o.name, _o.type)\n"
    )


def build_payload(scene_path, heading=None, sv_xy=(0.0, 0.0), height=2.5,
                  fov=78.0, scripts_dir=None):
    """Codigo para: insertar scripts/ en sys.path, reload(blender_build),
    json.load del scene.json, SAFE clear, build_scene y world/sun/camera.

    heading None -> camara aerea 3/4 (setup_camera).
    heading numerico (0=N, 90=E) -> camara a nivel de calle (Street View).
    """
    sp = _scripts_dir(scripts_dir)
    street = heading is not None
    lines = [
        "import sys, json, importlib",
        "sys.path.insert(0, %s)" % _lit(sp),
        "import blender_build as bb",
        "importlib.reload(bb)",
        "with open(%s) as _f:" % _lit(scene_path),
        "    scene = json.load(_f)",
        "# SAFE clear: NUNCA bpy.ops.wm.read_factory_settings (desregistra el",
        "# addon del blender-mcp y mata el servidor). clear_scene() es seguro.",
        "bb.clear_scene()",
        "cx, cy, R = bb.build_scene(scene)",
    ]
    if street:
        lines += [
            "bb.setup_world(sky=True)",
            "bb.setup_streetview_camera(%r, %r, %r, %r, %r)" % (
                float(heading), float(fov), float(height),
                float(sv_xy[0]), float(sv_xy[1])),
            "bb.apply_street_view_settings()",
        ]
    else:
        lines += [
            "bb.setup_world(sky=False)",
            "bb.setup_sun()",
            "bb.setup_camera(cx, cy, R)",
        ]
    lines += [
        "print('[mcp_loop] built', len(scene.get('buildings', [])),",
        "      'buildings roads=', len(scene.get('roads', [])),",
        "      'R=%.1fm' % R)",
    ]
    return "\n".join(lines) + "\n"


def render_payload(out_png, engine="BLENDER_EEVEE_NEXT", samples=None, res=None):
    """Codigo para fijar engine/samples/resolucion y renderizar un still a out_png.
    Sirve tanto para el preview de cada iteracion como para el render final."""
    lines = [
        "import bpy",
        "scn = bpy.context.scene",
        "scn.render.engine = %s" % _lit(engine),
    ]
    if samples is not None:
        s = int(samples)
        lines += [
            "try:",
            "    scn.cycles.samples = %d" % s,
            "except Exception:",
            "    pass",
            "try:",
            "    scn.eevee.taa_render_samples = %d" % s,
            "except Exception:",
            "    pass",
        ]
    if res is not None:
        lines += [
            "scn.render.resolution_x = %d" % int(res[0]),
            "scn.render.resolution_y = %d" % int(res[1]),
        ]
    lines += [
        "scn.render.resolution_percentage = 100",
        "scn.render.filepath = %s" % _lit(out_png),
        "bpy.ops.render.render(write_still=True)",
        "print('[mcp_loop] rendered ->', %s)" % _lit(out_png),
    ]
    return "\n".join(lines) + "\n"


def save_payload(blend_path):
    """Codigo para guardar el .blend actual (persistir el resultado del loop)."""
    return (
        "import bpy\n"
        "bpy.ops.wm.save_as_mainfile(filepath=%s)\n"
        "print('[mcp_loop] saved ->', %s)\n" % (_lit(blend_path), _lit(blend_path))
    )


def evaluate_payload():
    """Codigo que imprime los settings clave (engine/tonemapping/exposicion) para
    evaluar el screenshot contra la foto de referencia antes de corregir."""
    return (
        "import bpy\n"
        "scn = bpy.context.scene\n"
        "vs = scn.view_settings\n"
        "print('[mcp_loop] evaluate: engine=', scn.render.engine,\n"
        "      'view_transform=', getattr(vs, 'view_transform', '?'),\n"
        "      'exposure=', round(getattr(vs, 'exposure', 0.0), 3))\n"
        "print('[mcp_loop] objetos=', len(bpy.data.objects),\n"
        "      'materiales=', len(bpy.data.materials))\n"
    )


def correct_payload(sun_energy=None, exposure=None):
    """Codigo para aplicar una correccion tras evaluar: exposicion y/o energia del
    sol. Pensado para llamarse con distintos valores en cada iteracion del loop."""
    lines = ["import bpy", "scn = bpy.context.scene"]
    if exposure is not None:
        lines += [
            "try:",
            "    scn.view_settings.exposure = %r" % float(exposure),
            "except Exception:",
            "    pass",
        ]
    if sun_energy is not None:
        lines += [
            "for _l in bpy.data.lights:",
            "    if _l.type == 'SUN':",
            "        _l.energy = %r" % float(sun_energy),
        ]
    lines += ["print('[mcp_loop] correction applied')"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Plan del loop completo (3-6 iteraciones)
# ---------------------------------------------------------------------------

def loop_plan(scene_path, reference=None, preview_png=None, final_png=None,
              blend_path=None, engine="BLENDER_EEVEE_NEXT", samples=48):
    """Devuelve la lista de pasos del loop vivo. Cada paso es un dict con
    'step', 'purpose' y 'code' (el string a mandar por execute_blender_code).

    reference: ruta a la foto de referencia (Street View / aerea) contra la que
               se evalua; solo se usa en el texto de 'purpose'.
    """
    preview_png = preview_png or os.path.join("output", "preview.png")
    final_png = final_png or os.path.join("output", "render.png")
    blend_path = blend_path or os.path.join("output", "scene.blend")
    ref_note = ("comparar vs %s" % reference) if reference else \
               "comparar vs la foto de referencia"
    preview_samples = max(8, int(samples) // 4)

    return [
        {"step": "inspect",
         "purpose": "get_scene_info: inspeccionar/backup la escena antes de tocar nada",
         "code": inspect_payload()},
        {"step": "clear+build",
         "purpose": "SAFE clear (bb.clear_scene, NUNCA read_factory_settings) + "
                    "build_scene + setup_world/sun/camera",
         "code": build_payload(scene_path)},
        {"step": "screenshot",
         "purpose": "render de preview rapido para get_viewport_screenshot / evaluacion",
         "code": render_payload(preview_png, engine=engine, samples=preview_samples)},
        {"step": "evaluate",
         "purpose": "evaluar el preview y %s (materiales/sol/exposicion/camara)" % ref_note,
         "code": evaluate_payload()},
        {"step": "correct",
         "purpose": "corregir exposicion/sol segun la evaluacion; repetir 3-6x",
         "code": correct_payload(exposure=-0.5)},
        {"step": "save+render",
         "purpose": "guardar .blend + render final PNG + reporte corto",
         "code": save_payload(blend_path) +
                 render_payload(final_png, engine=engine, samples=int(samples))},
    ]
