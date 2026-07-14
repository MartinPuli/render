"""
Smoke test del loop vivo (blender-mcp) SIN bpy: mcp_loop solo genera strings de
codigo, asi que se puede validar en CI puro.

Correr:  python3 -m pytest tests/test_mcp_loop.py -q
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import mcp_loop as ml   # noqa: E402


def test_build_payload_is_code_str():
    code = ml.build_payload("output/x/scene.json")
    assert isinstance(code, str) and code.strip()
    assert "build_scene" in code


def test_build_payload_uses_safe_clear():
    # SAFE clear: el payload usa clear_scene() (no reinicia Blender).
    assert "clear_scene" in ml.build_payload("output/x/scene.json")


def test_loop_plan_list_of_dicts_with_code():
    plan = ml.loop_plan("output/x/scene.json")
    assert isinstance(plan, list) and len(plan) >= 3
    for step in plan:
        assert isinstance(step, dict)
        assert "code" in step and isinstance(step["code"], str) and step["code"].strip()


def test_render_payload_writes_still():
    code = ml.render_payload("output/x/render.png")
    assert isinstance(code, str) and "render.render" in code
    assert "output/x/render.png" in code
