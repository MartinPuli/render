"""
citycache.py — cache en disco para respuestas de Overpass (modulo puro, sin bpy).

Evita re-consultar Overpass en corridas repetidas. La clave suele ser el
key_hash() del texto de la query; el valor es la respuesta cruda (str). El
directorio se crea de forma perezosa (solo al primer put()).

Ubicacion: env MAPS3D_CACHE si esta seteada, si no ~/.cache/maps-to-3d.
"""
import hashlib
import os


def cache_dir():
    """Directorio de cache: MAPS3D_CACHE o ~/.cache/maps-to-3d."""
    env = os.environ.get("MAPS3D_CACHE")
    if env:
        return os.path.expanduser(env)
    return os.path.join(os.path.expanduser("~"), ".cache", "maps-to-3d")


def key_hash(text):
    """SHA1 (hex) del texto dado; sirve para derivar claves estables."""
    if isinstance(text, str):
        text = text.encode("utf-8")
    return hashlib.sha1(text).hexdigest()


def _path_for(key):
    return os.path.join(cache_dir(), str(key) + ".txt")


def _ensure_dir():
    d = cache_dir()
    os.makedirs(d, exist_ok=True)
    return d


def get(key):
    """Devuelve el contenido cacheado para `key`, o None si no existe."""
    path = _path_for(key)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def put(key, text):
    """Guarda `text` bajo `key` (creando el dir si hace falta) y devuelve el path."""
    _ensure_dir()
    path = _path_for(key)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path
