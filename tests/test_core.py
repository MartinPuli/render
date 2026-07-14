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


# --- F1a: landmarks SOLO por geofence (no reglas globales) ---
def test_puente_in_puerto_madero():
    keys = [l["key"] for l in cl.landmarks_for_center(-34.6084, -58.3638)]
    assert "puente_de_la_mujer" in keys


def test_villa31_no_false_puente():
    # Villa 31 / Retiro (~3 km del puente) NO debe generar el Puente de la Mujer.
    keys = [l["key"] for l in cl.landmarks_for_center(-34.5830, -58.3800)]
    assert "puente_de_la_mujer" not in keys


def test_far_place_no_landmarks():
    assert cl.landmarks_for_center(48.8584, 2.2945) == []   # Paris


def test_name_match_gate():
    lm = cl.LANDMARKS[0]
    assert cl.name_matches(lm, "Puente de la Mujer")
    assert not cl.name_matches(lm, "Pasarela X")


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
