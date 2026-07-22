"""Declarative acceptance gates for block mode.

Defaults confirm that the scene produced objects and the expected renders.
Run-specific targets belong in ``output/<slug>/eval.json``; the evaluator has no
hard-coded knowledge of places, identities, or universal quantities.

Usage:
  blender -b output/<slug>/<slug>_blocks.blend \
    -P scripts/blocks_eval.py -- output/<slug> [--eval <eval.json>]

COUNTING GATES ARE BLIND. The Racing/Cilindro run
(``output/racing-cilindro-one-shot/eval_report.json``) reported every gate
green while the canopy floated 15.3 m over the last seat row, the club name
rendered mirrored, the turf was buried under the OSM area layer, the light ring
hung in mid-air and a pair of stair cores sat 4 m inside the bowl. Every gate
that ran measured a QUANTITY (objects, seat modules, steps) or a
NON-OVERLAP, and none of those can tell a good stadium from a broken one.

The gates below measure RELATIONS between parts instead: how far the roof sits
above the seats it covers, which layer is on top of which, how much of the bowl
floor the turf actually reaches, whether hanging things still touch what holds
them up. They are all opt-in through ``eval.json`` and additionally guarded on
``football_stadium.detected``, so a run that is not a stadium can never fail
them. Turn the whole family on with ``"stadium_relational": true`` and override
individual thresholds alongside it.
"""

import json
import math
import os
import sys


DEFAULT_GATES = {
    "min_objects": 1,
    "required_files": ["blocks_oblique.png", "blocks_aerial.png", "blocks_holdout.png"],
    "max_black_pct": 0.4,
}


# Thresholds for the relational stadium family. Every number is justified in
# _stadium_relational_gates against what the Cilindro run actually shipped.
# ``min_stadium_circularity`` is deliberately absent: only a run whose style
# declares a round plan should be held to it, so it stays fully opt-in.
STADIUM_RELATIONAL_DEFAULTS = {
    "stadium_roof_over_last_row_range": [4.0, 10.0],
    "require_stadium_pitch_layer_order": True,
    "min_stadium_turf_coverage": 0.90,
    "max_stadium_hanging_gap_m": 1.0,
    "require_stadium_signage_upright": True,
    "max_stadium_seat_color_share": 0.90,
    "max_stadium_bowl_intrusion_m": 1.0,
    "require_stadium_camera_clearance": True,
}


# The parts stadium_detail.py emits, grouped by the role each plays in a
# relation. Both bowl shapes ("stacked" and "continuous_oval") are listed
# because a run only ever produces one of the two.
STADIUM_PARTS = {
    "seats": ("Stadium_Seats_Primary", "Stadium_Seats_Secondary"),
    "terraces": ("Stadium_Concrete_Terraces", "Stadium_Continuous_Terraces"),
    "roof": ("Stadium_Stand_Roofs", "Stadium_Continuous_Roof"),
    "turf": ("Stadium_Turf_Stripes", "Stadium_Turf_Alternate"),
    "markings": ("Stadium_Pitch_Markings",),
    "hanging": ("Stadium_Floodlight_Banks",),
    "envelope": ("Stadium_Rear_Structure", "Stadium_Exterior_Primary",
                 "Stadium_Exterior_Secondary"),
}

# Everything the generator builds as part of the stadium proper. Used as the
# allowlist for the bowl-intrusion gate: anything else that reaches inside the
# outer envelope is a foreign volume, INCLUDING objects a per-run polish script
# named "Stadium_*" (the Cilindro's Stadium_Stair_Core_E/W were exactly that).
STADIUM_OWN_PARTS = frozenset((
    "Stadium_Turf_Stripes", "Stadium_Turf_Alternate", "Stadium_Pitch_Markings",
    "Stadium_Goals", "Stadium_Goal_Nets",
    "Stadium_Concrete_Terraces", "Stadium_Continuous_Terraces",
    "Stadium_Seats_Primary", "Stadium_Seats_Secondary",
    "Stadium_Safety_Aisles", "Stadium_Vomitories",
    "Stadium_Rear_Structure", "Stadium_Railings",
    "Stadium_Stand_Roofs", "Stadium_Continuous_Roof", "Stadium_Roof_Trusses",
    "Stadium_Exterior_Primary", "Stadium_Exterior_Secondary",
    "Stadium_Exterior_Portals",
    "Stadium_Field_Furniture", "Stadium_Floodlight_Towers",
    "Stadium_Floodlight_Banks",
))

# Flat map layers: polygon soups that legitimately run under the whole tile and
# therefore under the bowl. They are excluded from the intrusion test by the
# thickness rule as well, but skipping them by name keeps the scan cheap.
GROUND_LAYER_PREFIXES = ("BLK_Areas_", "BLK_Roads_", "BLK_Ground", "BLK_Water",
                         "BLK_Terrain")


def load_gate_config(eval_path=None):
    config = dict(DEFAULT_GATES)
    if eval_path and os.path.exists(eval_path):
        with open(eval_path, encoding="utf-8") as stream:
            user = json.load(stream)
        config.update(user.get("gates", user))
    if config.pop("stadium_relational", False):
        # One switch turns the whole family on; explicit per-key thresholds in
        # eval.json still win, so a run can loosen a single gate without
        # losing the rest.
        for key, value in STADIUM_RELATIONAL_DEFAULTS.items():
            config.setdefault(key, value)
    return config


def _png_black_pct(path):
    """Return the percentage of nearly black pixels using Blender's image API."""
    import bpy

    image = bpy.data.images.load(path)
    try:
        pixels = list(image.pixels)
        count = len(pixels) // 4
        if not count:
            return 100.0
        black = sum(
            1
            for index in range(0, len(pixels), 4)
            if pixels[index] < 0.04
            and pixels[index + 1] < 0.04
            and pixels[index + 2] < 0.04
        )
        return 100.0 * black / count
    finally:
        bpy.data.images.remove(image)


def _reported_feature_heights(report):
    """Index reported heights by ID and name, including legacy reports."""
    indexed = {}
    features = list(report.get("features", [])) + list(report.get("landmarks", []))
    for feature in features:
        height = feature.get("height")
        if height is None:
            continue
        for key in (feature.get("id"), feature.get("osm_id"), feature.get("name")):
            if key not in (None, ""):
                indexed[str(key)] = float(height)
    return indexed


def _world_points(obj, limit=60000):
    """World-space vertex coordinates of a mesh object.

    Sub-sampled with a fixed stride above ``limit`` vertices: the relational
    gates only read extremes and radial profiles, and the bowl is built from
    hundreds of repeated modules, so a deterministic stride costs a few
    centimetres of precision on meshes where the alternative is a
    million-iteration Python loop per object.
    """
    from mathutils import Vector

    mesh = getattr(obj, "data", None)
    vertices = getattr(mesh, "vertices", None)
    if not vertices:
        return []
    total = len(vertices)
    flat = [0.0] * (total * 3)
    vertices.foreach_get("co", flat)
    stride = max(1, -(-total // limit))
    matrix = obj.matrix_world
    points = []
    for index in range(0, total, stride):
        base = index * 3
        world = matrix @ Vector((flat[base], flat[base + 1], flat[base + 2]))
        points.append((world.x, world.y, world.z))
    return points


def _part_points(objects, names, limit=60000):
    """Concatenated world points of every part that exists under ``names``."""
    points = []
    for name in names:
        obj = objects.get(name)
        if obj is not None and getattr(obj, "type", None) == "MESH":
            points.extend(_world_points(obj, limit=limit))
    return points


def _radial_profile(points, centre, bins=180, mode="max"):
    """Per-angle radius profile about ``centre``; None where nothing landed.

    Working in polar bins rather than a world bounding box is what makes these
    gates survive a rotated stadium - the Cilindro sits 77.1 deg off the world
    X axis, and every axis-aligned measurement of it is wrong by construction.
    """
    cx, cy = centre
    profile = [None] * bins
    span = 2.0 * math.pi
    for x, y, _z in points:
        dx, dy = x - cx, y - cy
        radius = math.hypot(dx, dy)
        index = int((math.atan2(dy, dx) + math.pi) / span * bins) % bins
        current = profile[index]
        if current is None or (radius > current if mode == "max" else radius < current):
            profile[index] = radius
    return profile


def _fill_profile(profile, max_gap=None):
    """Interpolate short holes in a radial profile; leave real gaps alone.

    A hole is not the same as a missing wall. Big flat quads only carry
    vertices at their corners, so a perfectly closed ring can still leave empty
    bins - those get interpolated. A run of empty bins longer than ``max_gap``
    means the ring genuinely stops there (a three-sided ground), and stays None
    so the caller can tell the two apart.
    """
    bins = len(profile)
    max_gap = max_gap or max(2, bins // 20)
    filled = list(profile)
    known = [index for index, radius in enumerate(profile) if radius is not None]
    if not known:
        return filled
    for position, start in enumerate(known):
        end = known[(position + 1) % len(known)]
        gap = (end - start) % bins
        if gap <= 1 or gap - 1 > max_gap:
            continue
        for step in range(1, gap):
            weight = step / gap
            filled[(start + step) % bins] = (
                profile[start] * (1.0 - weight) + profile[end] * weight)
    return filled


def _edge_samples(obj, step=1.0, limit=40000):
    """World points along a mesh's polygon edges, not just at its corners.

    The turf is a handful of very large quads: a radial profile of its
    vertices leaves most angular bins empty and a perfectly covered floor reads
    as bare. Walking the edges at ``step`` metres fills the bins from the
    outline itself, which is the thing coverage is actually about. Area would
    not work here - the generator lays alternating stripes on top of a base
    plane, so footprints double-count.
    """
    from mathutils import Vector

    mesh = getattr(obj, "data", None)
    if not getattr(mesh, "polygons", None):
        return []
    matrix = obj.matrix_world
    world = [matrix @ Vector(vertex.co) for vertex in mesh.vertices]
    points = []
    for polygon in mesh.polygons:
        loop = list(polygon.vertices)
        for index, vertex in enumerate(loop):
            start = world[vertex]
            end = world[loop[(index + 1) % len(loop)]]
            points.append((start.x, start.y, start.z))
            steps = int((end - start).length / step)
            for cut in range(1, min(steps, 64)):
                weight = cut / steps
                points.append((start.x + (end.x - start.x) * weight,
                               start.y + (end.y - start.y) * weight,
                               start.z + (end.z - start.z) * weight))
        if len(points) > limit:
            break
    return points


def _bbox_world(obj):
    """(min_x, max_x, min_y, max_y, min_z, max_z) of an object's bound box."""
    from mathutils import Vector

    corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    return (min(p.x for p in corners), max(p.x for p in corners),
            min(p.y for p in corners), max(p.y for p in corners),
            min(p.z for p in corners), max(p.z for p in corners))


def _seat_colour_share(objects, names):
    """Largest share of seat surface painted a single material.

    Weighted by polygon area, and pooled ACROSS objects by material name: the
    generator splits seats into a primary and a secondary object, so counting
    per object would report two colours even when the secondary mesh is empty.
    """
    by_material = {}
    for name in names:
        obj = objects.get(name)
        mesh = getattr(obj, "data", None) if obj is not None else None
        polygons = getattr(mesh, "polygons", None)
        if not polygons:
            continue
        count = len(polygons)
        areas = [0.0] * count
        slots = [0] * count
        polygons.foreach_get("area", areas)
        polygons.foreach_get("material_index", slots)
        materials = list(mesh.materials) or [None]
        for area, slot in zip(areas, slots):
            material = materials[slot] if slot < len(materials) else None
            key = material.name if material is not None else "%s:slot%d" % (name, slot)
            by_material[key] = by_material.get(key, 0.0) + area
    total = sum(by_material.values())
    if total <= 0.0:
        return None, 0
    share = max(by_material.values()) / total
    # Slivers of a third material (a stray safety strip) should not read as a
    # colour scheme, so only materials over 2% of the seating count.
    significant = sum(1 for area in by_material.values() if area / total >= 0.02)
    return share, significant


def measure_stadium_scene(objects=None, cameras=None, bins=180):
    """Measure the part-to-part relations the counting gates cannot see.

    Returns a plain dict so ``evaluate_report`` stays importable without
    Blender. Every entry is optional: a missing part leaves its keys absent and
    the gate that needs them reports ``missing:<part>`` instead of crashing.
    """
    if objects is None:
        import bpy
        objects = {obj.name: obj for obj in bpy.data.objects}
        cameras = [obj for obj in bpy.data.objects if getattr(obj, "type", None) == "CAMERA"]
    cameras = cameras or []
    scene = {}

    markings = _part_points(objects, STADIUM_PARTS["markings"])
    turf = _part_points(objects, STADIUM_PARTS["turf"])
    seats = _part_points(objects, STADIUM_PARTS["seats"])
    terraces = _part_points(objects, STADIUM_PARTS["terraces"])
    roof = _part_points(objects, STADIUM_PARTS["roof"])
    envelope = _part_points(objects, STADIUM_PARTS["envelope"]) or terraces

    # The painted lines are the one object that is unambiguously centred on the
    # playing surface, so they anchor the polar frame; terraces are the
    # fallback for a run with no markings.
    anchor = markings or turf or terraces
    if not anchor:
        return scene
    centre = (0.5 * (min(p[0] for p in anchor) + max(p[0] for p in anchor)),
              0.5 * (min(p[1] for p in anchor) + max(p[1] for p in anchor)))
    scene["centre"] = [round(centre[0], 3), round(centre[1], 3)]

    if seats:
        scene["seat_top_z"] = round(max(p[2] for p in seats), 3)
        share, distinct = _seat_colour_share(objects, STADIUM_PARTS["seats"])
        if share is not None:
            scene["seat_colour_share"] = round(share, 4)
            scene["seat_colour_materials"] = distinct
    if roof:
        scene["roof_top_z"] = round(max(p[2] for p in roof), 3)
        scene["roof_bottom_z"] = round(min(p[2] for p in roof), 3)
        roof_outer = [r for r in _radial_profile(roof, centre, bins, "max") if r is not None]
        roof_inner = [r for r in _radial_profile(roof, centre, bins, "min") if r is not None]
        if roof_outer and roof_inner:
            scene["roof_radius_max"] = round(max(roof_outer), 3)
            scene["roof_radius_min"] = round(min(roof_inner), 3)
    if turf:
        scene["turf_min_z"] = round(min(p[2] for p in turf), 3)
        scene["turf_max_z"] = round(max(p[2] for p in turf), 3)
    if markings:
        scene["markings_min_z"] = round(min(p[2] for p in markings), 3)

    # Top of the OSM area layer, restricted to the polygons that actually
    # overlap the pitch: the layer spans the whole tile, and only the part over
    # the pitch can bury the turf.
    if markings:
        mx0, mx1 = min(p[0] for p in markings), max(p[0] for p in markings)
        my0, my1 = min(p[1] for p in markings), max(p[1] for p in markings)
        area_top = None
        for name, obj in objects.items():
            if not name.startswith("BLK_Areas_") or getattr(obj, "type", None) != "MESH":
                continue
            ax0, ax1, ay0, ay1, _az0, az1 = _bbox_world(obj)
            if ax1 < mx0 or ax0 > mx1 or ay1 < my0 or ay0 > my1:
                continue
            area_top = az1 if area_top is None else max(area_top, az1)
        if area_top is not None:
            scene["area_layer_top_z"] = round(area_top, 3)

    # Turf coverage: how much of the bowl floor the green actually reaches.
    # Measured against the inner face of the stand rather than against the
    # painted lines, because the run-off belongs to the bowl floor too - that
    # is exactly the strip the Cilindro left bare.
    bowl_floor = None
    if terraces:
        floor_z = min(point[2] for point in terraces)
        front = [point for point in terraces if point[2] <= floor_z + 3.0] or terraces
        bowl_floor = _fill_profile(_radial_profile(front, centre, bins, "min"))
    if turf and bowl_floor:
        # Each turf layer is scored on its own and the worst one is the answer:
        # the Cilindro's base plane was fine while Stadium_Turf_Alternate
        # stopped 8.2 m short, and a union would have hidden that.
        per_layer = {}
        for name in STADIUM_PARTS["turf"]:
            obj = objects.get(name)
            if obj is None or getattr(obj, "type", None) != "MESH":
                continue
            reach = _fill_profile(
                _radial_profile(_edge_samples(obj), centre, bins, "max"))
            ratios = [min(1.0, (reach[index] or 0.0) / bowl_floor[index])
                      for index in range(bins) if bowl_floor[index]]
            if ratios:
                per_layer[name] = round(sum(ratios) / len(ratios), 4)
        if per_layer:
            scene["turf_coverage_by_layer"] = per_layer
            scene["turf_coverage"] = min(per_layer.values())

    # Circularity of the plan: the per-angle OUTER edge of the built ring,
    # compared 5th percentile against 95th. Percentiles rather than raw
    # min/max because a single portal notch or stair recess would otherwise
    # decide the answer, while an ellipse runs short over a whole quadrant and
    # survives the trim.
    plan = terraces + envelope
    if plan:
        profile = _fill_profile(_radial_profile(plan, centre, bins, "max"))
        filled = sorted(radius for radius in profile if radius is not None)
        if filled:
            low = filled[int(0.05 * (len(filled) - 1))]
            high = filled[int(0.95 * (len(filled) - 1))]
            scene["plan_radius_p5"] = round(low, 3)
            scene["plan_radius_p95"] = round(high, 3)
            scene["plan_bins_filled"] = len(filled)
            # A long hole in the profile means the ring genuinely stops there,
            # which for a declared round plan is the failure itself.
            scene["plan_circularity"] = (
                0.0 if len(filled) < bins else round(low / high, 4))

    # Hanging elements: the light ring is mounted on the canopy, so its gap to
    # the roof underside is the whole of its correctness.
    if roof:
        gaps = {}
        roof_bottom = min(p[2] for p in roof)
        for name in STADIUM_PARTS["hanging"]:
            points = _part_points(objects, (name,))
            if points:
                gaps[name] = round(roof_bottom - max(p[2] for p in points), 3)
        if gaps:
            scene["hanging_gap_m"] = gaps

    # Signage: a FONT object laid flat and rotated -90 deg about X stands up
    # with its glyph up-vector pointing down, which reads mirrored from
    # outside; a negative world determinant is the same defect via scale.
    signage = []
    for name, obj in objects.items():
        if "Signage" not in name:
            continue
        rot_x = float(obj.rotation_euler[0]) if hasattr(obj, "rotation_euler") else 0.0
        signage.append({
            "name": name,
            "upright": rot_x >= -1e-3,
            "mirrored": obj.matrix_world.determinant() < 0.0,
        })
    if signage:
        scene["signage"] = sorted(signage, key=lambda item: item["name"])

    # Foreign volumes inside the bowl, measured against two different
    # boundaries because they answer two different questions.
    #
    #   "floor" - the inner face of the stands. Anything standing in there is
    #     on the pitch or the run-off: a building the stadium detector failed
    #     to suppress, or a block-mode volume dropped on the playing surface.
    #     Objects the stadium passes build are excluded by name, since dugouts,
    #     tunnels, perimeter boards and boxes live inside the bowl by design.
    #     This is the default and it has no false alarms on the Cilindro.
    #   "ring" - the outer edge of the seating deck, and every non-core object
    #     including per-run "Stadium_*" additions. This is the strict reading
    #     that catches a service core carved out of the stand (the Cilindro's
    #     Stadium_Stair_Core_E/W started at u = 86 with the drum at r = 90), at
    #     the cost of also flagging neighbouring buildings that abut the stand
    #     - three of them really do, at r 85-93 against a 96 m ring. Opt in
    #     with "stadium_bowl_intrusion_reference": "ring".
    #
    # Flat layers cannot invade a bowl visually, so anything under 1 m tall is
    # skipped, and the bound box prefilter keeps the scan cheap on a city tile.
    boundaries = {}
    if bowl_floor:
        boundaries["floor"] = bowl_floor
    ring_source = terraces or envelope
    if ring_source:
        boundaries["ring"] = _fill_profile(
            _radial_profile(ring_source, centre, bins, "max"))
    for label, profile in boundaries.items():
        reach = max(radius for radius in profile if radius is not None)
        scene["bowl_%s_radius_max" % label] = round(reach, 3)
        intrusions = []
        for name, obj in objects.items():
            if getattr(obj, "type", None) != "MESH" or name in STADIUM_OWN_PARTS:
                continue
            if name.startswith(GROUND_LAYER_PREFIXES):
                continue
            if label == "floor" and name.startswith("Stadium_"):
                continue
            bx0, bx1, by0, by1, bz0, bz1 = _bbox_world(obj)
            if bz1 - bz0 < 1.0:
                continue
            near_x = max(bx0 - centre[0], 0.0, centre[0] - bx1)
            near_y = max(by0 - centre[1], 0.0, centre[1] - by1)
            if math.hypot(near_x, near_y) > reach:
                continue
            depth = 0.0
            for x, y, _z in _world_points(obj):
                dx, dy = x - centre[0], y - centre[1]
                index = int((math.atan2(dy, dx) + math.pi) / (2.0 * math.pi) * bins) % bins
                limit = profile[index]
                if limit is None:
                    continue
                depth = max(depth, limit - math.hypot(dx, dy))
            if depth > 0.0:
                intrusions.append({"name": name, "depth_m": round(depth, 3)})
        scene["bowl_intrusions_%s" % label] = sorted(
            intrusions, key=lambda item: -item["depth_m"])[:12]

    # Cameras buried in the canopy. BLK_CamBowl sat at z = 25.0 with the roof
    # slab at 24.90 and rendered pure black; the black-pixel gate only catches
    # that after a render, this catches it from geometry alone.
    if roof and scene.get("roof_radius_min") is not None:
        buried = []
        for camera in cameras:
            location = camera.matrix_world.translation
            radius = math.hypot(location.x - centre[0], location.y - centre[1])
            inside_slab = (scene["roof_bottom_z"] - 0.2 <= location.z
                           <= scene["roof_top_z"] + 0.2)
            inside_ring = scene["roof_radius_min"] <= radius <= scene["roof_radius_max"]
            if inside_slab and inside_ring:
                buried.append(camera.name)
        scene["cameras_in_roof"] = sorted(buried)

    return scene


def _gate_missing(part):
    """A configured relation that could not be measured is a failure, not a
    pass: the whole point of this family is that silence looked like success.
    """
    return {"pass": False, "value": "missing:%s" % part, "want": "measurable"}


def _stadium_relational_gates(report, config, scene, gates):
    """Part-to-part gates for stadium runs. Silent for every other run.

    Two guards keep a non-stadium run safe: each gate needs its key present in
    eval.json, and the whole family is skipped unless the build report says a
    stadium was detected.
    """
    requested = [key for key in STADIUM_RELATIONAL_DEFAULTS if key in config]
    if "min_stadium_circularity" in config:
        requested.append("min_stadium_circularity")
    if not requested:
        return
    stadium = report.get("football_stadium") or {}
    if not stadium.get("detected"):
        return
    if scene is None:
        # Called outside Blender (tests, CI) - nothing to measure, and failing
        # here would only punish the caller for not having a scene.
        return

    if "stadium_roof_over_last_row_range" in config:
        # The Cilindro shipped with roof_z derived from stand_height (27.5 m,
        # the structural envelope) instead of bowl_height (15.8 m, the actual
        # rake), leaving the canopy 15.3 m over the last row - and it passed.
        # Real covered bowls clear the back row by roughly one storey: below
        # ~4 m the roof clips the top row's sightline, above ~10 m it stops
        # reading as cover and the stand looks unroofed from the pitch.
        low, high = [float(value) for value in config["stadium_roof_over_last_row_range"]]
        if "roof_top_z" not in scene or "seat_top_z" not in scene:
            gates["stadium_roof_over_last_row"] = _gate_missing("roof/seats")
        else:
            value = round(scene["roof_top_z"] - scene["seat_top_z"], 3)
            gates["stadium_roof_over_last_row"] = {
                "pass": low <= value <= high, "value": value, "want": [low, high],
            }

    if config.get("require_stadium_pitch_layer_order"):
        # The OSM area layer is a flat polygon soup at z ~= 0.06 covering the
        # whole tile. The turf was built below it and was therefore invisible;
        # grading the grass materials changed nothing on screen. Correct
        # stacking is areas < turf < painted lines.
        needed = ("turf_min_z", "turf_max_z", "markings_min_z")
        if any(key not in scene for key in needed):
            gates["stadium_pitch_layer_order"] = _gate_missing("turf/markings")
        else:
            area_top = scene.get("area_layer_top_z")
            above_areas = area_top is None or scene["turf_min_z"] > area_top
            below_lines = scene["markings_min_z"] >= scene["turf_max_z"]
            gates["stadium_pitch_layer_order"] = {
                "pass": bool(above_areas and below_lines),
                "value": {"areas": area_top, "turf": [scene["turf_min_z"],
                                                      scene["turf_max_z"]],
                          "markings": scene["markings_min_z"]},
                "want": "areas < turf < markings",
            }

    if "min_stadium_turf_coverage" in config:
        # The turf was sized to the painted lines instead of to the inner face
        # of the stands, so the entire run-off rendered as a pale ring. The
        # run-off is ~7.5 m on a ~60 m radius, i.e. 12% of the reach: anything
        # under 0.90 means a bare band wide enough to see from the air.
        wanted = float(config["min_stadium_turf_coverage"])
        if "turf_coverage" not in scene:
            gates["stadium_turf_coverage_min"] = _gate_missing("turf/terraces")
        else:
            value = scene["turf_coverage"]
            gates["stadium_turf_coverage_min"] = {
                "pass": value >= wanted, "value": value, "want": wanted,
            }

    if "max_stadium_hanging_gap_m" in config and stadium.get("lighting_mode") == "roof_ring":
        # Only roof-mounted lighting is held to this: a stadium lit from
        # corner towers has no canopy to hang from. The banks were positioned
        # independently of the roof, so moving the roof left them in mid-air.
        # One metre is the slack a mounting bracket could plausibly explain.
        wanted = float(config["max_stadium_hanging_gap_m"])
        gaps = scene.get("hanging_gap_m") or {}
        if not gaps:
            gates["stadium_hanging_gap_max"] = _gate_missing("roof/floodlight banks")
        else:
            worst = max(abs(gap) for gap in gaps.values())
            gates["stadium_hanging_gap_max"] = {
                "pass": worst <= wanted, "value": {"worst_m": worst, "by_part": gaps},
                "want": wanted,
            }

    if config.get("require_stadium_signage_upright"):
        # The club name was built with rot X = -90 deg, which stands the text
        # up with its glyphs inverted: from outside the bowl it reads mirrored.
        entries = scene.get("signage") or []
        if not entries:
            gates["stadium_signage_upright"] = _gate_missing("signage")
        else:
            bad = [item["name"] for item in entries
                   if not item["upright"] or item["mirrored"]]
            gates["stadium_signage_upright"] = {
                "pass": not bad, "value": bad or "all upright",
                "want": "no inverted or mirrored signage",
            }

    if "max_stadium_seat_color_share" in config:
        # seat_pattern "solid" left all 21342 seat modules one colour. Vertical
        # bands of club colour are what makes a bowl read as a club's ground;
        # a single material over 90% of the seating area is a monochrome bowl.
        wanted = float(config["max_stadium_seat_color_share"])
        if "seat_colour_share" not in scene:
            gates["stadium_seat_color_share_max"] = _gate_missing("seats")
        else:
            value = scene["seat_colour_share"]
            gates["stadium_seat_color_share_max"] = {
                "pass": value <= wanted,
                "value": {"dominant_share": value,
                          "materials": scene.get("seat_colour_materials", 1)},
                "want": wanted,
            }

    if "min_stadium_circularity" in config:
        # _continuous_oval_structure derives both semi-axes from the pitch
        # (outer_a = length/2 + runoff + depth, outer_b = width/2 + ...), so
        # the envelope is always an ellipse. On a round ground that is wrong by
        # construction. Measured on the Cilindro, EVERY part comes out at the
        # same 0.79: terraces 0.786, rear structure 0.790, roof 0.790, seats
        # 0.786 - and the ground it is modelling is a 180 m circle. The polish
        # pass never fixed it either, which is the point: nothing in the run
        # was ever asked the question. 0.93 is the tolerance of a real ring
        # built from straight precast bays; the ellipse misses it by a mile.
        wanted = float(config["min_stadium_circularity"])
        if "plan_circularity" not in scene:
            gates["stadium_circularity_min"] = _gate_missing("bowl plan")
        else:
            value = scene["plan_circularity"]
            gates["stadium_circularity_min"] = {
                "pass": value >= wanted,
                "value": {"ratio": value, "r_p5": scene.get("plan_radius_p5"),
                          "r_p95": scene.get("plan_radius_p95")},
                "want": wanted,
            }

    if "max_stadium_bowl_intrusion_m" in config:
        # A metre of tolerance: a volume that leans on the boundary from
        # outside measures a few centimetres through profile quantisation,
        # while the cases worth catching are metres deep (the Cilindro's stair
        # cores cut 4 m past the drum radius).
        wanted = float(config["max_stadium_bowl_intrusion_m"])
        reference = str(config.get("stadium_bowl_intrusion_reference", "floor"))
        intrusions = scene.get("bowl_intrusions_%s" % reference)
        if intrusions is None:
            gates["stadium_bowl_intrusion_max"] = _gate_missing("bowl %s" % reference)
        else:
            worst = max([item["depth_m"] for item in intrusions] or [0.0])
            gates["stadium_bowl_intrusion_max"] = {
                "pass": worst <= wanted,
                "value": {"reference": reference, "worst_m": worst,
                          "offenders": [item for item in intrusions
                                        if item["depth_m"] > wanted]},
                "want": wanted,
            }

    if config.get("require_stadium_camera_clearance"):
        # A camera inside the canopy slab renders pure black. The black-pixel
        # gate only notices after a render has been paid for; the geometry says
        # it up front.
        buried = scene.get("cameras_in_roof")
        if buried is None:
            gates["stadium_camera_clearance"] = _gate_missing("roof")
        else:
            gates["stadium_camera_clearance"] = {
                "pass": not buried, "value": buried or "clear",
                "want": "no camera inside the roof slab",
            }


def evaluate_report(report, config, file_metrics=None, material_names=(), scene=None):
    """Evaluate measured data without Blender, for tests and CI.

    ``scene`` carries the relational measurements from ``measure_stadium_scene``
    when the evaluator runs inside Blender; without it the stadium relational
    gates simply do not appear.
    """
    file_metrics = file_metrics or {}
    gates = {}

    if "min_objects" in config:
        value = int(report.get("objects", 0))
        wanted = int(config["min_objects"])
        gates["objects_min"] = {"pass": value >= wanted, "value": value, "want": wanted}

    if "min_individual_buildings" in config:
        value = int(report.get("buildings_individual", 0))
        wanted = int(config["min_individual_buildings"])
        gates["individual_buildings_min"] = {
            "pass": value >= wanted,
            "value": value,
            "want": wanted,
        }

    if "min_distinct_colors" in config:
        value = len({name for name in material_names if name.startswith("BLK_C_")})
        wanted = int(config["min_distinct_colors"])
        gates["distinct_colors_min"] = {
            "pass": value >= wanted,
            "value": value,
            "want": wanted,
        }

    appearance = report.get("building_appearance", {})
    for config_key, report_key, gate_name in (
            ("min_facade_shader_buildings", "facade_shader_buildings", "facade_shader_min"),
            ("min_facade_geometry_buildings", "facade_geometry_buildings", "facade_geometry_min"),
            ("min_facade_window_panels", "facade_window_panels", "facade_panels_min"),
            ("min_facade_window_frame_parts", "facade_window_frame_parts",
             "facade_window_frame_parts_min"),
            ("min_facade_window_mullions", "facade_window_mullions",
             "facade_window_mullions_min"),
            ("min_visible_interior_bays", "visible_interior_bays",
             "visible_interior_bays_min"),
            ("min_lit_interior_rooms", "lit_interior_rooms",
             "lit_interior_rooms_min"),
            ("min_symmetric_window_pairs", "symmetric_window_pairs",
             "symmetric_window_pairs_min"),
            ("min_balconies", "balconies", "balconies_min"),
            ("min_facade_accent_parts", "facade_accent_parts",
             "facade_accent_parts_min")):
        if config_key in config:
            value = int(appearance.get(report_key, 0))
            wanted = int(config[config_key])
            gates[gate_name] = {"pass": value >= wanted, "value": value, "want": wanted}
    for config_key, report_key, gate_name in (
            ("min_facade_profiles", "facade_profiles", "facade_profiles_min"),
            ("min_facade_variants", "facade_variants", "facade_variants_min")):
        if config_key in config:
            raw = appearance.get(report_key, {})
            value = len(raw)
            wanted = int(config[config_key])
            gates[gate_name] = {"pass": value >= wanted, "value": value, "want": wanted}

    construction = report.get("construction_detail", {})
    for config_key, report_key, gate_name in (
            ("min_building_parts", "building_parts", "building_parts_min"),
            ("min_construction_bands", "bands", "construction_bands_min"),
            ("min_entrances", "entrances", "entrances_min")):
        if config_key in config:
            value = int(construction.get(report_key, 0))
            wanted = int(config[config_key])
            gates[gate_name] = {"pass": value >= wanted, "value": value, "want": wanted}

    interiors = report.get("building_interiors", {})
    for config_key, report_key, gate_name in (
            ("min_interior_layout_buildings", "buildings", "interior_layout_buildings_min"),
            ("min_interior_floor_slabs", "floor_slabs", "interior_floor_slabs_min"),
            ("min_interior_corridor_segments", "corridor_segments", "interior_corridors_min"),
            ("min_interior_partitions", "partitions", "interior_partitions_min"),
            ("min_interior_cores", "cores", "interior_cores_min")):
        if config_key in config:
            value = int(interiors.get(report_key, 0))
            wanted = int(config[config_key])
            gates[gate_name] = {"pass": value >= wanted, "value": value, "want": wanted}

    covered = report.get("covered_structures", {})
    for config_key, report_key, gate_name in (
            ("min_covered_structures", "count", "covered_structures_min"),
            ("min_cover_columns", "columns", "cover_columns_min"),
            ("min_cover_edge_beams", "edge_beams", "cover_edge_beams_min")):
        if config_key in config:
            value = int(covered.get(report_key, 0))
            wanted = int(config[config_key])
            gates[gate_name] = {"pass": value >= wanted, "value": value, "want": wanted}

    urban = report.get("urban_objects", {})
    for config_key, report_key, gate_name in (
            ("min_urban_objects", "count", "urban_objects_min"),
            ("min_urban_object_parts", "parts", "urban_object_parts_min")):
        if config_key in config:
            value = int(urban.get(report_key, 0))
            wanted = int(config[config_key])
            gates[gate_name] = {"pass": value >= wanted, "value": value, "want": wanted}

    if "required_urban_object_kinds" in config:
        actual = set((urban.get("by_kind") or {}).keys())
        wanted = {str(value) for value in config["required_urban_object_kinds"]}
        gates["required_urban_object_kinds"] = {
            "pass": wanted <= actual, "value": sorted(actual), "want": sorted(wanted),
        }

    urban_detail = report.get("urban_detail", {})
    for config_key, report_key, gate_name in (
            ("min_urban_detail_objects", "count", "urban_detail_objects_min"),
            ("min_urban_detail_parts", "parts", "urban_detail_parts_min"),
            ("min_urban_text_objects", "text_objects", "urban_text_objects_min"),
            ("min_urban_explicit_directions", "explicit_directions",
             "urban_explicit_directions_min"),
            ("min_urban_explicit_dimensions", "explicit_dimensions",
             "urban_explicit_dimensions_min"),
            ("min_urban_wall_hosted", "wall_hosted", "urban_wall_hosted_min")):
        if config_key in config:
            value = int(urban_detail.get(report_key, 0))
            wanted = int(config[config_key])
            gates[gate_name] = {"pass": value >= wanted, "value": value, "want": wanted}

    if "min_urban_road_hosted" in config:
        value = int(urban_detail.get("road_hosted", 0))
        wanted = int(config["min_urban_road_hosted"])
        gates["urban_road_hosted_min"] = {
            "pass": value >= wanted, "value": value, "want": wanted,
        }

    if "required_urban_sign_shapes" in config:
        actual = set((urban_detail.get("by_sign_shape") or {}).keys())
        wanted = {str(value) for value in config["required_urban_sign_shapes"]}
        gates["required_urban_sign_shapes"] = {
            "pass": wanted <= actual, "value": sorted(actual), "want": sorted(wanted),
        }

    if "required_urban_families" in config:
        actual = set((urban_detail.get("by_family") or {}).keys())
        wanted = {str(value) for value in config["required_urban_families"]}
        gates["required_urban_families"] = {
            "pass": wanted <= actual, "value": sorted(actual), "want": sorted(wanted),
        }

    streetscape = report.get("streetscape", {})
    for config_key, report_key, gate_name in (
            ("min_streetscape_lane_arrows", "lane_arrows", "streetscape_lane_arrows_min"),
            ("min_streetscape_kerb_segments", "kerb_segments", "streetscape_kerbs_min"),
            ("min_streetscape_traffic_islands", "traffic_islands", "streetscape_islands_min"),
            ("min_streetscape_stop_lines", "stop_lines", "streetscape_stop_lines_min"),
            ("min_streetscape_lane_dividers", "lane_dividers", "streetscape_lane_dividers_min"),
            ("min_streetscape_cycle_lanes", "cycle_lanes", "streetscape_cycle_lanes_min"),
            ("min_streetscape_cycle_lane_separators", "cycle_lane_separators",
             "streetscape_cycle_lane_separators_min"),
            ("min_streetscape_cycle_protection_elements", "cycle_protection_elements",
             "streetscape_cycle_protection_elements_min"),
            ("min_streetscape_bus_lanes", "bus_lanes", "streetscape_bus_lanes_min"),
            ("min_streetscape_bus_lane_metadata_profiles", "bus_lane_metadata_profiles",
             "streetscape_bus_lane_metadata_profiles_min"),
            ("min_streetscape_parking_strips", "parking_strips",
             "streetscape_parking_strips_min"),
            ("min_streetscape_manhole_covers", "manhole_covers",
             "streetscape_manhole_covers_min"),
            ("min_streetscape_drainage_inlets", "drainage_inlets",
             "streetscape_drainage_inlets_min"),
            ("min_streetscape_sidewalk_strips", "sidewalk_strips",
             "streetscape_sidewalk_strips_min"),
            ("min_streetscape_sidewalk_metadata_profiles", "sidewalk_metadata_profiles",
             "streetscape_sidewalk_metadata_profiles_min"),
            ("min_streetscape_sidewalk_kerb_edges", "sidewalk_kerb_edges",
             "streetscape_sidewalk_kerb_edges_min"),
            ("min_streetscape_curb_ramps", "curb_ramps",
             "streetscape_curb_ramps_min"),
            ("min_streetscape_raised_crossings", "raised_crossings",
             "streetscape_raised_crossings_min"),
            ("min_streetscape_speed_tables", "speed_tables",
             "streetscape_speed_tables_min"),
            ("min_streetscape_tree_rows", "tree_rows", "streetscape_tree_rows_min"),
            ("min_streetscape_utility_lines", "utility_lines", "streetscape_utility_lines_min"),
            ("min_streetscape_utility_conductors", "utility_conductors",
             "streetscape_utility_conductors_min")):
        if config_key in config:
            value = int(streetscape.get(report_key, 0))
            wanted = int(config[config_key])
            gates[gate_name] = {"pass": value >= wanted, "value": value, "want": wanted}

    if "min_streetscape_cycle_lane_m" in config:
        value = float(streetscape.get("cycle_lane_m", 0.0))
        wanted = float(config["min_streetscape_cycle_lane_m"])
        gates["streetscape_cycle_lane_m_min"] = {
            "pass": value >= wanted, "value": value, "want": wanted,
        }
    for config_key, report_key, gate_name in (
            ("min_streetscape_bus_lane_m", "bus_lane_m", "streetscape_bus_lane_m_min"),
            ("min_streetscape_parking_strip_m", "parking_strip_m",
             "streetscape_parking_strip_m_min"),
            ("min_streetscape_sidewalk_m", "sidewalk_m",
             "streetscape_sidewalk_m_min")):
        if config_key in config:
            value = float(streetscape.get(report_key, 0.0))
            wanted = float(config[config_key])
            gates[gate_name] = {"pass": value >= wanted, "value": value, "want": wanted}
    for config_key, report_key, gate_name in (
            ("max_streetscape_curb_ramp_height", "curb_ramp_max_height",
             "streetscape_curb_ramp_height_max"),
            ("max_streetscape_raised_crossing_height", "raised_crossing_max_height",
             "streetscape_raised_crossing_height_max")):
        if config_key in config:
            value = float(streetscape.get(report_key, 0.0))
            wanted = float(config[config_key])
            gates[gate_name] = {"pass": value <= wanted, "value": value, "want": wanted}

    utility = report.get("utility_networks", {})
    for config_key, report_key, gate_name in (
            ("min_utility_substations", "substations", "utility_substations_min"),
            ("min_utility_transformers", "transformers", "utility_transformers_min"),
            ("min_utility_telecom_poles", "telecom_poles", "utility_telecom_poles_min"),
            ("min_utility_telecom_cabinets", "telecom_cabinets", "utility_telecom_cabinets_min"),
            ("min_utility_communication_lines", "communication_lines",
             "utility_communication_lines_min"),
            ("min_utility_communication_conductors", "communication_conductors",
             "utility_communication_conductors_min"),
            ("min_utility_metadata_only_lines", "metadata_only_lines",
             "utility_metadata_only_lines_min")):
        if config_key in config:
            value = int(utility.get(report_key, 0))
            wanted = int(config[config_key])
            gates[gate_name] = {"pass": value >= wanted, "value": value, "want": wanted}

    fluid = report.get("fluid_networks", {})
    for config_key, report_key, gate_name in (
            ("min_fluid_visible_lines", "visible_lines", "fluid_visible_lines_min"),
            ("min_fluid_metadata_only_lines", "metadata_only_lines",
             "fluid_metadata_only_lines_min"),
            ("min_fluid_supports", "supports", "fluid_supports_min"),
            ("min_fluid_pumping_stations", "pumping_stations",
             "fluid_pumping_stations_min"),
            ("min_fluid_valves", "valves", "fluid_valves_min"),
            ("min_fluid_measurement_points", "measurement_points",
             "fluid_measurement_points_min"),
            ("min_fluid_cabinets", "fluid_cabinets", "fluid_cabinets_min")):
        if config_key in config:
            value = int(fluid.get(report_key, 0))
            wanted = int(config[config_key])
            gates[gate_name] = {"pass": value >= wanted, "value": value, "want": wanted}
    if "min_fluid_visible_length_m" in config:
        value = float(fluid.get("visible_length_m", 0.0))
        wanted = float(config["min_fluid_visible_length_m"])
        gates["fluid_visible_length_m_min"] = {
            "pass": value >= wanted, "value": value, "want": wanted,
        }

    access = report.get("pedestrian_access", {})
    for config_key, report_key, gate_name in (
            ("min_pedestrian_steps", "steps", "pedestrian_steps_min"),
            ("min_pedestrian_ramps", "ramps", "pedestrian_ramps_min"),
            ("min_pedestrian_escalators", "escalators", "pedestrian_escalators_min"),
            ("min_pedestrian_moving_walkways", "moving_walkways", "pedestrian_moving_walkways_min"),
            ("min_pedestrian_elevators", "elevators", "pedestrian_elevators_min"),
            ("min_pedestrian_inclined_elevators", "inclined_elevators", "pedestrian_inclined_elevators_min"),
            ("min_pedestrian_handrails", "handrails", "pedestrian_handrails_min"),
            ("min_pedestrian_unspecified_handrails", "unspecified_handrails", "pedestrian_unspecified_handrails_min"),
            ("min_pedestrian_step_count", "step_count", "pedestrian_step_count_min")):
        if config_key in config:
            value = int(access.get(report_key, 0)); wanted = int(config[config_key])
            gates[gate_name] = {"pass": value >= wanted, "value": value, "want": wanted}
    for config_key, report_key, gate_name in (
            ("min_pedestrian_step_m", "step_m", "pedestrian_step_m_min"),
            ("min_pedestrian_ramp_m", "ramp_m", "pedestrian_ramp_m_min"),
            ("min_pedestrian_handrail_m", "handrail_m", "pedestrian_handrail_m_min")):
        if config_key in config:
            value = float(access.get(report_key, 0.0)); wanted = float(config[config_key])
            gates[gate_name] = {"pass": value >= wanted, "value": value, "want": wanted}
    if "max_pedestrian_rise_m" in config:
        value = float(access.get("max_rise_m", 0.0)); wanted = float(config["max_pedestrian_rise_m"])
        gates["pedestrian_rise_m_max"] = {"pass": value <= wanted, "value": value, "want": wanted}

    vegetation = report.get("vegetation_areas", {})
    for config_key, report_key, gate_name in (
            ("min_vegetation_area_zones", "zones", "vegetation_area_zones_min"),
            ("min_vegetation_area_instances", "instances", "vegetation_instances_min"),
            ("min_vegetation_area_parts", "parts", "vegetation_parts_min")):
        if config_key in config:
            value = int(vegetation.get(report_key, 0))
            wanted = int(config[config_key])
            gates[gate_name] = {"pass": value >= wanted, "value": value, "want": wanted}
    if "required_vegetation_area_kinds" in config:
        actual = set((vegetation.get("by_kind") or {}).keys())
        wanted = {str(value) for value in config["required_vegetation_area_kinds"]}
        gates["required_vegetation_area_kinds"] = {
            "pass": wanted <= actual, "value": sorted(actual), "want": sorted(wanted),
        }

    residential = report.get("residential", {})
    for config_key, report_key, gate_name, cast in (
            ("min_residential_zones", "zone_count", "residential_zones_min", int),
            ("min_residential_zone_area_m2", "zone_area_m2",
             "residential_zone_area_min", float),
            ("min_residential_buildings", "building_count",
             "residential_buildings_min", int),
            ("min_residential_boundary_segments", "mapped_boundary_segments",
             "residential_boundary_segments_min", int)):
        if config_key in config:
            value = cast(residential.get(report_key, 0))
            wanted = cast(config[config_key])
            gates[gate_name] = {"pass": value >= wanted, "value": value, "want": wanted}

    stadium = report.get("football_stadium", {})
    for config_key, report_key, gate_name in (
            ("min_stadium_stand_rows", "stand_rows", "stadium_stand_rows_min"),
            ("min_stadium_stand_sections", "stand_sections", "stadium_stand_sections_min"),
            ("min_stadium_seat_modules", "seat_modules", "stadium_seat_modules_min"),
            ("min_stadium_aisle_steps", "aisle_steps", "stadium_aisle_steps_min"),
            ("min_stadium_vomitories", "vomitories", "stadium_vomitories_min"),
            ("min_stadium_roof_panels", "roof_panels", "stadium_roof_panels_min"),
            ("min_stadium_roof_supports", "roof_supports", "stadium_roof_supports_min"),
            ("min_stadium_floodlight_towers", "floodlight_towers", "stadium_floodlights_min"),
            ("min_stadium_pitch_markings", "pitch_marking_segments", "stadium_pitch_markings_min"),
            ("min_stadium_goals", "goals", "stadium_goals_min")):
        if config_key in config:
            value = int(stadium.get(report_key, 0))
            wanted = int(config[config_key])
            gates[gate_name] = {"pass": value >= wanted, "value": value, "want": wanted}

    for config_key, report_key, gate_name in (
            ("max_stadium_seat_aisle_overlap_cells", "seat_aisle_overlap_cells",
             "stadium_seat_aisle_overlap_max"),
            ("max_stadium_seat_vomitory_overlap_cells", "seat_vomitory_overlap_cells",
             "stadium_seat_vomitory_overlap_max"),
            ("max_stadium_row_rise", "max_row_rise", "stadium_row_rise_max")):
        if config_key in config:
            value = float(stadium.get(report_key, float("inf")))
            wanted = float(config[config_key])
            gates[gate_name] = {"pass": value <= wanted, "value": value, "want": wanted}

    if "min_stadium_roof_support_seat_clearance" in config:
        value = float(stadium.get("roof_support_seat_clearance", 0.0))
        wanted = float(config["min_stadium_roof_support_seat_clearance"])
        gates["stadium_roof_support_seat_clearance_min"] = {
            "pass": value >= wanted, "value": value, "want": wanted,
        }

    if "required_stadium_sides" in config:
        actual = set(stadium.get("stand_sides") or [])
        wanted = {str(value).lower() for value in config["required_stadium_sides"]}
        gates["required_stadium_sides"] = {
            "pass": wanted <= actual, "value": sorted(actual), "want": sorted(wanted),
        }

    _stadium_relational_gates(report, config, scene, gates)

    hospital = report.get("hospital", {})
    for config_key, report_key, gate_name in (
            ("min_hospital_sites", "sites", "hospital_sites_min"),
            ("min_hospital_canopies", "entrance_canopies", "hospital_canopies_min"),
            ("min_hospital_emergency_bays", "emergency_bays", "hospital_emergency_bays_min"),
            ("min_hospital_medical_crosses", "medical_crosses", "hospital_medical_crosses_min"),
            ("min_hospital_roof_units", "roof_units", "hospital_roof_units_min"),
            ("min_hospital_helipads", "helipads", "hospital_helipads_min")):
        if config_key in config:
            value = int(hospital.get(report_key, 0))
            wanted = int(config[config_key])
            gates[gate_name] = {"pass": value >= wanted, "value": value, "want": wanted}

    highway = report.get("highway", {})
    for config_key, report_key, gate_name in (
            ("min_highway_carriageways", "carriageways", "highway_carriageways_min"),
            ("min_highway_lanes", "lanes", "highway_lanes_min"),
            ("min_highway_marking_dashes", "lane_marking_dashes", "highway_markings_min"),
            ("min_highway_edge_lines", "edge_lines", "highway_edge_lines_min"),
            ("min_highway_guardrails", "guardrail_segments", "highway_guardrails_min"),
            ("min_highway_guardrail_posts", "guardrail_posts", "highway_guardrail_posts_min"),
            ("min_highway_bridge_piers", "bridge_piers", "highway_bridge_piers_min"),
            ("min_highway_gantries", "gantries", "highway_gantries_min")):
        if config_key in config:
            value = int(highway.get(report_key, 0))
            wanted = int(config[config_key])
            gates[gate_name] = {"pass": value >= wanted, "value": value, "want": wanted}

    if "required_color_sources" in config:
        actual = set((appearance.get("color_sources") or {}).keys())
        wanted = {str(value) for value in config["required_color_sources"]}
        gates["required_color_sources"] = {
            "pass": wanted <= actual,
            "value": sorted(actual),
            "want": sorted(wanted),
        }

    for config_key, report_key, gate_name in (
            ("min_facade_color_confidence_mean", "facade_color_confidence_mean",
             "facade_color_confidence_mean"),
            ("min_roof_color_confidence_mean", "roof_color_confidence_mean",
             "roof_color_confidence_mean")):
        if config_key in config:
            value = float(appearance.get(report_key, 0.0))
            wanted = float(config[config_key])
            gates[gate_name] = {"pass": value >= wanted,
                                "value": round(value, 3), "want": wanted}

    expected = config.get("expected_feature_heights", {})
    if expected:
        actual = _reported_feature_heights(report)
        tolerance = float(config.get("feature_height_tolerance", 0.5))
        for selector, wanted in expected.items():
            value = actual.get(str(selector))
            gates[f"feature_height:{selector}"] = {
                "pass": value is not None and abs(value - float(wanted)) <= tolerance,
                "value": value if value is not None else "missing",
                "want": {"height": float(wanted), "tolerance": tolerance},
            }

    for filename in config.get("required_files", []):
        metric = file_metrics.get(filename, {})
        exists = bool(metric.get("exists"))
        gate = {"pass": exists, "value": "exists" if exists else "missing", "want": "exists"}
        if exists and filename.lower().endswith(".png") and "max_black_pct" in config:
            black = metric.get("black_pct")
            limit = float(config["max_black_pct"])
            gate = {
                "pass": black is not None and float(black) <= limit,
                "value": round(float(black), 3) if black is not None else "unmeasured",
                "want": f"<={limit}% black pixels",
            }
        gates[f"file:{filename}"] = gate

    result = {"complete": bool(gates) and all(gate["pass"] for gate in gates.values()),
              "gates": gates}
    if scene:
        # Kept in the report even when every gate passes: the numbers are what
        # a later run compares against when something drifts.
        result["stadium_measurements"] = scene
    return result


def run_checks(outdir, eval_path=None):
    import bpy

    eval_path = eval_path or os.path.join(outdir, "eval.json")
    config = load_gate_config(eval_path)
    report_path = os.path.join(outdir, "build_report.json")
    if os.path.exists(report_path):
        with open(report_path, encoding="utf-8") as stream:
            report = json.load(stream)
    else:
        report = {}

    metrics = {}
    for filename in config.get("required_files", []):
        path = os.path.join(outdir, filename)
        metrics[filename] = {"exists": os.path.exists(path)}
        if metrics[filename]["exists"] and filename.lower().endswith(".png"):
            metrics[filename]["black_pct"] = _png_black_pct(path)

    # Measuring the bowl walks every stadium mesh, so only do it when a
    # stadium was actually built and the run asked for at least one relation.
    scene = None
    wants_relations = any(key in config for key in STADIUM_RELATIONAL_DEFAULTS) \
        or "min_stadium_circularity" in config
    if wants_relations and (report.get("football_stadium") or {}).get("detected"):
        try:
            scene = measure_stadium_scene()
        except Exception as error:  # a broken measurement must not lose the run
            print("BLOCKS EVAL: stadium measurement failed:", error)
            scene = {}

    result = evaluate_report(
        report,
        config,
        file_metrics=metrics,
        material_names=(material.name for material in bpy.data.materials),
        scene=scene,
    )
    with open(os.path.join(outdir, "eval_report.json"), "w", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False)
    print("BLOCKS EVAL:", "COMPLETE" if result["complete"] else "INCOMPLETE",
          json.dumps(result, ensure_ascii=False))
    return result


def _cli():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if not argv:
        print("usage: blender -b <blend> -P blocks_eval.py -- <outdir> [--eval f]")
        return
    eval_path = argv[argv.index("--eval") + 1] if "--eval" in argv else None
    run_checks(argv[0], eval_path)


if __name__ == "__main__":
    _cli()
