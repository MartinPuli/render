"""
cityroofs.py — eleccion del TIPO de techo por edificio (modulo puro, sin bpy).

Prioriza el tag OSM `roof:shape` (que place_to_3d guarda como b["roof_shape"]);
si no hay tag, reparte VARIEDAD de techos por altura + un hash de la posicion,
para que no queden todos iguales. La geometria concreta de cada techo la arma
`blender_build.py` (necesita bmesh); aca solo vive la decision, testeable sin
Blender ni Overpass.
"""
import math

# Techo por defecto: casas/edificios mas bajos que esto reciben aguas; los mas
# altos, azotea (plana o con parapeto). Igual a ROOF_SMALL_MAX_H de blender_build.
ROOF_SMALL_MAX_H = 11.0

ROOF_KINDS = ("flat", "parapet", "hipped", "gabled", "pyramidal", "dome", "skillion")

# Mapa de valores OSM roof:shape -> tipo que sabemos construir.
_OSM_ROOF = {
    "flat": "flat",
    "gabled": "gabled",
    "gambrel": "gabled",
    "saltbox": "gabled",
    "quadruple_saltbox": "gabled",
    "hipped": "hipped",
    "half-hipped": "hipped",
    "mansard": "hipped",
    "pyramidal": "pyramidal",
    "dome": "dome",
    "onion": "dome",
    "round": "dome",
    "skillion": "skillion",
    "mono_pitch": "skillion",
    "lean_to": "skillion",
}


def _hash01(seed):
    """Ruido determinista en [0,1) a partir de un seed (misma formula que el builder)."""
    n = math.sin(seed * 127.1 + 311.7) * 43758.5453
    return n - math.floor(n)


_LOW_BIAS = {"hipped", "gabled", "pyramidal", "skillion"}
_HIGH_BIAS = {"flat", "parapet", "skillion"}


def choose_roof_kind(roof_shape, height_m, seed=0.0, small_max=None, bias=None):
    """Devuelve uno de ROOF_KINDS.

    - Si `roof_shape` (tag OSM) es conocido, se respeta (maxima prioridad).
    - Si no, y el perfil arquitectonico sugiere un `bias` compatible con la
      altura, se aplica la mayor parte de las veces (mantiene coherencia de zona).
    - Si tampoco, da variedad por `seed` (posicion): bajos -> aguas variadas,
      altos -> azotea, para que la cuadra no sea monotona.
    """
    small_max = ROOF_SMALL_MAX_H if small_max is None else small_max
    if roof_shape:
        rs = str(roof_shape).strip().lower()
        if rs in _OSM_ROOF:
            return _OSM_ROOF[rs]
    h = _hash01(seed)
    if height_m <= small_max:
        if bias in _LOW_BIAS and h < 0.6:
            return bias
        if h < 0.45:
            return "hipped"
        if h < 0.78:
            return "gabled"
        if h < 0.90:
            return "pyramidal"
        return "skillion"
    if bias in _HIGH_BIAS and h < 0.6:
        return bias
    return "parapet" if h < 0.55 else "flat"
