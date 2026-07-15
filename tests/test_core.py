"""
Tests reproducibles (pytest) sin Overpass ni Blender: validan la logica pura de
geofencing de landmarks, clipping de geometria al radio y parseo de alturas.

Correr:  python3 -m pytest tests/ -q
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import citylandmarks as cl   # noqa: E402
import place_to_3d as p      # noqa: E402


def test_core_has_no_preinstalled_location_tuning():
    scripts = os.path.join(os.path.dirname(__file__), "..", "scripts")
    core = "\n".join(open(os.path.join(scripts, name), encoding="utf-8").read().lower()
                     for name in ("citylandmarks.py", "place_to_3d.py", "blender_build.py"))
    banned = ("puente de la mujer", "villa 31", "obelisco de buenos aires",
              "puerto madero", "ezeiza")
    assert not any(name in core for name in banned)


# --- F1a: el core no contiene ciudades; packs externos son opt-in ---
def test_core_landmark_registry_is_empty():
    assert cl.LANDMARKS == []
    assert cl.landmarks_for_center(0.0, 0.0) == []


def test_explicit_landmark_pack_can_be_geofenced():
    pack = [{"key": "sample", "lat": 10.0, "lon": 20.0,
             "radius_m": 100.0, "match_names": ["sample monument"]}]
    assert [x["key"] for x in cl.landmarks_for_center(10.0, 20.0, pack)] == ["sample"]
    assert cl.landmarks_for_center(11.0, 20.0, pack) == []


def test_name_match_gate():
    lm = {"match_names": ["sample monument"]}
    assert cl.name_matches(lm, "Sample Monument")
    assert not cl.name_matches(lm, "Unrelated Feature")


# --- Clipping: la geometria queda dentro del radio pedido ---
def test_clip_polygon_within_radius():
    H = 100.0
    clipped = p.clip_polygon([(-500, -500), (500, -500), (500, 500), (-500, 500)], H)
    assert clipped
    for x, y in clipped:
        assert -H - 1e-6 <= x <= H + 1e-6
        assert -H - 1e-6 <= y <= H + 1e-6


def test_clip_segment_outside_none():
    assert p.clip_segment((200, 200), (300, 300), 100.0) is None


def test_clip_segment_inside_kept():
    assert p.clip_segment((-10, 0), (10, 0), 100.0) is not None


# --- Alturas (escala real, 1u = 1m) ---
def test_parse_height_levels():
    h, _ = p.parse_height({"building:levels": "4"})
    assert abs(h - 4 * p.LEVEL_HEIGHT) < 1e-6


def test_parse_height_explicit():
    h, _ = p.parse_height({"height": "25 m"})
    assert abs(h - 25.0) < 1e-6


# --- F1b: camara nunca dentro de un edificio ---
def test_camera_moves_out_of_building():
    import citycamera as cam
    scene = {
        "buildings": [{"footprint": [(-10, -10), (10, -10), (10, 10), (-10, 10)]}],
        "roads": [{"path": [[0, -40], [0, -15]], "z": 0.06}],
    }
    (x, y), moved = cam.safe_street_point(scene, 0.0, 0.0)  # origen dentro del edificio
    assert moved
    assert not cam.inside_any_building(scene["buildings"], x, y)


def test_camera_stays_if_already_safe():
    import citycamera as cam
    scene = {"buildings": [{"footprint": [(-10, -10), (10, -10), (10, 10), (-10, 10)]}],
             "roads": []}
    (x, y), moved = cam.safe_street_point(scene, 50.0, 50.0)  # afuera
    assert not moved and (x, y) == (50.0, 50.0)


# --- F2a: procedencia + confianza por edificio ---
def test_height_source_classification():
    assert p.height_source({"height": "30 m"}) == "explicit"
    assert p.height_source({"building:levels": "5"}) == "levels"
    assert p.height_source({"building": "yes"}) == "default"


def test_confidence_ordering():
    c_expl = p.building_confidence({"height": "30 m"})
    c_lvl = p.building_confidence({"building:levels": "5"})
    c_def = p.building_confidence({"building": "yes"})
    assert c_expl > c_lvl > c_def          # mas dato OSM => mas confianza
    assert 0.0 <= c_def <= c_expl <= 1.0
    # el nombre aporta un plus de confianza
    assert p.building_confidence({"height": "30 m", "name": "Torre X"}) > c_expl


# --- F2c: variedad de techos por edificio (roof:shape + fallback) ---
def test_roof_tag_respected():
    import cityroofs as cr
    assert cr.choose_roof_kind("gabled", 8) == "gabled"
    assert cr.choose_roof_kind("onion", 40) == "dome"      # onion -> dome
    assert cr.choose_roof_kind("flat", 40) == "flat"
    assert cr.choose_roof_kind("skillion", 6) == "skillion"
    assert cr.choose_roof_kind("mansard", 9) == "hipped"


def test_roof_default_variety():
    import cityroofs as cr
    # bajos sin tag: aguas variadas (al menos 2 tipos distintos entre semillas)
    low = {cr.choose_roof_kind(None, 8, seed=s * 3.1) for s in range(80)}
    assert low <= set(cr.ROOF_KINDS)
    assert len(low & {"hipped", "gabled", "pyramidal", "skillion"}) >= 2
    # altos sin tag: azotea (parapeto o lisa), nunca aguas
    tall = {cr.choose_roof_kind(None, 45, seed=s * 2.3) for s in range(80)}
    assert tall <= {"parapet", "flat"}


# --- F3a: perfiles arquitectonicos ---
def test_profile_classification_synthetic():
    import cityprofiles as cp
    towers = [{"height": 90, "roof_shape": None, "type": "yes"} for _ in range(20)]
    assert cp.classify_profile(towers) == "modern_towers"
    historic = [{"height": 7, "roof_shape": "gabled", "type": "house"} for _ in range(20)]
    assert cp.classify_profile(historic) == "historic_center"
    informal = [{"height": 4, "roof_shape": None, "type": "yes"} for _ in range(20)]
    assert cp.classify_profile(informal) == "informal_dense"
    assert cp.classify_profile([]) == "mixed"
    d = cp.profile_defaults("modern_towers")
    assert d["roof_bias"] == "flat" and d["default_height"] > 15


def test_roof_bias_applies_without_tag():
    import cityroofs as cr
    # perfil historico -> sesgo gabled en edificios bajos sin tag
    kinds = {cr.choose_roof_kind(None, 8, seed=s * 1.7, bias="gabled") for s in range(40)}
    assert "gabled" in kinds
    # el tag OSM siempre gana sobre el sesgo
    assert cr.choose_roof_kind("hipped", 8, bias="gabled") == "hipped"


# --- Capas especiales: aeropuertos no se degradan a calles genericas ---
def test_overpass_query_requests_airport_layers():
    query = p.build_overpass_query(-1, -1, 1, 1)
    assert '"aeroway"' in query
    assert "runway" in query and "taxiway" in query and "apron" in query


def test_parse_airport_special_features_and_widths():
    project = p.make_projector(0.0, 0.0)
    data = {"elements": [
        {"type": "way", "id": 1,
         "tags": {"aeroway": "runway", "width": "150 ft", "ref": "11/29"},
         "geometry": [{"lat": 0.0, "lon": 0.0},
                      {"lat": 0.001, "lon": 0.0}]},
        {"type": "way", "id": 2, "tags": {"aeroway": "apron"},
         "geometry": [{"lat": 0.0, "lon": 0.0},
                      {"lat": 0.0, "lon": 0.001},
                      {"lat": 0.001, "lon": 0.001},
                      {"lat": 0.0, "lon": 0.0}]},
    ]}
    features = p.parse_special_features(data, project)
    runway = next(f for f in features if f["kind"] == "runway")
    apron = next(f for f in features if f["kind"] == "apron")
    assert runway["geometry"] == "line"
    assert abs(runway["width"] - 45.72) < 0.02
    assert apron["geometry"] == "surface"
    assert p.classify_scene_kind(data, "anything") == "airport"


def test_clip_special_features_stays_inside_radius():
    features = [{"geometry": "line", "kind": "runway", "width": 45,
                 "path": [[-500, 0], [500, 0]]},
                {"geometry": "surface", "kind": "apron",
                 "polygon": [[-300, -300], [300, -300], [300, 300], [-300, 300]]}]
    clipped = p.clip_special_features(features, 100)
    assert clipped
    for feature in clipped:
        points = feature.get("path") or feature.get("polygon")
        assert all(-100.0001 <= x <= 100.0001 and -100.0001 <= y <= 100.0001
                   for x, y in points)


def test_duplicate_apron_way_and_relation_is_removed():
    ring = [{"lat": 0.0, "lon": 0.0}, {"lat": 0.0, "lon": 0.001},
            {"lat": 0.001, "lon": 0.001}, {"lat": 0.0, "lon": 0.0}]
    data = {"elements": [
        {"type": "way", "id": 10, "tags": {"aeroway": "apron"},
         "geometry": ring},
        {"type": "relation", "id": 20,
         "tags": {"aeroway": "apron", "type": "multipolygon"},
         "members": [{"role": "outer", "geometry": ring}]},
    ]}
    features = p.parse_special_features(data, p.make_projector(0.0, 0.0))
    assert len([f for f in features if f["kind"] == "apron"]) == 1


def test_generic_osm_obelisk_becomes_landmark_without_place_name():
    data = {"elements": [{
        "type": "node", "id": 77, "lat": 10.0, "lon": 20.0,
        "tags": {"memorial": "obelisk", "height": "42 m", "name": "Any Name"},
    }]}
    features = p.parse_special_features(data, p.make_projector(10.0, 20.0))
    landmark = next(f for f in features if f.get("family") == "landmark")
    assert landmark["kind"] == "obelisk"
    assert landmark["height"] == 42.0
    assert landmark["height_source"] == "explicit"
    assert landmark["point"] == [0.0, 0.0]


def test_generic_landmark_query_is_tag_driven():
    query = p.build_overpass_query(-1, -1, 1, 1)
    assert '"memorial"="obelisk"' in query
    assert '"man_made"' in query
    assert "Buenos Aires" not in query and "Ezeiza" not in query


def test_scene_profile_cannot_be_triggered_by_place_name():
    empty_osm = {"elements": []}
    assert p.classify_scene_kind(empty_osm, "International Airport") == "urban"
    tagged = {"elements": [{"tags": {"aeroway": "runway"}}]}
    assert p.classify_scene_kind(tagged, "Unrelated Label") == "airport"
