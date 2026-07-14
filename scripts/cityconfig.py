"""
cityconfig.py — carga de configuracion de la skill maps-to-3d (modulo puro, sin bpy).

Combina, en este orden de prioridad (de menor a mayor): DEFAULTS < archivo de
config (JSON siempre; YAML solo si PyYAML esta instalado) < variables de entorno.
Nunca rompe si el archivo no existe o esta mal formado: en el peor caso devuelve
los defaults. Testeable sin Blender ni Overpass.
"""
import json
import os

# Defaults base. texture_dir/hdri/cache_dir arrancan en None y se completan desde
# el entorno en load_config(); el resto son constantes razonables.
DEFAULTS = {
    "radius": 250,          # metros alrededor del centro
    "samples": 128,         # muestras de render
    "engine": "cycles",     # motor de Blender
    "texture_dir": None,    # env MAPS3D_TEXTURES
    "hdri": None,           # env MAPS3D_HDRI
    "cache_dir": None,      # env MAPS3D_CACHE
}

# Mapa de override por entorno: env -> (clave de config, caster).
ENV_OVERRIDES = {
    "MAPS3D_RADIUS": ("radius", int),
    "MAPS3D_SAMPLES": ("samples", int),
    "MAPS3D_ENGINE": ("engine", str),
    "MAPS3D_TEXTURES": ("texture_dir", str),
    "MAPS3D_HDRI": ("hdri", str),
    "MAPS3D_CACHE": ("cache_dir", str),
}


def _note(msg):
    """Deja una nota informativa sin romper si citylog no esta disponible."""
    try:
        from citylog import get_logger
        get_logger().info(msg)
    except Exception:
        pass


def _apply_env(cfg):
    """Aplica overrides desde variables de entorno sobre cfg (in-place)."""
    for env_key, (cfg_key, caster) in ENV_OVERRIDES.items():
        raw = os.environ.get(env_key)
        if raw is None or raw == "":
            continue
        try:
            cfg[cfg_key] = caster(raw)
        except (TypeError, ValueError):
            _note("valor invalido en %s=%r, ignorado" % (env_key, raw))
    return cfg


def _parse_yaml(text, path):
    """Parsea YAML solo si PyYAML esta instalado; si no, nota clara y None."""
    try:
        import yaml  # PyYAML es opcional (extra 'yaml')
    except ImportError:
        _note("PyYAML no instalado: se ignora YAML %s (instalar extra 'yaml')" % path)
        return None
    try:
        return yaml.safe_load(text)
    except Exception as exc:  # yaml.YAMLError y demas
        _note("YAML invalido en %s: %s" % (path, exc))
        return None


def _merge_file(cfg, path):
    """Mergea el archivo de config sobre cfg. Nunca rompe."""
    if not os.path.isfile(path):
        _note("config no encontrado: %s (se usan defaults)" % path)
        return cfg
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        _note("no se pudo leer %s: %s" % (path, exc))
        return cfg

    ext = os.path.splitext(path)[1].lower()
    if ext in (".yaml", ".yml"):
        data = _parse_yaml(text, path)
    else:
        # JSON siempre (cualquier extension que no sea YAML explicito).
        try:
            data = json.loads(text)
        except ValueError as exc:
            _note("JSON invalido en %s: %s" % (path, exc))
            data = None

    if isinstance(data, dict):
        cfg.update(data)
    return cfg


def load_config(path=None):
    """
    Devuelve un dict de configuracion.

    Orden de precedencia: DEFAULTS < archivo (si se pasa `path`) < entorno.
    - JSON: siempre se mergea.
    - YAML: solo si PyYAML esta instalado; si no, se ignora con una nota.
    - Archivo faltante o mal formado: no rompe, se conservan los defaults.
    """
    cfg = dict(DEFAULTS)
    _apply_env(cfg)            # completa texture_dir/hdri/cache_dir + defaults de entorno
    if path:
        _merge_file(cfg, path)  # el archivo pisa los defaults
    _apply_env(cfg)            # el entorno tiene la ultima palabra
    return cfg
