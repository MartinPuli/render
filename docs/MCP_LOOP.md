# The Blender-MCP live loop (primary experience)

The **primary** way to build and refine a scene is the live Blender-MCP loop: an
actual Blender is running with the `blender-mcp` addon, and we drive it by sending
Python through the `execute_blender_code` tool. You see every change instantly in
the viewport and iterate against a reference photo (Google Street View or an aerial
shot) until it matches.

`scripts/mcp_loop.py` is a **pure** module (no `bpy` at import time) whose helpers
*return the code strings* to send through `execute_blender_code`. That keeps the
loop testable in plain CI (`tests/test_mcp_loop.py`) while the strings run inside
the live Blender.

## The loop at a glance

```
get_scene_info            inspect / backup (what's there before we touch it)
      |
      v
SAFE clear                bb.clear_scene()   <-- NEVER read_factory_settings
      |
      v
bb.build_scene(scene)     buildings + roads + areas + landmarks
      |
      v
setup_world / sun / camera
      |
      v
get_viewport_screenshot   (or a fast render_payload preview)
      |
      v
evaluate vs reference     materials / sun / exposure / camera off?
      |
      v
correct                   tweak exposure, sun energy, materials, camera
      |
      +---------------------------------------------+
      | repeat 3-6x until it matches the reference  |
      +---------------------------------------------+
      |
      v
save .blend  +  render final PNG  +  short report
```

## Why the SAFE clear matters (do NOT skip this)

**Never call `bpy.ops.wm.read_factory_settings()` in the live Blender.** Under
`blender-mcp` a factory reset reloads Blender, which **unregisters the addon and
kills the MCP server** — the loop dies mid-iteration and you lose the session.

Use the safe clear instead:

```python
bb.clear_scene()   # removes objects + orphan datablocks, leaves the addon/server intact
```

`clear_scene()` (in `scripts/blender_build.py`) removes all objects and
collections and then purges orphan meshes/materials/lights/cameras/images/worlds/
curves/node-groups — without ever resetting Blender. The headless renderer
(`render_view.py`) *can* use `read_factory_settings(use_empty=True)` because it
runs in its own throwaway `blender -b` process; the **live loop cannot**.

## The exact `execute_blender_code` skeleton

This is what the *build* step sends. `mcp_loop.build_payload(scene_path)` returns
exactly this (path filled in), so you normally don't hand-write it:

```python
import sys, json, importlib
sys.path.insert(0, r"/Users/.../render/scripts")   # so `import blender_build` works
import blender_build as bb
importlib.reload(bb)                                # pick up edits without restarting Blender

with open(r"/Users/.../render/output/<slug>/scene.json") as _f:
    scene = json.load(_f)

# SAFE clear: NEVER bpy.ops.wm.read_factory_settings (it unregisters the MCP addon).
bb.clear_scene()

cx, cy, R = bb.build_scene(scene)
bb.setup_world(sky=False)   # sky=True for street-level (physical Nishita sky)
bb.setup_sun()
bb.setup_camera(cx, cy, R)  # or bb.setup_streetview_camera(heading, fov, h, x, y)
print('[mcp_loop] built', len(scene.get('buildings', [])), 'buildings R=%.1fm' % R)
```

The `importlib.reload(bb)` is the point of the loop: edit `blender_build.py` on
disk, resend the build payload, and the live Blender rebuilds with your change — no
restart, no lost MCP server.

## Driving it from `mcp_loop`

`mcp_loop.loop_plan(scene_path, reference=...)` returns a list of steps, each a dict
`{"step", "purpose", "code"}`. Send each `code` through `execute_blender_code` in
order; loop over the `screenshot -> evaluate -> correct` steps 3-6 times before the
final `save+render`:

```python
import mcp_loop
plan = mcp_loop.loop_plan("output/puerto-madero/scene.json",
                          reference="docs/example-puerto-madero.png")
for step in plan:
    print(step["step"], "-", step["purpose"])
    # execute_blender_code(code=step["code"])   # <-- via the blender-mcp tool
```

### The step helpers (all return code strings, no side effects)

| Helper | Loop step | What the returned code does |
| --- | --- | --- |
| `inspect_payload()` | inspect / backup | prints object/mesh/material counts of the *current* scene |
| `build_payload(scene_path, heading=None, ...)` | SAFE clear + build | the skeleton above; `heading` set -> street-level camera |
| `render_payload(out_png, engine=..., samples=...)` | screenshot / render | sets engine + samples + resolution, `bpy.ops.render.render(write_still=True)` |
| `evaluate_payload()` | evaluate | prints engine / view_transform / exposure to compare vs the reference |
| `correct_payload(sun_energy=..., exposure=...)` | correct | nudges exposure and/or the sun's energy |
| `save_payload(blend_path)` | save | `bpy.ops.wm.save_as_mainfile(...)` |

For a quick per-iteration preview, `get_viewport_screenshot` (the blender-mcp tool)
is fastest; use `render_payload(...)` when you want a real EEVEE/Cycles frame to
judge materials and lighting.

## What to correct each iteration

Evaluate the screenshot against the reference and adjust, roughly in this order:

1. **Camera** — heading/FOV/height (street-level) or the 3/4 aerial framing.
2. **Exposure / tonemapping** — physical sky is very bright; street-level uses AgX
   with a strongly negative exposure (see `apply_street_view_settings`).
3. **Sun** — energy and angle for the time of day.
4. **Materials** — glass tint on towers, asphalt/concrete/roof colors, water.

Re-send only the payload you changed (e.g. `correct_payload(exposure=-2.6)`), then
re-screenshot. Repeat until it matches.

## Finish

Once it matches: `save_payload(...)` to write the `.blend`, `render_payload(...)`
for the final PNG, and jot a short report (center lat/lon, radius, building count,
which corrections you applied). The saved `.blend` plus the reference photo make the
next session's loop reproducible.
