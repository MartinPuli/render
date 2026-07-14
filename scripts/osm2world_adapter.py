"""
osm2world_adapter.py — adaptador OPCIONAL para el motor externo OSM2World
(modulo puro, importable SIN java ni bpy).

EVALUACION de OSM2World como motor alternativo
----------------------------------------------
OSM2World (https://osm2world.org/) es un conversor maduro de datos OpenStreetMap
a geometria 3D. Se evaluo como motor alternativo al generador procedural propio.

Pros:
  - Conversion real OSM -> 3D lista para usar: reconstruye edificios con techos
    (roof:shape), materiales por tags, calles, agua y vegetacion sin que nosotros
    escribamos esa logica.
  - Proyecto maduro y probado, con soporte de formatos estandar (OBJ, glTF, etc.).
  - Cobertura amplia de tags OSM, mas completa que reglas caseras puntuales.

Contras:
  - Dependencia de JVM: requiere un java instalado y el .jar descargado aparte;
    no es pip-installable y suma un paso de setup.
  - Menos control nativo desde Blender: produce una malla ya "horneada", con lo
    que reajustar geometria/altura/estilo por objeto es mas dificil que en el
    pipeline procedural, donde construimos cada edificio con bpy.
  - Pipeline de materiales SEPARADO: sus materiales/texturas no se integran con
    nuestro sistema (perfiles de zona, texturas CC0, ventanas procedurales) y hay
    que reconciliarlos a mano tras importar el OBJ.

Conclusion / decision:
  OSM2World queda como motor OPT-IN. Solo se activa si el usuario define la
  variable de entorno OSM2WORLD_JAR (o pasa jar=) y tiene java disponible. El
  generador procedural propio sigue siendo el DEFAULT y no se elimina nada: si
  OSM2World no esta disponible, el caller debe caer al camino procedural.

Uso tipico desde un caller (p. ej. live_build/place_to_3d):
    import osm2world_adapter as o2w
    if o2w.osm2world_available():
        obj = o2w.run_osm2world("zona.osm", "zona.obj")   # importar OBJ luego
    else:
        ...  # fallback: generador procedural (default)
"""
import os
import shutil
import subprocess


class OSM2WorldUnavailable(RuntimeError):
    """OSM2World no esta disponible (falta el .jar o el binario java).

    El caller debe capturar esta excepcion y caer al generador procedural,
    que es el motor por defecto del pipeline.
    """


def _resolve_jar(jar=None):
    """Ruta del .jar: argumento explicito o variable de entorno OSM2WORLD_JAR."""
    return jar or os.environ.get("OSM2WORLD_JAR")


def _resolve_java(java=None):
    """Binario java: argumento explicito, env JAVA_BIN, o shutil.which('java')."""
    if java:
        return java
    return os.environ.get("JAVA_BIN") or shutil.which("java")


def osm2world_available(jar=None, java=None):
    """True solo si hay un .jar existente (arg o env OSM2WORLD_JAR) COMO ARCHIVO
    y ademas un binario java localizable (shutil.which('java') o env JAVA_BIN).

    Funcion pura: no lanza subprocess ni descarga nada.
    """
    jar_path = _resolve_jar(jar)
    if not jar_path or not os.path.isfile(jar_path):
        return False
    java_bin = _resolve_java(java)
    return bool(java_bin)


def run_osm2world(osm_input, out_obj, jar=None, java=None, extra_args=None):
    """Ejecuta OSM2World para convertir `osm_input` (OSM/PBF) en `out_obj` (OBJ).

    Si OSM2World no esta disponible (ver osm2world_available), lanza
    OSM2WorldUnavailable con un mensaje que le indica al caller que caiga al
    generador procedural (el motor por defecto). No descarga nada.

    Devuelve la ruta `out_obj` en caso de exito.
    """
    if not osm2world_available(jar=jar, java=java):
        raise OSM2WorldUnavailable(
            "OSM2World no esta disponible: definí la variable de entorno "
            "OSM2WORLD_JAR con la ruta al .jar y asegurate de tener 'java' en el "
            "PATH (o env JAVA_BIN). Mientras tanto, usá el generador procedural "
            "integrado, que es el motor por defecto."
        )
    jar_path = _resolve_jar(jar)
    java_bin = _resolve_java(java)
    cmd = [java_bin, "-jar", jar_path, "--input", osm_input, "--output", out_obj]
    if extra_args:
        cmd.extend(extra_args)
    subprocess.run(cmd, check=True)
    return out_obj
