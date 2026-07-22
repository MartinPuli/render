"""Generic, source-aware football-stadium detail for the editable block build.

The module is identity-free: detection and layout use normalized OSM semantics,
geometry, and per-run style only.  Club colors, text, crests, verified stand
dimensions, and cardinal exceptions belong in output/<slug>/style.json.

Parts are referenced to each other, not to convenient constants: the canopy to
the seating crest, the light ring to the canopy, the turf deck to whatever the
generic area pass laid over it.  The ``clearances`` block in the returned report
publishes those relationships so an evaluator can gate on them -- counting
objects cannot tell a good stadium from one with the roof floating over the
crowd.

TODO(camera): the generator builds no cameras, so a per-run script placing an
eye inside the bowl still has to dodge the geometry itself.  The failure mode is
specific: an eye between the canopy soffit and the top of the rear drum sits in
a closed band and renders solid black.  ``clearances.bowl_camera_max_eye_m`` is
published for that check, but the placement itself lives in the run script.
"""

import math

import bmesh
import bpy
from mathutils import Matrix


FOOTBALL_SPORTS = {"soccer", "football", "futsal"}
STADIUM_TYPES = {"stadium", "grandstand", "arena"}


DEFAULT_CONFIG = {
    "enabled": "auto",
    "pitch_length": None,
    "pitch_width": None,
    "pitch_osm_id": None,
    "rows_long": 16,
    "rows_end": 12,
    "stand_depth_long": 22.0,
    "stand_depth_end": 17.0,
    "stand_height_long": 18.0,
    "stand_height_end": 13.0,
    # The stand envelope/roof height is not the same thing as the seating rake.
    # When unset, bowl height is derived from a realistic row rise and capped by
    # the stand envelope.  Per-run verified values may override either side.
    "bowl_height_long": None,
    "bowl_height_end": None,
    "first_row_height": 0.55,
    "row_rise": 0.46,
    "rake_curve": 0.20,
    "runoff": 6.0,
    "sections_long": 8,
    "sections_end": 5,
    "aisle_width": 1.25,
    "roof_enabled": True,
    "roof_sides": "auto",
    "roof_coverage": 0.78,
    "roof_alpha": 1.0,
    # The canopy hangs over the SEATING RAKE, not over the structural envelope.
    # Referencing it to ``stand_height_*`` left roofs floating up to 15 m above
    # the last row.  ``roof_clearance`` is the gap from the top of the last row
    # to the underside of the canopy; ``roof_height`` overrides it outright with
    # a verified absolute height above ground.
    "roof_clearance": 6.0,
    "roof_height": None,
    "floodlights": True,
    "floodlight_height": 34.0,
    "seat_pattern": "alternating_sections",
    # A club ground reads by its colour bands, not by its seat count.  With
    # sections the band width is hostage to the structural bay spacing; with
    # ``seat_bands`` the bowl is striped by bearing, independently of how many
    # sections the frame has.  ``terrace_bands`` paints the same stripe on the
    # concrete so standing sectors and any gap in the seating keep the pattern.
    "seat_bands": 0,
    "terrace_bands": 0,
    # Sectors of standing terrace (concrete steps, no seats).  Each entry is
    # {"sides": [...]} or {"center_deg": .., "half_deg": ..} plus an optional
    # "max_height"/"min_height" window in metres above the bowl floor, which is
    # how a ground gets standing lower decks under a seated upper tier.
    "standing_sectors": [],
    "seat_spacing": 0.82,
    "max_seat_modules": 8000,
    "bowl_shape": "rectangular",
    "bowl_segments": 96,
    "bowl_exponent": 6.0,
    # Plan of the bowl.  The default derives BOTH edges of the ring from the
    # pitch, which forces an oval envelope and a stand of constant depth.  A
    # drum-shaped ground is the opposite: the outer radius is the given, the
    # first row wraps the pitch as an octagon inside it, and the corona between
    # them varies in depth on its own (thin at the corners, deep on the bands).
    # Set ``outer_radius`` (or bowl_shape="circular") to switch modes.
    "outer_radius": None,
    "inner_profile": None,        # None | "superellipse" | "octagon"
    "inner_margin": None,         # first row off the touchline; defaults to runoff
    "inner_corner_margin": None,  # first row off the pitch corner; defaults to runoff/2
    # How fast the inner plan dissolves into the outer one across the rows.
    # 1.0 spaces the rows evenly through the corona, which is what a radial row
    # layout does; above 1.0 the inner profile keeps its grip on the lower deck
    # but the first rows get shallow -- check ``row_depth_min_m`` in the report
    # before raising it, a row under ~0.7 m deep cannot hold a seat.
    "plan_blend": 1.0,
    "tier_break_rows": [],
    "vomitory_count": 8,
    "vomitory_width": 4.2,
    "vomitory_row_span": 4,
    "exterior_skin": False,
    "exterior_skin_height": 10.5,
    "exterior_skin_offset": 1.8,
    "lighting_mode": "towers",
    "roof_ring_lights": 48,
    "signage_text": None,
    "primary_color": [0.10, 0.22, 0.55],
    "secondary_color": [0.78, 0.12, 0.10],
    "seat_color": [0.16, 0.28, 0.62],
    "concrete_color": [0.43, 0.44, 0.43],
    "roof_color": [0.28, 0.31, 0.34],
    "steel_color": [0.12, 0.14, 0.16],
    "safety_color": [0.86, 0.55, 0.05],
    "grass_color": [0.06, 0.31, 0.08],
    "grass_alt_color": [0.08, 0.39, 0.10],
    # Turf sized to the painted lines leaves the whole run-off bare, which
    # renders as a pale apron between the touchline and the first row.
    # "stand_face" grows the turf to the inner face of the terraces instead.
    "turf_extent": "pitch",       # "pitch" | "stand_face"
    "turf_margin": 0.0,           # extra metres of grass past that extent
}


def _deep_update(base, override):
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value
    return base


def config_for(style):
    config = _deep_update(dict(DEFAULT_CONFIG), style.get("football_stadium") or {})
    return config


def _ring(points):
    result = [tuple(map(float, point[:2])) for point in (points or [])]
    if len(result) > 1 and math.hypot(result[0][0] - result[-1][0],
                                      result[0][1] - result[-1][1]) < 1e-6:
        result.pop()
    return result


def polygon_area(points):
    ring = _ring(points)
    if len(ring) < 3:
        return 0.0
    return abs(sum(
        ring[index][0] * ring[(index + 1) % len(ring)][1]
        - ring[(index + 1) % len(ring)][0] * ring[index][1]
        for index in range(len(ring)))) * 0.5


def centroid(points):
    ring = _ring(points)
    if not ring:
        return 0.0, 0.0
    return (sum(point[0] for point in ring) / len(ring),
            sum(point[1] for point in ring) / len(ring))


def principal_axis(points):
    ring = _ring(points)
    cx, cy = centroid(ring)
    xx = sum((x - cx) ** 2 for x, _ in ring)
    yy = sum((y - cy) ** 2 for _, y in ring)
    xy = sum((x - cx) * (y - cy) for x, y in ring)
    angle = 0.5 * math.atan2(2.0 * xy, xx - yy)
    return math.cos(angle), math.sin(angle)


def is_football_pitch(area):
    return (str(area.get("type") or "").lower() == "pitch"
            and str(area.get("sport") or "").lower() in FOOTBALL_SPORTS)


def is_stadium_building(building):
    tokens = {str(building.get(key) or "").lower()
              for key in ("type", "kind", "building_part")}
    return bool(tokens & STADIUM_TYPES)


def detect(scene, style=None):
    """Detect a football-stadium task from normalized semantics, not identity."""
    style = style or {}
    config = config_for(style)
    mode = config.get("enabled", "auto")
    if mode is False:
        return False
    if mode is True:
        return True
    if scene.get("scene_kind") == "football_stadium":
        return True
    profile = scene.get("stadium_profile") or {}
    if profile.get("football"):
        return True
    pitches = [area for area in scene.get("areas", []) if is_football_pitch(area)]
    stadiums = [item for item in scene.get("buildings", []) if is_stadium_building(item)]
    stadium_sites = [area for area in scene.get("areas", [])
                     if str(area.get("type") or "").lower() == "stadium"]
    return bool(pitches and (stadiums or stadium_sites))


def resolve_layout(scene, style=None):
    """Resolve a metric local stadium frame from the mapped pitch/site."""
    style = style or {}
    config = config_for(style)
    pitches = [area for area in scene.get("areas", []) if is_football_pitch(area)]
    stadium_buildings = [
        item for item in scene.get("buildings", []) if is_stadium_building(item)]
    stadium_sites = [
        area for area in scene.get("areas", [])
        if str(area.get("type") or "").lower() == "stadium"]

    focus = (style.get("focus") or {}).get("point") or [0.0, 0.0]
    focus = float(focus[0]), float(focus[1])
    preferred_id = config.get("pitch_osm_id")
    pitch = next((item for item in pitches
                  if preferred_id is not None
                  and str(item.get("osm_id")) == str(preferred_id)), None)
    if pitch is None and pitches:
        def pitch_score(item):
            c = centroid(item.get("polygon", []))
            distance = math.hypot(c[0] - focus[0], c[1] - focus[1])
            area = polygon_area(item.get("polygon", []))
            # Penalize tiny training/futsal fields when a full-size pitch is
            # available nearby, but keep proximity as the dominant selector.
            size_penalty = 120.0 if area < 2500.0 else 0.0
            return distance + size_penalty
        pitch = min(pitches, key=pitch_score)

    pitch_center = centroid((pitch or {}).get("polygon", [])) if pitch else focus
    site_candidates = stadium_sites or stadium_buildings
    site = min(
        site_candidates,
        key=lambda item: math.hypot(
            centroid(item.get("polygon") or item.get("footprint") or [])[0] - pitch_center[0],
            centroid(item.get("polygon") or item.get("footprint") or [])[1] - pitch_center[1]),
    ) if site_candidates else None
    pitch_points = ((pitch or {}).get("polygon") or
                    (site or {}).get("polygon") or
                    (site or {}).get("footprint") or [])
    if len(_ring(pitch_points)) < 3:
        return None

    center = centroid(pitch_points)
    ux, uy = principal_axis(pitch_points)
    vx, vy = -uy, ux
    projections_u = [(x - center[0]) * ux + (y - center[1]) * uy
                     for x, y in _ring(pitch_points)]
    projections_v = [(x - center[0]) * vx + (y - center[1]) * vy
                     for x, y in _ring(pitch_points)]
    extent_u = max(projections_u) - min(projections_u)
    extent_v = max(projections_v) - min(projections_v)
    if extent_v > extent_u:
        ux, uy, vx, vy = vx, vy, -ux, -uy
        extent_u, extent_v = extent_v, extent_u

    measured_length = extent_u if pitch else 0.0
    measured_width = extent_v if pitch else 0.0
    length = config.get("pitch_length")
    width = config.get("pitch_width")
    length = float(length) if length else (measured_length if 80.0 <= measured_length <= 125.0 else 105.0)
    width = float(width) if width else (measured_width if 45.0 <= measured_width <= 90.0 else 68.0)

    roof_setting = config.get("roof_sides", "auto")
    roof_side_source = "style"
    if isinstance(roof_setting, str) and roof_setting.lower() == "auto":
        mapped_sides = set()
        max_distance = max(length, width) * 0.5 + max(
            float(config["stand_depth_long"]), float(config["stand_depth_end"])) + 30.0
        for item in scene.get("buildings", []):
            open_roof = (str(item.get("structure_mode") or "").lower() == "roof_only"
                         or str(item.get("type") or "").lower() in
                         {"roof", "canopy", "carport"})
            if not open_roof or polygon_area(item.get("footprint", [])) < 120.0:
                continue
            roof_center = centroid(item.get("footprint", []))
            dx, dy = roof_center[0] - center[0], roof_center[1] - center[1]
            du, dv = dx * ux + dy * uy, dx * vx + dy * vy
            if math.hypot(du, dv) > max_distance:
                continue
            if abs(dv) / max(1.0, width) >= abs(du) / max(1.0, length):
                mapped_sides.add("north" if dv >= 0 else "south")
            else:
                mapped_sides.add("east" if du >= 0 else "west")
        if mapped_sides:
            config["roof_sides"] = sorted(mapped_sides)
            roof_side_source = "osm_roof_footprints"
        else:
            config["roof_sides"] = ["north", "south"]
            roof_side_source = "procedural_default"

    suppression_radius = (max(length, width) * 0.5
                          + max(float(config["stand_depth_long"]),
                                float(config["stand_depth_end"])) + 35.0)
    suppressed = []
    for item in stadium_buildings:
        item_center = centroid(item.get("footprint", []))
        if (item.get("osm_id") is not None
                and math.hypot(item_center[0] - center[0],
                               item_center[1] - center[1]) <= suppression_radius
                and item.get("osm_id") not in suppressed):
            suppressed.append(item.get("osm_id"))
    # When the specialized build supplies its own verified/configured roof,
    # suppress only large mapped open-cover footprints centered on this pitch.
    # Otherwise a generic filled roof slab can incorrectly cap the field.
    if config.get("roof_enabled", True):
        pitch_area = max(1.0, length * width)
        for item in scene.get("buildings", []):
            open_roof = (str(item.get("structure_mode") or "").lower() == "roof_only"
                         or str(item.get("type") or "").lower() in
                         {"roof", "canopy", "carport"})
            footprint = item.get("footprint", [])
            item_center = centroid(footprint)
            if (open_roof and item.get("osm_id") is not None
                    and polygon_area(footprint) >= pitch_area * 0.35
                    and math.hypot(item_center[0] - center[0],
                                   item_center[1] - center[1]) <= max(length, width) * 0.35
                    and item.get("osm_id") not in suppressed):
                suppressed.append(item.get("osm_id"))
    # OSM Simple 3D stadiums often contain hundreds of building:part wedges
    # under one outline.  If the specialized stadium replaces one mapped roof
    # or shell part, it must replace its siblings too; otherwise both systems
    # occupy the bowl and the generic parts hide the generated seats.
    suppressed_outline_ids = {
        str(item.get("outline_osm_id"))
        for item in scene.get("buildings", [])
        if item.get("outline_osm_id") is not None
        and str(item.get("osm_id")) in {str(value) for value in suppressed}
    }
    if suppressed_outline_ids:
        for item in scene.get("buildings", []):
            if (item.get("is_building_part")
                    and str(item.get("outline_osm_id")) in suppressed_outline_ids
                    and item.get("osm_id") is not None
                    and item.get("osm_id") not in suppressed):
                suppressed.append(item.get("osm_id"))
    return {
        "center": center,
        "axis_u": (ux, uy),
        "axis_v": (vx, vy),
        "angle": math.atan2(uy, ux),
        "pitch_length": round(length, 3),
        "pitch_width": round(width, 3),
        "pitch_osm_id": (pitch or {}).get("osm_id"),
        "pitch_source": "osm" if pitch else "inferred_football_standard",
        "site_osm_id": (site or {}).get("osm_id"),
        "suppressed_building_ids": suppressed,
        "suppressed_building_part_count": sum(
            1 for item in scene.get("buildings", [])
            if item.get("is_building_part")
            and item.get("osm_id") in suppressed),
        "roof_side_source": roof_side_source,
        "config": config,
    }


def _world(layout, u, v):
    cx, cy = layout["center"]
    ux, uy = layout["axis_u"]
    vx, vy = layout["axis_v"]
    return cx + ux * u + vx * v, cy + uy * u + vy * v


def _add_box(bm, layout, u, v, z, du, dv, dz, yaw=0.0):
    x, y = _world(layout, u, v)
    matrix = (Matrix.Translation((x, y, z))
              @ Matrix.Rotation(layout["angle"] + yaw, 4, "Z")
              @ Matrix.Diagonal((du, dv, dz, 1.0)))
    bmesh.ops.create_cube(bm, size=1.0, matrix=matrix)


def _add_cylinder(bm, layout, u, v, base, radius, height, segments=10):
    x, y = _world(layout, u, v)
    bmesh.ops.create_cone(
        bm, cap_ends=True, cap_tris=False, segments=max(6, int(segments)),
        radius1=radius, radius2=radius, depth=height,
        matrix=Matrix.Translation((x, y, base + height / 2.0)))


def _superellipse_point(a, b, angle, exponent):
    """Return a rounded-rectangle/oval point in the local pitch frame."""
    power = 2.0 / max(2.0, float(exponent))
    c, s = math.cos(angle), math.sin(angle)
    return (a * math.copysign(abs(c) ** power, c),
            b * math.copysign(abs(s) ** power, s))


def _superellipse_radius(a, b, angle, exponent):
    """Polar radius of the same curve, i.e. |u/a|^n + |v/b|^n = 1."""
    n = max(2.0, float(exponent))
    ca, sa = abs(math.cos(angle)), abs(math.sin(angle))
    return ((ca / max(1e-6, a)) ** n + (sa / max(1e-6, b)) ** n) ** (-1.0 / n)


def octagon_radius(angle, half_u, half_v, half_diag):
    """Radius at ``angle`` of an eight-sided convex plan (four runs, four
    45-degree chamfers), given as the intersection of its supporting lines."""
    best = float("inf")
    lines = ((0.0, half_u), (math.pi * 0.5, half_v),
             (math.pi, half_u), (-math.pi * 0.5, half_v),
             (math.pi * 0.25, half_diag), (math.pi * 0.75, half_diag),
             (-math.pi * 0.75, half_diag), (-math.pi * 0.25, half_diag))
    for phi, dist in lines:
        cos_d = math.cos(angle - phi)
        if cos_d > 1e-6:
            best = min(best, dist / cos_d)
    return best


def bowl_plan(config, length, width):
    """Resolve the two plan curves the seating corona is cut from.

    ``superellipse`` (default) derives BOTH edges from the pitch: outer_a from
    the length and outer_b from the width.  A rectangular pitch therefore forces
    an oval envelope, and cutting the inner lip from the same curve gives a
    stand of constant depth all the way round -- which no real bowl has.

    ``circular`` inverts the order of causation, the way a drum-shaped ground is
    actually laid out: the outer radius is the given, the first row wraps the
    pitch as an octagon inside it, and the depth of the corona between them is a
    consequence -- thin where the pitch corner already reaches out, deep behind
    the touchlines.  Pure on purpose so the plan policy stays regression-testable.
    """
    runoff = float(config.get("runoff", 6.0))
    depth = float(max(config.get("stand_depth_long", 22.0),
                      config.get("stand_depth_end", 17.0)))
    exponent = max(2.0, float(config.get("bowl_exponent", 6.0)))
    inner_a, inner_b = length / 2.0 + runoff, width / 2.0 + runoff
    shape = str(config.get("bowl_shape", "rectangular")).lower()
    radius = config.get("outer_radius")
    circular = radius is not None or shape in {"circular", "circle", "drum"}
    plan = {
        "mode": "circular" if circular else "superellipse",
        "inner_a": inner_a, "inner_b": inner_b,
        "exponent": exponent, "depth": depth,
        "outer_radius": None, "octagon": None,
        "inner_profile": "superellipse",
        "blend": max(0.4, float(config.get("plan_blend", 1.0))),
    }
    if not circular:
        return plan
    plan["outer_radius"] = float(radius) if radius is not None else (
        max(length, width) / 2.0 + runoff + depth)
    profile = config.get("inner_profile")
    plan["inner_profile"] = str(profile).lower() if profile else "octagon"
    if plan["inner_profile"] == "octagon":
        margin = config.get("inner_margin")
        margin = runoff if margin is None else float(margin)
        corner = config.get("inner_corner_margin")
        corner = runoff * 0.5 if corner is None else float(corner)
        plan["octagon"] = (length / 2.0 + margin, width / 2.0 + margin,
                           (length / 2.0 + width / 2.0) / math.sqrt(2.0) + corner)
    return plan


def plan_inner_radius(plan, angle):
    if plan["octagon"] is not None:
        return octagon_radius(angle, *plan["octagon"])
    return _superellipse_radius(plan["inner_a"], plan["inner_b"], angle,
                                plan["exponent"])


def plan_outer_radius(plan, angle):
    if plan["mode"] == "circular":
        return plan["outer_radius"]
    return _superellipse_radius(plan["inner_a"] + plan["depth"],
                                plan["inner_b"] + plan["depth"], angle,
                                plan["exponent"])


def plan_radius(plan, angle, frac):
    """Radius of the terrace centre line at fraction ``frac`` of the corona.

    The blend exponent keeps the inner profile's grip over the lower rows and
    lets it dissolve into the outer envelope higher up, which is what concentric
    seating rings do between an octagonal first row and a circular drum.
    """
    inner = plan_inner_radius(plan, angle)
    # A mis-set outer_radius must not invert the corona and lay the rows
    # inward; keep at least a nominal stand depth at every bearing.
    outer = max(plan_outer_radius(plan, angle), inner + 1.0)
    return inner + (outer - inner) * (max(0.0, float(frac)) ** plan["blend"])


def plan_point(plan, angle, frac):
    """(u, v) on the terrace centre line at fraction ``frac`` of the corona."""
    if plan["mode"] == "circular":
        radius = plan_radius(plan, angle, frac)
        return radius * math.cos(angle), radius * math.sin(angle)
    grow = plan["depth"] * float(frac)
    return _superellipse_point(plan["inner_a"] + grow, plan["inner_b"] + grow,
                               angle, plan["exponent"])


def plan_depth(plan, angle, frac_lo, frac_hi):
    """Radial thickness of one row at ``angle`` -- variable in circular mode."""
    if plan["mode"] == "circular":
        return max(0.05, plan_radius(plan, angle, frac_hi)
                   - plan_radius(plan, angle, frac_lo))
    return plan["depth"] * (float(frac_hi) - float(frac_lo))


def plan_outer_offset_point(plan, angle, offset):
    """(u, v) ``offset`` metres inward from the outer envelope.

    Used by everything that hangs off the rim -- canopy, trusses, light ring --
    so those parts move together when the envelope changes.
    """
    if plan["mode"] == "circular":
        radius = max(1.0, plan["outer_radius"] - offset)
        return radius * math.cos(angle), radius * math.sin(angle)
    return _superellipse_point(plan["inner_a"] + plan["depth"] - offset,
                               plan["inner_b"] + plan["depth"] - offset,
                               angle, plan["exponent"])


def plan_inner_offset_point(plan, angle, offset):
    """(u, v) ``offset`` metres inside the first row's plan curve."""
    if plan["mode"] == "circular":
        radius = max(1.0, plan_inner_radius(plan, angle) - offset)
        return radius * math.cos(angle), radius * math.sin(angle)
    return _superellipse_point(plan["inner_a"] - offset, plan["inner_b"] - offset,
                               angle, plan["exponent"])


def plan_circularity(plan, samples=72):
    """min/max outer radius.  1.0 is a true circle; a gate can assert on it."""
    radii = [plan_outer_radius(plan, 2.0 * math.pi * i / samples)
             for i in range(samples)]
    return min(radii) / max(1e-6, max(radii))


def bowl_row_profile(config, rows, row, side="long"):
    """Return terrace top/rise using a bounded, progressively steeper rake.

    ``stand_height_*`` remains the structural envelope.  The bowl gets its own
    height so a tall facade or roof does not turn every seating row into a wall.
    This helper is pure on purpose: CI can regression-test the geometry policy.
    """
    rows = max(1, int(rows))
    row = min(rows - 1, max(0, int(row)))
    first = max(0.20, float(config.get("first_row_height", 0.55)))
    curve = min(0.65, max(0.0, float(config.get("rake_curve", 0.20))))
    nominal_rise = min(0.62, max(0.30, float(config.get("row_rise", 0.46))))
    envelope = max(1.0, float(config.get("stand_height_%s" % side, 18.0)))
    configured = config.get("bowl_height_%s" % side)
    if configured is None:
        target = min(envelope, first + nominal_rise * rows * (1.0 + curve * 0.5))
    else:
        target = min(envelope, max(first + rows * 0.30, float(configured)))
    available = max(rows * 0.30, target - first)
    weights = [1.0 + curve * (index / max(1, rows - 1))
               for index in range(rows)]
    scale = available / sum(weights)
    rises = [weight * scale for weight in weights]
    top = first + sum(rises[:row + 1])
    return {"top": top, "rise": rises[row], "bowl_height": first + sum(rises)}


ROOF_THICKNESS = 0.36
SIDE_ANGLES = {"east": 0.0, "north": math.pi * 0.5,
               "west": math.pi, "south": -math.pi * 0.5}


def is_continuous_bowl(config):
    return str(config.get("bowl_shape", "rectangular")).lower() in {
        "oval", "elliptical", "continuous_oval", "circular", "circle", "drum"}


def bowl_rows(config, side="long", continuous=False):
    """The row count each builder actually uses, in one place."""
    if continuous:
        return max(10, int(max(config["rows_long"], config["rows_end"])))
    floor = 6 if side == "long" else 5
    return max(floor, int(config.get("rows_%s" % side, 16)))


def bowl_crest_height(config, side="long", continuous=False):
    """Top of the LAST SEATING ROW above the bowl floor.

    Everything that has to sit over the crowd -- canopy, trusses, light ring --
    must reference this and not ``stand_height_*``.  The envelope is the rear
    facade; a stand with a 27 m facade and a 16 m rake is normal, and using the
    facade as the roof datum parks the canopy 11 m above the last row.
    """
    rows = bowl_rows(config, side, continuous)
    return bowl_row_profile(config, rows, rows - 1, side)["bowl_height"]


def roof_underside_height(config, side="long", continuous=False):
    """Height above the bowl floor of the canopy's underside."""
    explicit = config.get("roof_height")
    if explicit is not None:
        return max(3.0, float(explicit))
    clearance = max(2.0, float(config.get("roof_clearance", 6.0)))
    return bowl_crest_height(config, side, continuous) + clearance


def sector_centers(spec):
    """Bearings (pitch frame, +u behind the east goal) a sector spec covers."""
    centers = []
    for name in spec.get("sides") or []:
        angle = SIDE_ANGLES.get(str(name).lower())
        if angle is not None:
            centers.append(angle)
    if spec.get("center_deg") is not None:
        centers.append(math.radians(float(spec["center_deg"])))
    for value in spec.get("centers_deg") or []:
        centers.append(math.radians(float(value)))
    return centers


def standing_sector(config, angle, height):
    """True when this bowl cell is standing terrace: concrete steps, no seats.

    Grounds publish far more capacity than numbered places precisely because
    whole decks are standing room.  Without this the generator can only express
    "every terrace carries seats", and the ends read as an all-seater bowl.
    """
    for spec in config.get("standing_sectors") or []:
        if not isinstance(spec, dict):
            continue
        top = spec.get("max_height")
        if top is not None and height > float(top):
            continue
        bottom = spec.get("min_height")
        if bottom is not None and height < float(bottom):
            continue
        half = math.radians(max(1.0, float(spec.get("half_deg", 45.0))))
        for center in sector_centers(spec):
            delta = abs((angle - center + math.pi) % (2.0 * math.pi) - math.pi)
            if delta <= half:
                return True
    return False


def band_index(angle, bands):
    """Vertical colour band a bearing falls in, measured off the pitch axis."""
    bands = max(1, int(bands))
    return int(((angle % (2.0 * math.pi)) / (2.0 * math.pi)) * bands) % bands


def oval_access_mask(index, segments, section_count, row, rows, config):
    """Classify an oval terrace cell without allowing access/seat overlap."""
    section_phase = (index * section_count / max(1, segments)) % 1.0
    # One mesh segment is the minimum editable aisle at this LOD.  Selecting
    # the closest segment to each section boundary avoids the old modulo bug
    # that turned roughly a quarter of the entire bowl into giant wedges.
    aisle = min(section_phase, 1.0 - section_phase) <= (
        0.5 * section_count / max(1, segments))
    tier_break = int(row) in {int(v) for v in (config.get("tier_break_rows") or [])}

    count = max(0, int(config.get("vomitory_count", 8)))
    span = max(1, int(config.get("vomitory_row_span", 4)))
    center_row = min(rows - 1, max(0, int(round(rows * 0.38))))
    in_rows = abs(int(row) - center_row) <= span // 2
    t = 2.0 * math.pi * (index + 0.5) / max(1, segments)
    vomitory = False
    if count and in_rows:
        half_angle = math.pi / max(1, segments)
        for portal in range(count):
            center = 2.0 * math.pi * (portal + 0.5) / count
            delta = abs((t - center + math.pi) % (2.0 * math.pi) - math.pi)
            if delta <= half_angle:
                vomitory = True
                break
    return {"aisle": aisle, "tier_break": tier_break, "vomitory": vomitory}


def _add_plan_segment(bm, layout, point, t0, t1, z, radial_depth, height):
    """One prism of a ring, taking its two ends from any plan curve."""
    p0, p1 = point(t0), point(t1)
    u, v = (p0[0] + p1[0]) * 0.5, (p0[1] + p1[1]) * 0.5
    du, dv = p1[0] - p0[0], p1[1] - p0[1]
    chord = max(0.08, math.hypot(du, dv))
    yaw = math.atan2(dv, du)
    _add_box(bm, layout, u, v, z, chord + 0.05,
             max(0.04, radial_depth), height, yaw=yaw)
    return (u, v, yaw, chord)


def _add_ring_segment(bm, layout, a, b, t0, t1, exponent,
                      z, radial_depth, height, inset=0.0):
    return _add_plan_segment(
        bm, layout,
        lambda angle: _superellipse_point(a - inset, b - inset, angle, exponent),
        t0, t1, z, radial_depth, height)


def _frame_uv(layout, x, y):
    """World point -> (u, v) in the pitch frame.  Inverse of ``_world``."""
    cx, cy = layout["center"]
    ux, uy = layout["axis_u"]
    vx, vy = layout["axis_v"]
    dx, dy = x - cx, y - cy
    return dx * ux + dy * uy, dx * vx + dy * vy


def _paint_bands(bm, layout, bands, slot=1):
    """Assign alternating material slots by bearing about the pitch axis.

    Painting after the fact, per face, keeps the band width independent of the
    structural bay spacing: a bowl with 26 sections can still carry 44 stripes.
    """
    painted = 0
    for face in bm.faces:
        center = face.calc_center_median()
        u, v = _frame_uv(layout, center.x, center.y)
        if band_index(math.atan2(v, u), bands) % 2:
            face.material_index = slot
            painted += 1
    return painted


def _finalize(name, bm, material, collection, extra_materials=()):
    if not bm.faces:
        bm.free()
        return None
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    obj.data.materials.append(material)
    for extra in extra_materials:
        obj.data.materials.append(extra)
    obj["stadium_detail"] = True
    obj["provenance"] = "osm_or_procedural_inference"
    collection.objects.link(obj)
    return obj


def _collection(name, parent):
    collection = bpy.data.collections.get(name) or bpy.data.collections.new(name)
    if collection.name not in parent.children:
        try:
            parent.children.link(collection)
        except RuntimeError:
            pass
    return collection


def _material(factory, name, rgb, roughness=0.75, metallic=0.0):
    return factory(name, rgb, roughness=roughness, metallic=metallic)


def _pitch_geometry(layout, ground, mats, collection, report, area_top=0.0):
    cfg = layout["config"]
    length, width = layout["pitch_length"], layout["pitch_width"]
    # The OSM area layer is a flat polygon soup that spans the whole tile a few
    # centimetres above datum.  Built at a fixed +0.06 the turf ends up UNDER
    # it, so the pitch renders as whatever landuse colour covers the site and
    # regrading the grass materials changes nothing on screen.  Stack the whole
    # pitch deck above whatever was measured overlapping it.
    lift = max(0.0, float(area_top) + 0.02 - 0.06)
    ground = ground + lift  # every layer below is part of the same pitch deck
    # Grass runs to the foot of the stands, not to the painted lines: sized to
    # the markings it leaves the entire run-off bare and a pale apron rings the
    # field.  Opt-in so existing runs keep the geometry they were tuned against.
    margin = max(0.0, float(cfg.get("turf_margin", 0.0)))
    if str(cfg.get("turf_extent", "pitch")).lower() == "stand_face":
        runoff = float(cfg.get("runoff", 6.0))
        turf_u = length / 2.0 + runoff - 0.15 + margin
        turf_v = width / 2.0 + runoff - 0.15 + margin
    else:
        turf_u, turf_v = length / 2.0 + margin, width / 2.0 + margin

    turf = bmesh.new()
    stripe_count = 12
    stripe_length = 2.0 * turf_u / stripe_count
    for index in range(stripe_count):
        _add_box(turf, layout, -turf_u + stripe_length * (index + 0.5), 0,
                 ground + 0.06, stripe_length + 0.02, 2.0 * turf_v, 0.12)
    _finalize("Stadium_Turf_Stripes", turf, mats["grass"], collection)

    alt = bmesh.new()
    for index in range(0, stripe_count, 2):
        _add_box(alt, layout, -turf_u + stripe_length * (index + 0.5), 0,
                 ground + 0.125, stripe_length - 0.04, 2.0 * turf_v - 0.08, 0.015)
    _finalize("Stadium_Turf_Alternate", alt, mats["grass_alt"], collection)
    report.update({
        "area_layer_top_m": round(float(area_top), 4),
        "turf_lift_m": round(lift, 4),
        "turf_top_m": round(lift + 0.1325, 4),
        "markings_base_m": round(lift + 0.133, 4),
        "turf_extent_m": [round(2.0 * turf_u, 2), round(2.0 * turf_v, 2)],
        "turf_covers_runoff": str(
            cfg.get("turf_extent", "pitch")).lower() == "stand_face",
    })

    line = 0.11
    markings = bmesh.new()
    for u, v, du, dv in (
            (0, -width / 2, length, line), (0, width / 2, length, line),
            (-length / 2, 0, line, width), (length / 2, 0, line, width),
            (0, 0, line, width)):
        _add_box(markings, layout, u, v, ground + 0.142, du, dv, 0.018)
    for side in (-1, 1):
        goal_u = side * length / 2
        for depth, box_width in ((16.5, 40.32), (5.5, 18.32)):
            inner_u = goal_u - side * depth
            _add_box(markings, layout, inner_u, 0, ground + 0.142,
                     line, box_width, 0.018)
            for v in (-box_width / 2, box_width / 2):
                _add_box(markings, layout, goal_u - side * depth / 2, v,
                         ground + 0.142, depth, line, 0.018)
        _add_cylinder(markings, layout, goal_u - side * 11.0, 0,
                      ground + 0.14, 0.10, 0.025, 10)

    segments = 64
    radius = 9.15
    for index in range(segments):
        a = 2.0 * math.pi * index / segments
        b = 2.0 * math.pi * (index + 1) / segments
        u = radius * (math.cos(a) + math.cos(b)) / 2.0
        v = radius * (math.sin(a) + math.sin(b)) / 2.0
        chord = 2.0 * radius * math.sin(math.pi / segments)
        _add_box(markings, layout, u, v, ground + 0.142, chord, line, 0.018,
                 yaw=(a + b) / 2.0 + math.pi / 2.0)
    _add_cylinder(markings, layout, 0, 0, ground + 0.14, 0.10, 0.025, 10)
    _finalize("Stadium_Pitch_Markings", markings, mats["white"], collection)

    goals = bmesh.new()
    nets = bmesh.new()
    goal_width, goal_height, goal_depth = 7.32, 2.44, 2.0
    for side in (-1, 1):
        u = side * (length / 2 + 0.03)
        for v in (-goal_width / 2, goal_width / 2):
            _add_cylinder(goals, layout, u, v, ground + 0.15, 0.055,
                          goal_height, 8)
        _add_box(goals, layout, u, 0, ground + 0.15 + goal_height,
                 0.10, goal_width + 0.10, 0.10)
        back_u = u + side * goal_depth
        for v in (-goal_width / 2, goal_width / 2):
            _add_cylinder(goals, layout, back_u, v, ground + 0.15, 0.04,
                          goal_height, 8)
        for index in range(9):
            v = -goal_width / 2 + goal_width * index / 8.0
            _add_box(nets, layout, u + side * goal_depth / 2, v,
                     ground + 0.15 + goal_height / 2,
                     goal_depth, 0.018, goal_height)
        for index in range(5):
            z = ground + 0.15 + goal_height * index / 4.0
            _add_box(nets, layout, u + side * goal_depth / 2, 0, z,
                     goal_depth, goal_width, 0.014)
    _finalize("Stadium_Goals", goals, mats["white"], collection)
    _finalize("Stadium_Goal_Nets", nets, mats["net"], collection)
    report.update({"pitch_stripes": stripe_count, "pitch_marking_segments": segments + 16,
                   "goals": 2, "goal_net_lines": 28,
                   "pitch_source": layout["pitch_source"]})


def _stand_rows(layout, ground, mats, collection, report):
    cfg = layout["config"]
    if is_continuous_bowl(cfg):
        return _continuous_oval_stands(layout, ground, mats, collection, report)
    length, width = layout["pitch_length"], layout["pitch_width"]
    runoff = float(cfg["runoff"])
    concrete, seats_primary, seats_secondary, safety, dark = (
        bmesh.new(), bmesh.new(), bmesh.new(), bmesh.new(), bmesh.new())
    rows_built = sections_built = aisle_steps = vomitories = seat_modules = 0
    cleared_seat_cells = standing_cells = secondary_modules = 0
    max_row_rise = bowl_top_height = 0.0
    seat_spacing = max(0.65, float(cfg.get("seat_spacing", 0.82)))
    seat_cap = max(100, int(cfg.get("max_seat_modules", 8000)))
    seat_bands = max(0, int(cfg.get("seat_bands", 0)))

    def seat_target(section, u, v):
        # Bands are measured off the pitch axis, so the stripe stays put when
        # the bay spacing changes; sections remain the fallback.
        if seat_bands:
            return (seats_secondary if band_index(math.atan2(v, u), seat_bands) % 2
                    else seats_primary)
        if str(cfg.get("seat_pattern", "")).lower() == "alternating_sections":
            return seats_secondary if section % 2 else seats_primary
        return seats_primary

    def long_stand(side, side_name):
        nonlocal rows_built, sections_built, aisle_steps, vomitories, seat_modules
        nonlocal cleared_seat_cells, standing_cells, max_row_rise, bowl_top_height
        nonlocal secondary_modules
        rows = max(6, int(cfg["rows_long"]))
        depth = float(cfg["stand_depth_long"])
        height = float(cfg["stand_height_long"])
        sections = max(3, int(cfg["sections_long"]))
        total = length + 18.0
        aisle = min(2.0, float(cfg["aisle_width"]))
        section_width = (total - aisle * (sections - 1)) / sections
        row_depth = depth / rows
        inner = width / 2 + runoff
        portal_centers = (-total * 0.24, total * 0.24)
        portal_width = max(2.4, float(cfg.get("vomitory_width", 4.2)))
        portal_span = max(1, int(cfg.get("vomitory_row_span", 4)))
        portal_row = min(rows - 1, max(0, int(round(rows * 0.34))))
        for row in range(rows):
            profile = bowl_row_profile(cfg, rows, row, "long")
            rise = profile["rise"]
            terrace_top = ground + profile["top"]
            z = terrace_top - rise * 0.5
            max_row_rise = max(max_row_rise, rise)
            bowl_top_height = max(bowl_top_height, profile["bowl_height"])
            v = side * (inner + row_depth * (row + 0.5))
            for section in range(sections):
                u = -total / 2 + section_width / 2 + section * (section_width + aisle)
                _add_box(concrete, layout, u, v, z, section_width,
                         row_depth - 0.04, rise)
                if standing_sector(cfg, math.atan2(v, u), profile["top"]):
                    # Standing terrace: the concrete step is the accommodation.
                    standing_cells += 1
                    sections_built += 1
                    continue
                count = max(1, int(section_width / seat_spacing))
                seat_w = min(seat_spacing * 0.76, section_width / count * 0.82)
                target = seat_target(section, u, v)
                for seat_index in range(count):
                    if seat_modules >= seat_cap:
                        break
                    seat_u = u - section_width / 2 + section_width * (seat_index + 0.5) / count
                    blocked = (abs(row - portal_row) <= portal_span // 2
                               and any(abs(seat_u - center) <= portal_width * 0.5
                                       for center in portal_centers))
                    if blocked:
                        cleared_seat_cells += 1
                        continue
                    seat_v = v - side * row_depth * 0.18
                    _add_box(target, layout, seat_u, seat_v, terrace_top + 0.07,
                             seat_w, row_depth * 0.42, 0.12)
                    _add_box(target, layout, seat_u,
                             seat_v + side * row_depth * 0.18,
                             terrace_top + 0.26, seat_w,
                             max(0.07, row_depth * 0.10), 0.38)
                    seat_modules += 1
                    if target is seats_secondary:
                        secondary_modules += 1
                sections_built += 1
            for aisle_index in range(1, sections):
                u = -total / 2 + aisle_index * (section_width + aisle) - aisle / 2
                _add_box(safety, layout, u, v, terrace_top + 0.025,
                         aisle * 0.70, row_depth * 0.82, 0.06)
                aisle_steps += 1
            rows_built += 1
        portal_profile = bowl_row_profile(cfg, rows, portal_row, "long")
        portal_floor = ground + portal_profile["top"] - portal_profile["rise"]
        for u in portal_centers:
            v = side * (inner + depth * 0.34)
            _add_box(dark, layout, u, v, portal_floor - 0.08,
                     portal_width, row_depth * portal_span, 0.14)
            for offset in (-1.0, 1.0):
                _add_box(dark, layout, u + offset * portal_width * 0.48, v,
                         portal_floor + 1.45, 0.16,
                         row_depth * portal_span, 2.9)
            _add_box(dark, layout, u, v, portal_floor + 2.92,
                     portal_width, row_depth * portal_span, 0.18)
            vomitories += 1
        report["stand_sides"].append(side_name)

    def end_stand(side, side_name):
        nonlocal rows_built, sections_built, aisle_steps, vomitories, seat_modules
        nonlocal cleared_seat_cells, standing_cells, max_row_rise, bowl_top_height
        nonlocal secondary_modules
        rows = max(5, int(cfg["rows_end"]))
        depth = float(cfg["stand_depth_end"])
        height = float(cfg["stand_height_end"])
        sections = max(3, int(cfg["sections_end"]))
        total = width + 16.0
        aisle = min(2.0, float(cfg["aisle_width"]))
        section_width = (total - aisle * (sections - 1)) / sections
        row_depth = depth / rows
        inner = length / 2 + runoff
        portal_width = max(2.4, float(cfg.get("vomitory_width", 4.2)))
        portal_span = max(1, int(cfg.get("vomitory_row_span", 4)))
        portal_row = min(rows - 1, max(0, int(round(rows * 0.34))))
        for row in range(rows):
            profile = bowl_row_profile(cfg, rows, row, "end")
            rise = profile["rise"]
            terrace_top = ground + profile["top"]
            z = terrace_top - rise * 0.5
            max_row_rise = max(max_row_rise, rise)
            bowl_top_height = max(bowl_top_height, profile["bowl_height"])
            u = side * (inner + row_depth * (row + 0.5))
            for section in range(sections):
                v = -total / 2 + section_width / 2 + section * (section_width + aisle)
                _add_box(concrete, layout, u, v, z,
                         row_depth - 0.04, section_width, rise)
                if standing_sector(cfg, math.atan2(v, u), profile["top"]):
                    standing_cells += 1
                    sections_built += 1
                    continue
                count = max(1, int(section_width / seat_spacing))
                seat_w = min(seat_spacing * 0.76, section_width / count * 0.82)
                target = seat_target(section, u, v)
                for seat_index in range(count):
                    if seat_modules >= seat_cap:
                        break
                    seat_v = v - section_width / 2 + section_width * (seat_index + 0.5) / count
                    if (abs(row - portal_row) <= portal_span // 2
                            and abs(seat_v) <= portal_width * 0.5):
                        cleared_seat_cells += 1
                        continue
                    seat_u = u - side * row_depth * 0.18
                    _add_box(target, layout, seat_u, seat_v, terrace_top + 0.07,
                             row_depth * 0.42, seat_w, 0.12)
                    _add_box(target, layout,
                             seat_u + side * row_depth * 0.18, seat_v,
                             terrace_top + 0.26,
                             max(0.07, row_depth * 0.10), seat_w, 0.38)
                    seat_modules += 1
                    if target is seats_secondary:
                        secondary_modules += 1
                sections_built += 1
            for aisle_index in range(1, sections):
                v = -total / 2 + aisle_index * (section_width + aisle) - aisle / 2
                _add_box(safety, layout, u, v, terrace_top + 0.025,
                         row_depth * 0.82, aisle * 0.70, 0.06)
                aisle_steps += 1
            rows_built += 1
        portal_profile = bowl_row_profile(cfg, rows, portal_row, "end")
        portal_floor = ground + portal_profile["top"] - portal_profile["rise"]
        portal_u = side * (inner + depth * 0.34)
        _add_box(dark, layout, portal_u, 0, portal_floor - 0.08,
                 row_depth * portal_span, portal_width, 0.14)
        for offset in (-1.0, 1.0):
            _add_box(dark, layout, portal_u, offset * portal_width * 0.48,
                     portal_floor + 1.45, row_depth * portal_span, 0.16, 2.9)
        _add_box(dark, layout, portal_u, 0, portal_floor + 2.92,
                 row_depth * portal_span, portal_width, 0.18)
        vomitories += 1
        report["stand_sides"].append(side_name)

    long_stand(1, "north")
    long_stand(-1, "south")
    end_stand(1, "east")
    end_stand(-1, "west")
    terrace_bands = max(0, int(cfg.get("terrace_bands", 0)))
    terrace_extra = ()
    if terrace_bands:
        # Wherever the seating is interrupted -- a standing sector, a portal, a
        # gap between blocks -- bare concrete reads as a stripe with the colour
        # drained out.  Carry the club pattern onto the steps themselves.
        _paint_bands(concrete, layout, terrace_bands, slot=1)
        terrace_extra = (mats["seat_secondary"],)
    _finalize("Stadium_Concrete_Terraces", concrete, mats["concrete"], collection,
              extra_materials=terrace_extra)
    _finalize("Stadium_Seats_Primary", seats_primary, mats["seat_primary"], collection)
    _finalize("Stadium_Seats_Secondary", seats_secondary, mats["seat_secondary"], collection)
    _finalize("Stadium_Safety_Aisles", safety, mats["safety"], collection)
    _finalize("Stadium_Vomitories", dark, mats["dark"], collection)
    report.update({"stand_rows": rows_built, "stand_sections": sections_built,
                   "seat_modules": seat_modules,
                   "seat_modules_secondary": secondary_modules,
                   "seat_color_share_secondary": round(
                       secondary_modules / max(1, seat_modules), 4),
                   "aisle_steps": aisle_steps, "vomitories": vomitories,
                   "seat_clearance_cells": cleared_seat_cells,
                   "standing_terrace_cells": standing_cells,
                   "seat_bands": seat_bands, "terrace_bands": terrace_bands,
                   "seat_aisle_overlap_cells": 0,
                   "seat_vomitory_overlap_cells": 0,
                   "bowl_top_height": round(bowl_top_height, 3),
                   "structure_height": round(max(float(cfg["stand_height_long"]),
                                                 float(cfg["stand_height_end"])), 3),
                   "max_row_rise": round(max_row_rise, 3)})


def _continuous_oval_stands(layout, ground, mats, collection, report):
    """Build a continuous seating bowl around the pitch.

    The plan comes from ``bowl_plan``: either the legacy superellipse pair or a
    circular envelope with an octagonal first row, in which case the depth of
    every row varies with bearing instead of being constant.
    """
    cfg = layout["config"]
    length, width = layout["pitch_length"], layout["pitch_width"]
    rows = bowl_rows(cfg, "long", continuous=True)
    depth = float(max(cfg["stand_depth_long"], cfg["stand_depth_end"]))
    structure_height = float(max(cfg["stand_height_long"], cfg["stand_height_end"]))
    section_count = max(12, int(cfg["sections_long"]) * 2
                        + int(cfg["sections_end"]) * 2)
    # Seating needs finer tangential resolution than the roof shell.  Eight
    # cells per section keeps a 1-2 m aisle from swallowing a 4 m mesh wedge.
    segments = max(96, int(cfg.get("bowl_segments", 96)), section_count * 8)
    plan = bowl_plan(cfg, length, width)
    seat_spacing = max(0.65, float(cfg.get("seat_spacing", 0.82)))
    seat_cap = max(100, int(cfg.get("max_seat_modules", 8000)))
    seat_bands = max(0, int(cfg.get("seat_bands", 0)))
    tier_breaks = {int(value) for value in (cfg.get("tier_break_rows") or [])}
    concrete, seats_primary, seats_secondary = bmesh.new(), bmesh.new(), bmesh.new()
    # The tier break gets its own mesh.  Emitted into the vomitory bmesh it
    # buried 16 real openings under ~300 flat plates, and any consumer taking
    # the object's extent read almost the whole ring as "vomitory".
    safety, dark, breaks = bmesh.new(), bmesh.new(), bmesh.new()
    seat_modules = aisle_steps = cleared_seat_cells = 0
    standing_cells = tier_break_plates = secondary_modules = 0
    max_row_rise = bowl_top_height = 0.0
    min_row_depth = float("inf")
    max_row_depth = 0.0

    for row in range(rows):
        profile = bowl_row_profile(cfg, rows, row, "long")
        rise = profile["rise"]
        terrace_top = ground + profile["top"]
        z = terrace_top - rise * 0.5
        max_row_rise = max(max_row_rise, rise)
        bowl_top_height = max(bowl_top_height, profile["bowl_height"])
        frac_lo, frac_mid, frac_hi = (row / rows, (row + 0.5) / rows,
                                      (row + 1.0) / rows)

        def row_point(angle, frac=frac_mid):
            return plan_point(plan, angle, frac)

        for index in range(segments):
            t0 = 2.0 * math.pi * index / segments
            t1 = 2.0 * math.pi * (index + 1) / segments
            section = int(index * section_count / segments)
            access = oval_access_mask(index, segments, section_count,
                                      row, rows, cfg)
            aisle = access["aisle"]
            tier_break = access["tier_break"]
            vomitory = access["vomitory"]
            # A vomitory is an actual opening: no terrace prism and no seats.
            if vomitory:
                cleared_seat_cells += 1
                continue
            seg_depth = plan_depth(plan, 0.5 * (t0 + t1), frac_lo, frac_hi)
            min_row_depth = min(min_row_depth, seg_depth)
            max_row_depth = max(max_row_depth, seg_depth)
            u, v, yaw, chord = _add_plan_segment(
                concrete, layout, row_point, t0, t1, z,
                seg_depth - 0.035, rise)
            # Access surfaces are thin overlays on the concrete rake, never
            # full-height wedges capable of hiding neighboring seat modules.
            if aisle:
                _add_plan_segment(safety, layout, row_point, t0, t1,
                                  terrace_top + 0.025, seg_depth * 0.72, 0.05)
                aisle_steps += 1
            if tier_break:
                _add_plan_segment(breaks, layout, row_point, t0, t1,
                                  terrace_top + 0.02,
                                  max(0.05, seg_depth - 0.07), 0.04)
                tier_break_plates += 1
            if aisle or tier_break:
                continue
            if standing_sector(cfg, math.atan2(v, u), profile["top"]):
                standing_cells += 1
                continue
            if seat_bands:
                seat_target = (seats_secondary
                               if band_index(math.atan2(v, u), seat_bands) % 2
                               else seats_primary)
            else:
                seat_target = (seats_secondary if
                               str(cfg.get("seat_pattern", "")).lower()
                               == "alternating_sections" and section % 2
                               else seats_primary)
            count = max(1, int(chord / seat_spacing))
            tangent = (math.cos(yaw), math.sin(yaw))
            seat_w = min(seat_spacing * 0.74, chord / count * 0.82)
            for seat_index in range(count):
                if seat_modules >= seat_cap:
                    break
                offset = chord * ((seat_index + 0.5) / count - 0.5)
                su, sv = u + tangent[0] * offset, v + tangent[1] * offset
                _add_box(seat_target, layout, su, sv, terrace_top + 0.07,
                         seat_w, seg_depth * 0.40, 0.12, yaw=yaw)
                _add_box(seat_target, layout, su, sv, terrace_top + 0.26,
                         seat_w, max(0.07, seg_depth * 0.10), 0.38, yaw=yaw)
                seat_modules += 1
                if seat_target is seats_secondary:
                    secondary_modules += 1

    vomitories = 0
    portal_count = max(0, int(cfg.get("vomitory_count", 8)))
    portal_width = max(2.4, float(cfg.get("vomitory_width", 4.2)))
    portal_span = max(1, int(cfg.get("vomitory_row_span", 4)))
    portal_row = min(rows - 1, max(0, int(round(rows * 0.38))))
    portal_profile = bowl_row_profile(cfg, rows, portal_row, "long")
    portal_floor = ground + portal_profile["top"] - portal_profile["rise"]
    portal_frac = 0.38
    portal_half = 0.5 * portal_span / rows
    for index in range(portal_count):
        t = 2.0 * math.pi * (index + 0.5) / max(1, portal_count)
        u, v = plan_point(plan, t, portal_frac)
        portal_depth = max(2.2, plan_depth(plan, t,
                                           max(0.0, portal_frac - portal_half),
                                           min(1.0, portal_frac + portal_half)))
        yaw = t + math.pi / 2.0
        # Recessed floor, slim jambs, and lintel describe the opening while
        # leaving the cleared seating volume physically empty.
        _add_box(dark, layout, u, v, portal_floor - 0.08,
                 portal_width, portal_depth, 0.14, yaw=yaw)
        for offset in (-1.0, 1.0):
            tangent_u, tangent_v = math.cos(yaw), math.sin(yaw)
            ju = u + tangent_u * offset * portal_width * 0.48
            jv = v + tangent_v * offset * portal_width * 0.48
            _add_box(dark, layout, ju, jv, portal_floor + 1.45,
                     0.16, portal_depth, 2.9, yaw=yaw)
        _add_box(dark, layout, u, v, portal_floor + 2.92,
                 portal_width, portal_depth, 0.18, yaw=yaw)
        vomitories += 1

    terrace_bands = max(0, int(cfg.get("terrace_bands", 0)))
    terrace_extra = ()
    if terrace_bands:
        _paint_bands(concrete, layout, terrace_bands, slot=1)
        terrace_extra = (mats["seat_secondary"],)
    _finalize("Stadium_Continuous_Terraces", concrete, mats["concrete"], collection,
              extra_materials=terrace_extra)
    _finalize("Stadium_Seats_Primary", seats_primary, mats["seat_primary"], collection)
    _finalize("Stadium_Seats_Secondary", seats_secondary, mats["seat_secondary"], collection)
    _finalize("Stadium_Safety_Aisles", safety, mats["safety"], collection)
    _finalize("Stadium_Vomitories", dark, mats["dark"], collection)
    _finalize("Stadium_Tier_Break_Plates", breaks, mats["dark"], collection)
    report["stand_sides"].extend(["north", "south", "east", "west"])
    report.update({
        "bowl_shape": plan["mode"] if plan["mode"] == "circular" else "continuous_oval",
        "bowl_plan": {
            "mode": plan["mode"],
            "inner_profile": plan["inner_profile"],
            "outer_radius_m": (round(plan["outer_radius"], 3)
                               if plan["outer_radius"] else None),
            "envelope_circularity": round(plan_circularity(plan), 4),
            "row_depth_min_m": round(min_row_depth, 3) if max_row_depth else 0.0,
            "row_depth_max_m": round(max_row_depth, 3),
        },
        "continuous_rows": rows,
        "tier_break_rows": sorted(tier_breaks),
        "tier_break_plates": tier_break_plates,
        "bowl_segments": segments,
        "stand_rows": rows * 4,
        "stand_sections": rows * section_count,
        "seat_modules": seat_modules,
        "seat_modules_secondary": secondary_modules,
        "seat_color_share_secondary": round(
            secondary_modules / max(1, seat_modules), 4),
        "aisle_steps": aisle_steps,
        "vomitories": vomitories,
        "seat_bands": seat_bands,
        "terrace_bands": terrace_bands,
        "standing_terrace_cells": standing_cells,
        "seat_clearance_cells": cleared_seat_cells,
        "seat_aisle_overlap_cells": 0,
        "seat_vomitory_overlap_cells": 0,
        "bowl_top_height": round(bowl_top_height, 3),
        "structure_height": round(structure_height, 3),
        "max_row_rise": round(max_row_rise, 3),
    })


def _structure_and_roofs(layout, ground, mats, collections, report):
    cfg = layout["config"]
    if is_continuous_bowl(cfg):
        return _continuous_oval_structure(layout, ground, mats, collections, report)
    length, width = layout["pitch_length"], layout["pitch_width"]
    runoff = float(cfg["runoff"])
    frames, rails, roofs, trusses = (bmesh.new(), bmesh.new(),
                                     bmesh.new(), bmesh.new())
    columns = beams = rail_segments = roof_panels = roof_supports = 0

    for side in (-1, 1):
        depth, height = float(cfg["stand_depth_long"]), float(cfg["stand_height_long"])
        outer_v = side * (width / 2 + runoff + depth)
        total = length + 18.0
        bays = max(6, int(total / 12.0))
        for index in range(bays + 1):
            u = -total / 2 + total * index / bays
            _add_box(frames, layout, u, outer_v, ground + height / 2,
                     0.42, 0.42, height)
            columns += 1
        for level in (0.28, 0.55, 0.82):
            _add_box(frames, layout, 0, outer_v, ground + height * level,
                     total, 0.38, 0.38)
            beams += 1
        inner_v = side * (width / 2 + runoff - 0.45)
        for z in (ground + 1.1, ground + 2.25):
            _add_box(rails, layout, 0, inner_v, z, total, 0.08, 0.08)
            rail_segments += 1

    for side in (-1, 1):
        depth, height = float(cfg["stand_depth_end"]), float(cfg["stand_height_end"])
        outer_u = side * (length / 2 + runoff + depth)
        total = width + 16.0
        bays = max(5, int(total / 11.0))
        for index in range(bays + 1):
            v = -total / 2 + total * index / bays
            _add_box(frames, layout, outer_u, v, ground + height / 2,
                     0.42, 0.42, height)
            columns += 1
        for level in (0.34, 0.68):
            _add_box(frames, layout, outer_u, 0, ground + height * level,
                     0.38, total, 0.38)
            beams += 1
        inner_u = side * (length / 2 + runoff - 0.45)
        for z in (ground + 1.1, ground + 2.25):
            _add_box(rails, layout, inner_u, 0, z, 0.08, total, 0.08)
            rail_segments += 1

    roof_sides = set(str(value).lower() for value in cfg.get("roof_sides", []))
    # Referenced to the SEATING RAKE.  ``stand_height_long`` is the rear facade;
    # using it as the roof datum floated the canopy metres above the last row.
    roof_under = roof_underside_height(cfg, "long")
    roof_z = ground + roof_under + ROOF_THICKNESS / 2.0
    if cfg.get("roof_enabled", True):
        for side, name in ((1, "north"), (-1, "south")):
            if name not in roof_sides:
                continue
            depth = float(cfg["stand_depth_long"])
            total = length + 20.0
            roof_depth = depth * float(cfg.get("roof_coverage", 0.78))
            v = side * (width / 2 + runoff + depth * 0.56)
            _add_box(roofs, layout, 0, v, roof_z, total, roof_depth,
                     ROOF_THICKNESS)
            roof_panels += 1
            bays = max(5, int(total / 18.0))
            outer_v = side * (width / 2 + runoff + depth * 0.94)
            for index in range(bays + 1):
                u = -total / 2 + total * index / bays
                # The rear column stops at the canopy it carries, so both move
                # together when the rake changes.
                _add_box(trusses, layout, u, outer_v, ground + roof_under / 2.0,
                         0.34, 0.34, roof_under)
                _add_box(trusses, layout, u, v, roof_z - 0.40,
                         0.26, roof_depth, 0.26)
                roof_supports += 1
    report.update({
        "roof_underside_m": round(roof_under, 3),
        "roof_above_last_row_m": round(
            roof_under - bowl_crest_height(cfg, "long"), 3),
        "roof_datum": "bowl_crest",
    })

    _finalize("Stadium_Rear_Structure", frames, mats["concrete"], collections["structure"])
    _finalize("Stadium_Railings", rails, mats["steel"], collections["details"])
    _finalize("Stadium_Stand_Roofs", roofs, mats["roof"], collections["roof"])
    _finalize("Stadium_Roof_Trusses", trusses, mats["steel"], collections["roof"])
    report.update({"rear_columns": columns, "rear_beams": beams,
                   "railing_segments": rail_segments, "roof_panels": roof_panels,
                   "roof_supports": roof_supports})


def _continuous_oval_structure(layout, ground, mats, collections, report):
    """Build the exposed frame, patterned outer drum, and continuous roof ring."""
    cfg = layout["config"]
    length, width = layout["pitch_length"], layout["pitch_width"]
    depth = float(max(cfg["stand_depth_long"], cfg["stand_depth_end"]))
    height = float(max(cfg["stand_height_long"], cfg["stand_height_end"]))
    segments = max(48, int(cfg.get("bowl_segments", 96)))
    plan = bowl_plan(cfg, length, width)
    frames, rails, roofs, trusses = bmesh.new(), bmesh.new(), bmesh.new(), bmesh.new()
    skin_primary, skin_secondary, portals = bmesh.new(), bmesh.new(), bmesh.new()
    columns = beams = rail_segments = roof_panels = roof_supports = skin_panels = 0

    def outer_point(angle, offset=0.0):
        return plan_outer_offset_point(plan, angle, offset)

    # A continuous exposed concrete skeleton around the outer perimeter -- a
    # true circle in circular mode, the pitch-derived superellipse otherwise.
    for index in range(segments):
        t0 = 2.0 * math.pi * index / segments
        t1 = 2.0 * math.pi * (index + 1) / segments
        if index % 3 == 0:
            u, v = outer_point(t0)
            _add_box(frames, layout, u, v, ground + height / 2,
                     0.46, 0.46, height, yaw=t0)
            columns += 1
        for level in (0.28, 0.55, 0.82):
            _add_plan_segment(frames, layout, outer_point, t0, t1,
                              ground + height * level, 0.34, 0.34)
            beams += 1
        # The rail follows the FIRST ROW, which in circular mode is the octagon
        # and not a scaled copy of the envelope.
        for z in (ground + 1.1, ground + 2.25):
            _add_plan_segment(rails, layout,
                              lambda angle: plan_inner_offset_point(plan, angle, 0.45),
                              t0, t1, z, 0.07, 0.07)
            rail_segments += 1

        if cfg.get("exterior_skin", False):
            skin_h = min(height * 0.72, float(cfg.get("exterior_skin_height", 10.5)))
            skin_offset = max(0.35, float(cfg.get("exterior_skin_offset", 1.8)))
            # Keep regular dark access portals between groups of facade fins.
            if index % max(8, segments // 12) == 0:
                _add_plan_segment(portals, layout,
                                  lambda angle: outer_point(angle, -skin_offset),
                                  t0, t1, ground + 2.2, 0.28, 4.4)
            else:
                target = skin_secondary if (index // 3) % 2 else skin_primary
                _add_plan_segment(target, layout,
                                  lambda angle: outer_point(angle, -skin_offset),
                                  t0, t1, ground + skin_h / 2, 0.22, skin_h)
                skin_panels += 1

    # Referenced to the seating rake, not to the rear facade: see
    # ``roof_underside_height``.
    roof_under = roof_underside_height(cfg, "long", continuous=True)
    roof_z = ground + roof_under + ROOF_THICKNESS / 2.0
    if cfg.get("roof_enabled", True):
        coverage = min(1.0, max(0.2, float(cfg.get("roof_coverage", 0.78))))
        roof_depth = depth * coverage
        for index in range(segments):
            t0 = 2.0 * math.pi * index / segments
            t1 = 2.0 * math.pi * (index + 1) / segments
            _add_plan_segment(roofs, layout,
                              lambda angle: outer_point(angle, roof_depth * 0.48),
                              t0, t1, roof_z, roof_depth, ROOF_THICKNESS)
            roof_panels += 1
            if index % 4 == 0:
                # Rear roof columns sit outside the last terrace.  The old
                # negative offset pierced the top seating row every four bays.
                u, v = outer_point(t0, -0.45)
                _add_box(trusses, layout, u, v, ground + roof_under / 2.0,
                         0.34, 0.34, roof_under, yaw=t0)
                inner_u, inner_v = outer_point(t0, roof_depth)
                mid_u, mid_v = (u + inner_u) / 2, (v + inner_v) / 2
                yaw = math.atan2(inner_v - v, inner_u - u)
                _add_box(trusses, layout, mid_u, mid_v, roof_z - 0.42,
                         math.hypot(inner_u - u, inner_v - v), 0.26, 0.26,
                         yaw=yaw)
                roof_supports += 1

    _finalize("Stadium_Rear_Structure", frames, mats["concrete"], collections["structure"])
    _finalize("Stadium_Railings", rails, mats["steel"], collections["details"])
    _finalize("Stadium_Continuous_Roof", roofs, mats["roof"], collections["roof"])
    _finalize("Stadium_Roof_Trusses", trusses, mats["steel"], collections["roof"])
    _finalize("Stadium_Exterior_Primary", skin_primary, mats["seat_primary"], collections["structure"])
    _finalize("Stadium_Exterior_Secondary", skin_secondary, mats["seat_secondary"], collections["structure"])
    _finalize("Stadium_Exterior_Portals", portals, mats["dark"], collections["structure"])
    report.update({
        "rear_columns": columns, "rear_beams": beams,
        "railing_segments": rail_segments, "roof_panels": roof_panels,
        "roof_supports": roof_supports, "exterior_skin_panels": skin_panels,
        "roof_support_seat_clearance": 0.45,
        "roof_underside_m": round(roof_under, 3),
        "roof_above_last_row_m": round(
            roof_under - bowl_crest_height(cfg, "long", continuous=True), 3),
        "roof_datum": "bowl_crest",
        "drum_top_m": round(height, 3),
    })


def _field_furniture_and_lighting(layout, ground, mats, collections, report):
    cfg = layout["config"]
    length, width = layout["pitch_length"], layout["pitch_width"]
    details, lights, light_heads = bmesh.new(), bmesh.new(), bmesh.new()

    # Two dugouts, a centered player tunnel, perimeter fence, and scoreboard.
    for u in (-17.0, 17.0):
        v = width / 2 + 3.0
        _add_box(details, layout, u, v, ground + 1.35, 11.0, 2.2, 0.18)
        for offset in (-5.0, 0.0, 5.0):
            _add_box(details, layout, u + offset, v + 0.85,
                     ground + 0.72, 0.12, 0.12, 1.35)
    _add_box(details, layout, 0, -(width / 2 + float(cfg["runoff"]) + 2.0),
             ground + 1.6, 5.0, 5.5, 3.2)
    fence_offset = 2.3
    for v in (-width / 2 - fence_offset, width / 2 + fence_offset):
        for z in (ground + 0.8, ground + 1.8):
            _add_box(details, layout, 0, v, z, length + 5.0, 0.07, 0.07)
    for u in (-length / 2 - fence_offset, length / 2 + fence_offset):
        for z in (ground + 0.8, ground + 1.8):
            _add_box(details, layout, u, 0, z, 0.07, width + 5.0, 0.07)
    score_u = length / 2 + float(cfg["runoff"]) + float(cfg["stand_depth_end"]) + 3.0
    _add_box(details, layout, score_u, 0, ground + 10.5, 0.8, 13.0, 7.0)
    _add_box(details, layout, score_u - 0.45, 0, ground + 11.0, 0.25, 11.5, 5.4)

    towers = lamps = 0
    lighting_mode = str(cfg.get("lighting_mode", "towers")).lower()
    if cfg.get("floodlights", True) and lighting_mode == "towers":
        tower_h = float(cfg.get("floodlight_height", 34.0))
        u = length / 2 + float(cfg["runoff"]) + float(cfg["stand_depth_end"]) + 7.0
        v = width / 2 + float(cfg["runoff"]) + float(cfg["stand_depth_long"]) + 7.0
        for su in (-1, 1):
            for sv in (-1, 1):
                _add_cylinder(lights, layout, su * u, sv * v, ground,
                              0.38, tower_h, 10)
                _add_box(light_heads, layout, su * u, sv * v,
                         ground + tower_h + 0.8, 1.2, 8.0, 2.3)
                towers += 1
                lamps += 12
    elif lighting_mode == "roof_ring":
        # A light ring is not a free-standing object: it is bolted to the inner
        # rim of the canopy.  Deriving its height and radius from the same roof
        # helper keeps the two together instead of leaving lamps in mid-air the
        # moment the roof moves.
        depth = float(max(cfg["stand_depth_long"], cfg["stand_depth_end"]))
        continuous = is_continuous_bowl(cfg)
        count = max(16, int(cfg.get("roof_ring_lights", 48)))
        plan = bowl_plan(cfg, length, width)
        coverage = min(1.0, max(0.2, float(cfg.get("roof_coverage", 0.78))))
        roof_depth = depth * coverage
        roof_under = roof_underside_height(cfg, "long", continuous=continuous)
        # 0.5 m inside the canopy's inner edge, hanging 6 cm below its soffit.
        rim_offset = roof_depth * 0.98 - 0.5
        bank_z = ground + roof_under - 0.06 - 0.12
        for index in range(count):
            t = 2.0 * math.pi * index / count
            u, v = plan_outer_offset_point(plan, t, rim_offset)
            _add_box(light_heads, layout, u, v, bank_z,
                     1.25, 0.34, 0.24, yaw=t + math.pi / 2.0)
            lamps += 1
        # Only claim the canopy as the anchor when there is a canopy: a ring
        # hung off a roof that was switched off is exactly the kind of floating
        # geometry this is meant to prevent.
        report["floodlight_anchor"] = ("roof_underside" if cfg.get("roof_enabled", True)
                                       else "bowl_rim")
        report["floodlight_gap_to_roof_m"] = 0.06
    _finalize("Stadium_Field_Furniture", details, mats["steel"], collections["details"])
    _finalize("Stadium_Floodlight_Towers", lights, mats["steel"], collections["lighting"])
    _finalize("Stadium_Floodlight_Banks", light_heads, mats["light"], collections["lighting"])
    report.update({"dugouts": 2, "player_tunnels": 1, "scoreboards": 1,
                   "fence_segments": 8, "floodlight_towers": towers,
                   "floodlight_lamps": lamps, "lighting_mode": lighting_mode})
    report.setdefault("floodlight_anchor",
                      "tower_top" if towers else "none")


def _run_identity(layout, ground, mats, collection, report):
    """Add optional run-configured signage without hard-coding venue identity."""
    text = str(layout["config"].get("signage_text") or "").strip()
    if not text:
        report["signage_objects"] = 0
        return
    cfg = layout["config"]
    height = float(max(cfg["stand_height_long"], cfg["stand_height_end"]))
    made = 0
    # Sit on the outer envelope wherever that is, so a drum-shaped ground does
    # not end up with its name floating inside or outside the facade.
    plan = bowl_plan(cfg, layout["pitch_length"], layout["pitch_width"])
    for side in (-1, 1):
        local = plan_outer_offset_point(plan, side * math.pi / 2.0, -0.62)
        u, v = _world(layout, local[0], local[1])
        curve = bpy.data.curves.new("Stadium_Run_Signage_%s" % side, "FONT")
        curve.body = text
        curve.align_x = "CENTER"
        curve.align_y = "CENTER"
        curve.size = max(2.2, min(4.8, 42.0 / max(8, len(text))))
        curve.extrude = 0.055
        curve.bevel_depth = 0.012
        obj = bpy.data.objects.new("Stadium_Run_Signage_%s" % side, curve)
        obj.location = (u, v, ground + min(height * 0.50, 7.6))
        # A font curve stands up with a +90 deg X rotation; -90 also stands it
        # up but flips the glyphs, so the sign on that side read mirrored from
        # the street.  Keep X positive and turn the whole object round in Z
        # instead, which is what "face outward" actually means.
        obj.rotation_euler = (math.radians(90.0), 0.0,
                              layout["angle"] + (math.pi if side > 0 else 0.0))
        obj.data.materials.append(mats["signage"])
        obj["stadium_detail"] = True
        obj["provenance"] = "per_run_style"
        collection.objects.link(obj)
        made += 1
    report["signage_objects"] = made
    report["signage_faces_outward"] = True


def area_layer_top(scene, layout):
    """Highest OSM area polygon overlapping the pitch, in local metres.

    The generic area pass lays a flat polygon soup a few centimetres above
    datum.  Anything the stadium builds below that is simply invisible, so the
    pitch deck has to be measured against it rather than assume a clear +0.06.
    Mirrors the offsets used when those polygons are emitted (z + 0.03 plus a
    per-index anti-z-fight nudge under 0.02).
    """
    center = layout["center"]
    reach = math.hypot(layout["pitch_length"], layout["pitch_width"]) / 2.0
    top = 0.0
    for area in scene.get("areas", []) or []:
        ring = _ring(area.get("polygon", []))
        if len(ring) < 3:
            continue
        xs = [point[0] for point in ring]
        ys = [point[1] for point in ring]
        if (max(xs) < center[0] - reach or min(xs) > center[0] + reach
                or max(ys) < center[1] - reach or min(ys) > center[1] + reach):
            continue
        top = max(top, float(area.get("z", 0.0) or 0.0) + 0.05)
    return top


def build(scene, style, parent_collection, ground_at, material_factory):
    """Build the detected stadium and return measurable component counts."""
    if not detect(scene, style):
        return None
    layout = resolve_layout(scene, style)
    if layout is None:
        return None
    ground = float(ground_at(*layout["center"]))
    collections = {
        "pitch": _collection("BLK_STADIUM_PITCH", parent_collection),
        "stands": _collection("BLK_STADIUM_STANDS", parent_collection),
        "structure": _collection("BLK_STADIUM_STRUCTURE", parent_collection),
        "roof": _collection("BLK_STADIUM_ROOFS", parent_collection),
        "details": _collection("BLK_STADIUM_DETAILS", parent_collection),
        "lighting": _collection("BLK_STADIUM_LIGHTING", parent_collection),
    }
    cfg = layout["config"]
    mats = {
        "grass": _material(material_factory, "BLK_STADIUM_GRASS_A", cfg.get("grass_color", [0.06, 0.31, 0.08]), 0.92),
        "grass_alt": _material(material_factory, "BLK_STADIUM_GRASS_B", cfg.get("grass_alt_color", [0.08, 0.39, 0.10]), 0.92),
        "white": _material(material_factory, "BLK_STADIUM_LINE", [0.92, 0.94, 0.90], 0.72),
        "net": _material(material_factory, "BLK_STADIUM_NET", [0.70, 0.73, 0.76], 0.80),
        "concrete": _material(material_factory, "BLK_STADIUM_CONCRETE", cfg["concrete_color"], 0.90),
        "seat_primary": _material(
            material_factory, "BLK_STADIUM_SEATS_PRIMARY",
            cfg.get("seat_color") or cfg["primary_color"], 0.56),
        "seat_secondary": _material(
            material_factory, "BLK_STADIUM_SEATS_SECONDARY",
            cfg["secondary_color"], 0.56),
        "safety": _material(material_factory, "BLK_STADIUM_SAFETY", cfg.get("safety_color", [0.86, 0.55, 0.05]), 0.68),
        "steel": _material(material_factory, "BLK_STADIUM_STEEL", cfg["steel_color"], 0.35, 0.72),
        "roof": _material(material_factory, "BLK_STADIUM_ROOF", cfg["roof_color"], 0.58, 0.32),
        "dark": _material(material_factory, "BLK_STADIUM_VOID", [0.025, 0.03, 0.035], 0.80),
        "light": _material(material_factory, "BLK_STADIUM_LIGHT", [0.95, 0.88, 0.63], 0.24),
        "signage": _material(material_factory, "BLK_STADIUM_SIGNAGE", [0.94, 0.97, 1.0], 0.34),
    }
    roof_alpha = min(1.0, max(0.08, float(cfg.get("roof_alpha", 1.0))))
    if roof_alpha < 1.0:
        roof_mat = mats["roof"]
        roof_mat.diffuse_color = (*roof_mat.diffuse_color[:3], roof_alpha)
        bsdf = roof_mat.node_tree.nodes.get("Principled BSDF") if roof_mat.use_nodes else None
        if bsdf:
            bsdf.inputs["Base Color"].default_value[3] = roof_alpha
            transmission = bsdf.inputs.get("Transmission Weight") or bsdf.inputs.get("Transmission")
            if transmission:
                transmission.default_value = min(0.45, 1.0 - roof_alpha + 0.12)
        if hasattr(roof_mat, "surface_render_method"):
            roof_mat.surface_render_method = "DITHERED"
    report = {
        "detected": True,
        "layout_source": "osm_pitch" if layout["pitch_osm_id"] is not None else "inferred",
        "pitch_osm_id": layout["pitch_osm_id"],
        "site_osm_id": layout["site_osm_id"],
        "pitch_dimensions_m": [layout["pitch_length"], layout["pitch_width"]],
        "orientation_deg": round(math.degrees(layout["angle"]), 3),
        "suppressed_building_ids": layout["suppressed_building_ids"],
        "suppressed_building_part_count": layout.get(
            "suppressed_building_part_count", 0),
        "roof_sides": list(layout["config"].get("roof_sides", [])),
        "roof_side_source": layout["roof_side_source"],
        "stand_sides": [],
        "provenance": {"massing": "osm_or_metric_inference",
                       "microdetail": "procedural_inference"},
    }
    _pitch_geometry(layout, ground, mats, collections["pitch"], report,
                    area_top=area_layer_top(scene, layout))
    _stand_rows(layout, ground, mats, collections["stands"], report)
    _structure_and_roofs(layout, ground, mats, collections, report)
    _field_furniture_and_lighting(layout, ground, mats, collections, report)
    _run_identity(layout, ground, mats, collections["details"], report)
    # Relational measurements, not counts.  A gate that only counts objects
    # passes a stadium whose roof floats over the crowd and whose turf is buried
    # under the area layer; these are the numbers that catch that.  The free
    # band between the canopy underside and the drum parapet is also where a
    # per-run bowl camera has to keep its eye to avoid rendering solid black.
    report["clearances"] = {
        "roof_above_last_row_m": report.get("roof_above_last_row_m"),
        "roof_underside_m": report.get("roof_underside_m"),
        "bowl_top_height_m": report.get("bowl_top_height"),
        "structure_height_m": report.get("structure_height"),
        "turf_lift_m": report.get("turf_lift_m"),
        "turf_top_m": report.get("turf_top_m"),
        "markings_base_m": report.get("markings_base_m"),
        "floodlight_anchor": report.get("floodlight_anchor"),
        # Free interior height for an in-bowl camera: floor to canopy soffit.
        "bowl_camera_max_eye_m": round(
            max(2.0, float(report.get("roof_underside_m") or 0.0) - 1.2), 3),
    }
    return report
