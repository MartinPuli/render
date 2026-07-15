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

`one_shot_payload(...)` creates the reproducible baseline. The primary engine is
`scripts/loop_engineering.py`: score the baseline, rank one failure family,
checkpoint a controlled change, measure the delta, then accept or restore the
best checkpoint. “One shot” means no user micromanagement, not one internal call.

## The loop at a glance

```
get_scene_info            inspect / backup (what's there before we touch it)
      |
      v
SAFE clear                bb.clear_scene()   <-- NEVER read_factory_settings
      |
      v
bb.build_scene(scene)     buildings + roads + areas + special infrastructure
      |
      v
setup_world / sun / camera
      |
      v
get_viewport_screenshot   (or a fast render_payload preview)
      |
      v
score vs reference        6 weighted dimensions + critical defects
      |
      v
change one family         hypothesis + expected metric
      |
      v
checkpoint + delta        loop_NN.png + loop_NN.blend + loop_state.json
      |
      +--------------------------------------------------+
      | accept gates / continue <= 6 / restore best      |
      +--------------------------------------------------+
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

For the autonomous path:

```python
from mcp_loop import one_shot_payload
code = one_shot_payload(
    "output/ezeiza/scene.json", "output/ezeiza", slug="ezeiza_mcp",
    scripts_dir="scripts", textures_dir="textures", hdri_path="textures/sky.hdr",
)
# execute_blender_code(code=code)
```

Require `ONE_SHOT_OK` and treat it as `BASELINE_READY`, then open both PNGs.
`render_payload` automatically falls
back across `BLENDER_EEVEE_NEXT`, `BLENDER_EEVEE` and `CYCLES` and also handles
look-name differences between Blender releases.

Initialize `loop_engineering.new_state(...)`, record the six scores and defects,
then use `mcp_loop.iteration_payload(...)` for each controlled correction.
Persist every result with `loop_engineering.save_state(...)`. Accept at weighted
score >=85, every dimension >=70, and zero critical defects. Stop after six
iterations or two deltas below one point and restore `best_iteration`.

For manual/granular iteration:

`mcp_loop.loop_plan(scene_path, reference=...)` returns a list of steps, each a dict
`{"step", "purpose", "code"}`. Send each `code` through `execute_blender_code` in
order; loop over the `screenshot -> evaluate -> correct` steps only while the
score improves (maximum six controlled iterations) before the
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
| `one_shot_payload(...)` | baseline | backup + build + two renders + save + report + validation |
| `iteration_payload(...)` | checkpoint | freezes comparable PNG + BLEND for one scored iteration |
| `restore_checkpoint_payload(...)` | rollback | opens the best scored checkpoint |
| `validate_payload(...)` | acceptance | fails loudly for missing camera/content/files |

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
