# PLAN — maps-to-3d como herramienta open-source robusta

Diferencial a preservar: el **loop con Blender MCP**
`construir → capturar → comparar con referencias → detectar errores → corregir → repetir`.

## 1) Auditoría de la arquitectura actual (problemas)

Hoy todo vive mezclado en `scripts/`, sin capas claras:

| Problema | Dónde | Impacto |
|---|---|---|
| **Landmarks por regla global** | `blender_build.add_puente_mujer` se llama SIEMPRE en `build_scene`; detecta "la pasarela peatonal más larga sobre agua" | En **Villa 31** cualquier pasarela elevada genera un "Puente de la Mujer" falso (criterio de aceptación) |
| **Sin identidad por edificio** | `place_to_3d` guarda solo footprint/height/color/type; `build_scene` agrupa por color en pocos objetos | No se puede seleccionar/editar una casa; sin ID OSM / procedencia / confidence |
| **Cámara puede caer dentro de un edificio** | `render_view`/`world_scene` ubican la cámara por fórmula, sin chequear colisión | Vista de calle atraviesa muros (criterio) |
| **Inferencia arquitectónica pobre** | techos casi siempre a 4 aguas; ventanas procedurales a escala fija; sin azoteas/chapa/ampliaciones | Silueta falsa, sobre todo en asentamientos (informal_dense) |
| **Fuentes/geometría/render acoplados** | descarga OSM + materiales + cámaras en los mismos módulos | difícil testear, extender o cambiar de motor |
| **Sin modelo intermedio** | no hay una `CityScene` normalizada; `scene.json` es ad-hoc | no hay dónde colgar procedencia/confidence ni separar fuentes de render |
| **Empaquetado** | sin `pyproject.toml`, sin CLI unificada, sin config, logging ad-hoc | no instalable/parametrizable como herramienta |
| **Sin tests** | 0 tests; depende de Overpass en vivo | no reproducible, sin regresión |
| **Google 3D Tiles guardado a disco/.blend** | `fetch_3dtiles`/`import_3dtiles` | choca con ToS (render-only) — separar del pipeline OSS (ver `README.md`, `EVALUATION.md`) |

## 2) Arquitectura objetivo (capas)

```
Fuentes            OSM (Overpass) · Elevacion · Street/KartaView/Mapillary · (Google 3D Tiles: modo aparte)
   |
Modelo intermedio  CityScene  { buildings[], roads[], areas[], landmarks[], meta{crs, center, sources} }
                   cada entidad: id_osm, tags, height + height_source, provenance, confidence
   |
Generadores 3D     profiles (informal_dense/historic_center/industrial/modern_towers) -> geometria+materiales
                   motor: (a) Python actual  (b) adaptador OSM2World (GLB/OBJ)  [seleccionable]
   |
Backend            Blender headless (world_scene) - Blender MCP vivo (live_build)
   |
Evaluador + loop   render -> referencia -> panel adversarial (EVALUATION.md) -> fix -> repetir
```

Los **landmarks, materiales y cámaras** no se mezclan con la descarga de datos.

## 3) Plan incremental (fases, por prioridad de criterio de aceptación)

**Fase 0 — sin romper nada (compat).** Mantener los flujos actuales; agregar lo nuevo detrás de flags/registries.

**Fase 1 — criterios de aceptación duros (esta entrega):**
- [F1a] **Landmarks geofenced / por nombre-ID OSM.** Registry de landmarks con
  `{name, lat, lon, radius, match_tags}`; solo se generan si el centro de la escena cae en
  su geofence Y (opcional) matchea nombre/ID OSM. -> *Villa 31 sin Puente de la Mujer falso.*
- [F1b] **Camara sin colisiones.** Chequear (x,y) de la camara contra footprints
  (point-in-poly); si esta dentro, moverla a la calle real mas cercana y validar distancia a
  fachadas. -> *La calle no atraviesa edificios.*
- [F1c] **Tests reproducibles con fixtures** (pytest, sin Overpass): clipping, alturas,
  geofence de landmarks, colision de camara, escala 1u=1m.

**Fase 2 — identidad + fidelidad:**
- [F2a] **Procedencia por entidad** en CityScene: `id_osm`, `tags`, `height_source`
  (tag/levels/estimado), `confidence`, `source`. Persistir como custom props por objeto.
- [F2b] **Cascada de alturas** (height -> levels x 3.0/3.5 -> estimado por tipo + jitter) y
  **materiales por tag OSM** (`building:material/colour`, `roof:material/colour`).
- [F2c] **Techos reales** por `roof:shape` (flat/gabled/hipped/skillion...).

**Fase 3 — perfiles + motor:**
- [F3a] **Perfiles urbanos** configurables (informal_dense -> techos planos/chapa,
  ampliaciones, variedad controlada, sin ventanas gigantes; historic_center/industrial/modern_towers).
- [F3b] **Adaptador OSM2World** (prototipo): correr OSM2World -> GLB e importarlo, como motor
  alternativo, sin borrar el generador Python.

**Fase 4 — empaquetado + produccion:**
- `pyproject.toml`, CLI (`maps3d ...`), config YAML/JSON, logging estructurado, cache OSM
  controlada, reporte de fuentes/licencias/estimados. Google 3D Tiles como **modo aparte**
  (render-only + atribucion, ver `README.md`).

**Fase 5 — loop MCP como experiencia principal:** verificar `get_scene_info` -> backup ->
`live_build` (clear seguro, nunca `read_factory_settings`) -> capturar viewport -> evaluar ->
corregir -> repetir 3-6 veces -> guardar `.blend`/renders/reporte.

## Criterios de aceptacion (tracking)
- [ ] Villa 31 sin landmarks falsos -> **F1a**
- [ ] Camara de calle no atraviesa edificios -> **F1b**
- [ ] Techos con variedad coherente por edificio -> **F2c/F3a**
- [ ] Cada objeto con procedencia + confidence -> **F2a**
- [ ] Pipeline funciona sin Google API -> ya (modo OSM)
- [ ] Tests reproducibles + documentacion -> **F1c** + docs
- [ ] Flujos existentes siguen funcionando -> **F0**
