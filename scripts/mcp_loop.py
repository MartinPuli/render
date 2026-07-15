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
    backup_payload(path) -> str
    validate_payload(render_path=None, blend_path=None) -> str
    one_shot_payload(scene_path, out_dir, slug="scene", ...) -> str
    iteration_payload(out_dir, iteration, ...) -> str
    restore_checkpoint_payload(blend_path) -> str
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


def render_payload(out_png, engine="AUTO", samples=None, res=None):
    """Codigo para fijar engine/samples/resolucion y renderizar un still a out_png.
    Sirve tanto para el preview de cada iteracion como para el render final."""
    lines = [
        "import bpy",
        "scn = bpy.context.scene",
        "_preferred_engine = %s" % _lit(engine),
        "_engine_candidates = ([_preferred_engine] if _preferred_engine != 'AUTO' else []) + "
        "['BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE', 'CYCLES']",
        "_selected_engine = None",
        "for _engine in _engine_candidates:",
        "    try:",
        "        scn.render.engine = _engine",
        "        _selected_engine = _engine",
        "        break",
        "    except Exception:",
        "        pass",
        "if _selected_engine is None:",
        "    raise RuntimeError('No compatible Blender render engine found')",
        "for _look in ('Medium High Contrast', 'AgX - Medium High Contrast', 'None'):",
        "    try:",
        "        scn.view_settings.look = _look",
        "        break",
        "    except Exception:",
        "        pass",
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
        "print('[mcp_loop] rendered', _selected_engine, '->', %s)" % _lit(out_png),
    ]
    return "\n".join(lines) + "\n"


def save_payload(blend_path):
    """Codigo para guardar el .blend actual (persistir el resultado del loop)."""
    return (
        "import bpy\n"
        "bpy.ops.wm.save_as_mainfile(filepath=%s)\n"
        "print('[mcp_loop] saved ->', %s)\n" % (_lit(blend_path), _lit(blend_path))
    )


def backup_payload(backup_path):
    """Guarda la escena viva antes del clear seguro. El payload one-shot vuelve
    a guardar luego en la ruta final, por lo que el backup no queda activo."""
    return (
        "import bpy, os\n"
        "os.makedirs(os.path.dirname(%s) or '.', exist_ok=True)\n" % _lit(backup_path) +
        "bpy.ops.wm.save_as_mainfile(filepath=%s)\n" % _lit(backup_path) +
        "print('[mcp_loop] backup ->', %s)\n" % _lit(backup_path)
    )


def validate_payload(render_path=None, blend_path=None, extra_paths=None):
    """Validacion ejecutable dentro de Blender. Falla fuerte si el resultado no
    es entregable, en vez de declarar exito con un render vacio."""
    lines = [
        "import bpy, os, json",
        "_scene = bpy.context.scene",
        "_checks = {",
        "  'camera': _scene.camera is not None,",
        "  'objects': len(bpy.data.objects) >= 4,",
        "  'meshes': len(bpy.data.meshes) >= 1,",
        "  'materials': len(bpy.data.materials) >= 1,",
        "}",
    ]
    if render_path:
        lines.append("_checks['render_file'] = os.path.isfile(%s) and os.path.getsize(%s) > 4096" %
                     (_lit(render_path), _lit(render_path)))
    if blend_path:
        lines.append("_checks['blend_file'] = os.path.isfile(%s) and os.path.getsize(%s) > 4096" %
                     (_lit(blend_path), _lit(blend_path)))
    for idx, path in enumerate(extra_paths or []):
        lines.append("_checks['extra_file_%d'] = os.path.isfile(%s) and os.path.getsize(%s) > 4096" %
                     (idx, _lit(path), _lit(path)))
    lines += [
        "print('[mcp_loop] validation', json.dumps(_checks, sort_keys=True))",
        "if not all(_checks.values()):",
        "    raise RuntimeError('one-shot validation failed: %s' % _checks)",
    ]
    return "\n".join(lines) + "\n"


def one_shot_payload(scene_path, out_dir, slug="scene", scripts_dir=None,
                     textures_dir=None, hdri_path=None, engine="AUTO",
                     samples=48, res=(1400, 1000), backup=True):
    """Genera UN payload autocontenido para backup -> build -> render -> save ->
    validate. Es el camino recomendado cuando el MCP permite ejecutar Python en
    Blender pero no expone helpers especializados como tools separados."""
    sp = _scripts_dir(scripts_dir)
    out_dir = os.path.abspath(str(out_dir))
    scene_path = os.path.abspath(str(scene_path))
    slug = str(slug or "scene")
    blend_path = os.path.join(out_dir, slug + ".blend")
    render_path = os.path.join(out_dir, slug + "_aerial.png")
    oblique_path = os.path.join(out_dir, slug + "_oblique.png")
    backup_path = os.path.join(out_dir, "pre_" + slug + ".blend")
    report_path = os.path.join(out_dir, slug + "_report.json")

    lines = [
        "import bpy, os, sys, json, importlib",
        "os.makedirs(%s, exist_ok=True)" % _lit(out_dir),
        "sys.path.insert(0, %s)" % _lit(sp),
    ]
    if textures_dir:
        lines += ["os.environ['MAPS3D_TEXTURES'] = %s" % _lit(os.path.abspath(str(textures_dir)))]
    if hdri_path:
        lines += ["os.environ['MAPS3D_HDRI'] = %s" % _lit(os.path.abspath(str(hdri_path)))]
    if backup:
        lines.append(backup_payload(backup_path))
    lines += [
        "import blender_build as bb, live_build",
        "importlib.reload(bb); importlib.reload(live_build)",
        "_info = live_build.build(%s, heading=None, clear=True, standard_view=True)" %
        _lit(scene_path),
        "print('[mcp_loop] build info', json.dumps(_info, sort_keys=True))",
        render_payload(render_path, engine=engine, samples=samples, res=res),
        "_cam = bpy.context.scene.camera",
        "_target = bpy.data.objects.get('Target')",
        "_aerial_loc = _cam.location.copy()",
        "_aerial_rot = _cam.rotation_euler.copy()",
        "_aerial_angle = _cam.data.angle",
        "if _target is not None:",
        "    _vec = _cam.location - _target.location",
        "    _cam.location = _target.location + _vec * 0.64",
        "    _cam.location.z = _target.location.z + _vec.z * 0.52",
        "_cam.data.angle = 0.96",
        render_payload(oblique_path, engine=engine, samples=samples, res=(res[0], int(res[1] * 0.72))),
        "_cam.location = _aerial_loc",
        "_cam.rotation_euler = _aerial_rot",
        "_cam.data.angle = _aerial_angle",
        "bpy.context.scene.render.resolution_x = %d" % int(res[0]),
        "bpy.context.scene.render.resolution_y = %d" % int(res[1]),
        "bpy.context.scene.render.filepath = %s" % _lit(render_path),
        save_payload(blend_path),
        validate_payload(render_path, blend_path, extra_paths=[oblique_path]),
        "_report = dict(_info)",
        "_report.update({'role': 'baseline', 'blend': %s, 'render': %s, 'oblique': %s, 'engine': bpy.context.scene.render.engine, "
        "'objects': len(bpy.data.objects), 'materials': len(bpy.data.materials)})" %
        (_lit(blend_path), _lit(render_path), _lit(oblique_path)),
        "with open(%s, 'w', encoding='utf-8') as _f:" % _lit(report_path),
        "    json.dump(_report, _f, indent=2, ensure_ascii=False)",
        "print('[mcp_loop] BASELINE_READY', json.dumps(_report, sort_keys=True))",
        "print('[mcp_loop] ONE_SHOT_OK', json.dumps(_report, sort_keys=True))",
    ]
    return "\n".join(lines) + "\n"


def iteration_payload(out_dir, iteration, engine="AUTO", samples=32,
                      res=(1400, 1000), prefix="loop"):
    """Renderiza y guarda un checkpoint comparable de una iteracion.

    El scoring ocurre fuera de Blender con ``loop_engineering``; este payload
    congela exactamente el artefacto que recibio ese score.
    """
    out_dir = os.path.abspath(str(out_dir))
    index = int(iteration)
    if index < 0:
        raise ValueError("iteration must be >= 0")
    stem = "%s_%02d" % (str(prefix), index)
    render_path = os.path.join(out_dir, stem + ".png")
    blend_path = os.path.join(out_dir, stem + ".blend")
    lines = [
        "import os, json, bpy",
        "os.makedirs(%s, exist_ok=True)" % _lit(out_dir),
        render_payload(render_path, engine=engine, samples=samples, res=res),
        save_payload(blend_path),
        validate_payload(render_path, blend_path),
        "print('[mcp_loop] ITERATION_READY', json.dumps({"
        "'iteration': %d, 'render': %s, 'blend': %s}, sort_keys=True))" %
        (index, _lit(render_path), _lit(blend_path)),
    ]
    return "\n".join(lines) + "\n"


def restore_checkpoint_payload(blend_path):
    """Restaura el checkpoint ganador al terminar por estancamiento/max-iter.

    Abrir un .blend no es factory reset, pero el caller debe verificar MCP otra
    vez inmediatamente despues.
    """
    return (
        "import bpy, os\n"
        "_checkpoint = %s\n" % _lit(os.path.abspath(str(blend_path))) +
        "if not os.path.isfile(_checkpoint):\n"
        "    raise RuntimeError('checkpoint not found: %s' % _checkpoint)\n"
        "bpy.ops.wm.open_mainfile(filepath=_checkpoint)\n"
        "print('[mcp_loop] BEST_CHECKPOINT_RESTORED', _checkpoint)\n"
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
              blend_path=None, engine="AUTO", samples=48):
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
