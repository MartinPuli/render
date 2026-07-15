"""
cityprofiles.py — perfiles arquitectonicos de una zona (modulo puro, sin bpy).

En vez de reglas globales por ciudad (que ensuciaban la generalizacion), se
CLASIFICA la zona a partir de estadisticas reales de sus edificios OSM y se
derivan defaults con los que rellenar huecos cuando OSM viene pobre (sin altura,
sin roof:shape, sin material). Asi los tejidos bajos densos se clasifican como
'informal_dense' y los distritos de torres como 'modern_towers'
(torres, azotea), sin nombrar ninguna ciudad.

Testeable sin Blender ni Overpass. Se engancha en place_to_3d (adjunta
scene["profile"] + scene["profile_defaults"]) y blender_build lo usa como sesgo
para los edificios sin tag.
"""

PROFILES = (
    "modern_towers",
    "historic_center",
    "industrial",
    "informal_dense",
    "residential_lowrise",
    "mixed",
)

# Defaults por perfil para rellenar datos ausentes. `roof_bias` es el techo
# preferido para edificios SIN roof:shape; `wall_hint` orienta el material.
PROFILE_DEFAULTS = {
    "modern_towers":       {"default_height": 24.0, "roof_bias": "flat",     "wall_hint": "glass"},
    "historic_center":     {"default_height": 9.0,  "roof_bias": "gabled",   "wall_hint": "masonry"},
    "industrial":          {"default_height": 8.0,  "roof_bias": "skillion", "wall_hint": "concrete"},
    "informal_dense":      {"default_height": 4.5,  "roof_bias": "flat",     "wall_hint": "brick"},
    "residential_lowrise": {"default_height": 7.0,  "roof_bias": "hipped",   "wall_hint": "masonry"},
    "mixed":               {"default_height": 9.0,  "roof_bias": None,       "wall_hint": None},
}

_PITCHED = {"gabled", "hipped", "half-hipped", "gambrel", "mansard", "pyramidal", "saltbox"}
_INDUSTRIAL = {"industrial", "warehouse", "factory", "hangar", "manufacture"}


def _stats(buildings):
    n = len(buildings)
    heights = sorted(float(b.get("height", 0.0) or 0.0) for b in buildings)
    med = heights[n // 2] if n else 0.0
    tall = sum(1 for h in heights if h >= 28.0) / n if n else 0.0
    low = sum(1 for h in heights if h <= 8.0) / n if n else 0.0
    roofs = [str(b.get("roof_shape") or "").lower() for b in buildings]
    pitched = sum(1 for r in roofs if r in _PITCHED) / n if n else 0.0
    types = [str(b.get("type") or "").lower() for b in buildings]
    industrial = sum(1 for t in types if t in _INDUSTRIAL) / n if n else 0.0
    return {"n": n, "median_h": med, "tall_frac": tall, "low_frac": low,
            "pitched_frac": pitched, "industrial_frac": industrial}


def classify_profile(buildings):
    """Devuelve uno de PROFILES a partir de la mezcla de alturas/techos/tipos."""
    s = _stats(buildings)
    if s["n"] == 0:
        return "mixed"
    # Muchas torres = distrito moderno aunque la mediana sea baja (retail/cocheras
    # entre torres la bajan). Sirve tanto tall_frac alto como mediana + tall_frac.
    if s["tall_frac"] >= 0.20 or (s["tall_frac"] >= 0.12 and s["median_h"] >= 20.0):
        return "modern_towers"
    if s["pitched_frac"] >= 0.35 and s["median_h"] <= 14.0:
        return "historic_center"
    if s["industrial_frac"] >= 0.25:
        return "industrial"
    if s["low_frac"] >= 0.70 and s["pitched_frac"] < 0.10:
        return "informal_dense"
    if s["low_frac"] >= 0.50:
        return "residential_lowrise"
    return "mixed"


def profile_defaults(profile):
    """Copia de los defaults del perfil (default_height, roof_bias, wall_hint)."""
    return dict(PROFILE_DEFAULTS.get(profile, PROFILE_DEFAULTS["mixed"]))


def classify(buildings):
    """Atajo: (profile, defaults) para adjuntar al scene.json."""
    p = classify_profile(buildings)
    return p, profile_defaults(p)
