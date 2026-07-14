---
name: maps-to-3d
description: >
  Convierte un lugar real (link de Google Maps, coordenadas o nombre) en una
  escena 3D EXACTA y foto-real en Blender. Modo principal: importa la malla 3D
  real de Google Photorealistic 3D Tiles (edificios, puentes, terreno con
  fotogrametria, texturizados) — geometria exacta que funciona en cualquier
  ciudad del mundo. Modo alternativo: reconstruye por volumenes desde
  OpenStreetMap con materiales procedurales/PBR (vidrio, mamposteria, asfalto,
  agua, vegetacion), organizado en colecciones, con camaras, luz HDRI, exports y
  reporte. Usar cuando el usuario pasa un lugar/direccion/link de Maps y quiere
  verlo, disenarlo o reconstruirlo en 3D/Blender como render, maqueta o modelo de
  una zona o ciudad. Triggers: "google maps", "street view", "en 3d", "blender",
  "maqueta 3d", "render de este lugar", "reconstrui esta zona", "modelo de la
  ciudad", "lugar a 3d", "world to blender", "3d tiles", "google earth 3d".
---

# maps-to-3d — De un lugar real a una escena 3D EXACTA en Blender

Le pasas un lugar y arma la escena en Blender. Hay **dos modos de geometria**:

- **EXACTO (Google 3D Tiles) — recomendado.** Baja la malla 3D REAL de Google
  (la misma de Google Earth: edificios, puentes, terreno con fotogrametria y
  textura), la transforma a metros locales centrada en el origen, y la deja lista
  para render. Es lo mas parecido a "de sitio a render exacto" y anda en cualquier
  ciudad del mundo que tenga cobertura 3D de Google. Necesita `GOOGLE_MAPS_API_KEY`
  con la **Map Tiles API** habilitada.
- **ESTILIZADO (OpenStreetMap).** Reconstruye por volumenes (extrusion de plantas
  con alturas) + materiales procedurales/PBR, organizado en colecciones. No es
  exacto en fachadas, pero es liviano, editable y no necesita la Map Tiles API.

## FUENTES DE DATOS (de donde sale CADA cosa)

| Dato | Fuente | API / archivo | Exactitud |
|---|---|---|---|
| **Malla 3D real** (edificios, puentes, terreno, texturas) | **Google Photorealistic 3D Tiles** | Map Tiles API (`tile.googleapis.com/v1/3dtiles`) | **EXACTA** (fotogrametria de Google) |
| Volumenes de edificios + alturas | OpenStreetMap (Overpass) | `overpass-api.de` etc. | plantas reales; alturas reales si OSM las tiene, si no estimadas |
| Calles, avenidas, vias, agua, parques, puentes | OpenStreetMap (Overpass) | idem | trazado real |
| Elevacion del terreno | **Google Elevation API** | `maps.googleapis.com/maps/api/elevation` | real |
| Imagen satelital / aerea (textura de terreno) | **Google Static Maps (satellite)** | `maps.googleapis.com/maps/api/staticmap` | real |
| Fotos de referencia a nivel de calle | **Google Street View** | `maps.googleapis.com/maps/api/streetview` | real (para comparar) |
| Texturas PBR (asfalto/hormigon/corteza) + HDRI de cielo | **PolyHaven (CC0)** | `api.polyhaven.com` | genericas, uso libre |
| Colores/materiales de edificios (modo OSM) | inferidos por tipo/altura | — | **estimados**, no verificados |

**Verificar acceso de la key** (una vez): las 3 APIs de Google deben responder:
```bash
K=$GOOGLE_MAPS_API_KEY
curl -s "https://tile.googleapis.com/v1/3dtiles/root.json?key=$K" | head -c 80   # 3D Tiles
curl -s -o /dev/null -w "%{http_code}\n" "https://maps.googleapis.com/maps/api/staticmap?center=0,0&zoom=1&size=1x1&maptype=satellite&key=$K"
curl -s "https://maps.googleapis.com/maps/api/elevation/json?locations=0,0&key=$K" | head -c 40
```
Si 3D Tiles da 403, habilitar **Map Tiles API** en la consola de Google Cloud.
**Atribucion obligatoria** de Google en 3D Tiles/satelite y de OpenStreetMap (ODbL).

## Modo EXACTO — Google 3D Tiles (pasos)

```bash
export GOOGLE_MAPS_API_KEY="..."
# 1) resolver el lugar a lat,lng (o pasarlas directo)
python3 scripts/place_to_3d.py "<LUGAR>" --radius 380 --no-render --out output/<slug>   # opcional, para lat/lng + Street View
# 2) bajar la malla 3D real (traversa el tileset, ECEF->metros locales)
GOOGLE_MAPS_API_KEY=$GOOGLE_MAPS_API_KEY python3 scripts/fetch_3dtiles.py <LAT> <LON> 380 output/<slug>/tiles3d --detail 12 --max-tiles 260
# 3) importar a Blender, texturizar, camara+luz HDRI, render + .blend
export MAPS3D_HDRI="$(pwd)/textures/sky.hdr"     # opcional (cielo con nubes)
blender -b -P scripts/import_3dtiles.py -- output/<slug>/tiles3d output/<slug>/tiles3d --samples 48 --res 1400 880
```
`--detail` menor = mas fino (mas tiles, mas detalle); `--max-tiles` limita la descarga.
Salida: `tiles3d_aerial.png`, `tiles3d_oblique.png`, `google_3dtiles.blend`.
La geometria queda centrada en el origen, en metros (1 u = 1 m), Este=X Norte=Y Arriba=Z.

## Modo ESTILIZADO — OpenStreetMap (pasos)

```bash
python3 scripts/place_to_3d.py "<LUGAR>" --radius 350 --no-render --out output/<slug>
python3 scripts/get_textures.py textures   # opcional: PBR + HDRI (mas realista)
export MAPS3D_TEXTURES="$(pwd)/textures"; export MAPS3D_HDRI="$(pwd)/textures/sky.hdr"
blender -b -P scripts/world_scene.py -- output/<slug>/scene.json output/<slug> <slug> --engine CYCLES --samples 48 --export glb
```
Arma la escena completa en colecciones `00_REFERENCE..13_EXPORT`, con camaras
(aerea/oblicua/calle), luz, `.blend` self-contained, GLB, previews y `report.json`.

## Scripts
- `place_to_3d.py` — resuelve lugar→lat,lng, baja OSM + Street View, escribe `scene.json`.
- `fetch_3dtiles.py` — baja Google Photorealistic 3D Tiles de la zona → GLBs + `manifest.json` (transforms ECEF).
- `import_3dtiles.py` — importa los GLBs a Blender (ECEF→ENU local), texturas, camaras, luz, render, `.blend`.
- `blender_build.py` — geometria + materiales procedurales/PBR del modo OSM (14 colecciones). `clear_scene()` limpia SEGURO para el MCP (nunca `read_factory_settings`).
- `world_scene.py` — orquesta el modo OSM headless (colecciones + metadata + camaras + export + reporte).
- `render_view.py` — render de UNA vista (aerea o calle) del modo OSM, para comparar con Street View.
- `get_textures.py` — baja texturas PBR CC0 + HDRI de PolyHaven.
- `live_build.py` — construye el modo OSM en un Blender vivo via blender-mcp (clear seguro).

## Loop de evaluacion adversarial (¿pasa por real?) — el corazon de la skill
No alcanza con renderizar una vez: hay que **iterar comparando contra la realidad**.
El ciclo (lo ejecuta Claude usando la skill):

1. **Render** de una vista (`render_view.py` para calle/aerea del modo OSM, o
   `import_3dtiles.py` para el modo exacto).
2. **Conseguir la referencia REAL de esa misma vista** para comparar apples-to-apples:
   - Calle: `streetview/heading_XXX.jpg` que baja `place_to_3d.py` (mismo punto y rumbo).
   - Aerea: imagen satelital de Google Static Maps del area (misma zona).
3. **Evaluar con un panel adversarial** (subagentes, uno por lente: materiales,
   geometria, luz/atmosfera, y un "gestalt" que decide si pasa por foto). Cada uno
   lee `render` + `referencia`, devuelve un **score 0–100** y una lista de
   **giveaways** (que delata que es CG) con **severidad + fix concreto**. Un
   sintetizador los **deduplica y prioriza**.
4. **Arreglar el defecto #1** (materiales/geometria/luz en `blender_build.py` o el
   encuadre/compositor en `import_3dtiles.py`).
5. **Re-render → re-evaluar** y confirmar que el score subio (no asumir; medirlo).
6. **Repetir** hasta que el score se estanca o el usuario dice listo. "Indistinguible"
   es asintotico: el panel siempre encuentra algo (el modo 3D Tiles es lo mas cerca
   porque es fotogrametria real de Google).

Defectos que el panel marca casi siempre y sus fixes (ya aplicados en la skill):
vidrio que no refleja → HDRI + roughness baja + variacion por panel; arboles de
plastico → multi-lobulo + leaf-cards con alpha + translucidez; sin autos → autos
proc.; sin bruma → `setup_compositor` (haze por profundidad, con el **cielo excluido**);
slab flotante del 3D Tiles → plano base + horizonte + haze; agua plana → re-shader
reflectante por mascara de poligono OSM.

Este loop es lo que convierte "un modelo 3D" en "un render que intenta pasar por foto".

## Assets 3D reales (Hyper3D Rodin / Sketchfab, via blender-mcp)
Para enriquecer el modo OSM con arboles/autos/landmarks REALES (en vez de los
proxies procedurales), con blender-mcp conectado y **Hyper3D Rodin** habilitado
en el panel:
- `generate_hyper3d_model_via_text("...")` → `poll_rodin_job_status` → `import_generated_asset`.
  Ej.: un arbol realista ya generado y guardado en `assets/hyper_tree.glb` (reusable
  headless: importar con `bpy.ops.import_scene.gltf` e instanciar en las posiciones
  de arboles/autos que calcula `blender_build.scatter_trees`/`scatter_cars`).
- `search_sketchfab_models` + `download_sketchfab_model` (si Sketchfab esta habilitado con key).
Exportar el asset a GLB (`assets/`) permite reusarlo sin el MCP. **Nota:** el modo
Google 3D Tiles ya trae geometria real; Hyper3D/Sketchfab son para el modo OSM.

## Honestidad (aclararle al usuario)
- **Modo 3D Tiles:** geometria y textura REALES de Google (exacto). Limitaciones:
  cobertura depende de Google; a veces hay artefactos de fotogrametria; el terreno
  llega recortado en diagonal (bordes de los tiles).
- **Modo OSM:** plantas/alturas/trazado reales; colores/fachadas/arboles/autos
  **inferidos** (no verificados). Es maqueta creible, no captura exacta.

## Notas
- Overpass a veces se satura: reintentar (varios endpoints).
- Para otras ciudades: mismo pipeline, solo cambia lat/lng (3D Tiles cubre gran parte del mundo).
- La API key va por variable de entorno; nunca commitearla.
