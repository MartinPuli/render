# maps-to-3d 🌎→🟦 — de un lugar real a una escena 3D en Blender

Le pasás un lugar (link de Google Maps, `"lat,lng"` o un nombre) y te arma una
escena 3D en Blender. Lo diferencial no es "OSM → Blender" (eso ya existe), sino el
**loop con agente**: `lugar → escena → render → comparación con la realidad →
corrección automática → repetir`, hasta que un evaluador la aprueba.

Dos modos:

| Modo | Geometría | Uso | Salida |
|---|---|---|---|
| **EXACTO** | **Google Photorealistic 3D Tiles** (malla fotogramétrica real, texturizada) | render foto-real de cualquier ciudad del mundo | **imágenes** (render-only, ver ToS) |
| **ESTILIZADO** | **OpenStreetMap** (extrusión de volúmenes) + materiales PBR/procedurales | maqueta liviana, editable, redistribuible | `.blend` + GLB + `report.json` |

## ⚠️ Compliance — leer antes de usar en producción
- **Modo EXACTO (Google 3D Tiles) = "render-only".** Las [políticas de Map Tiles](https://developers.google.com/maps/documentation/tile/policies)
  **prohíben** precache/almacenamiento/uso offline y extracción de la malla. En prod:
  emitir **solo imágenes** renderizadas, mantener los tiles en un **cache temporal**
  (respetando `max-age`, borrado tras el render), **no** exportar la geometría de Google
  a `.blend`/GLB persistente, y **componer la atribución** (agregado de `asset.copyright`
  de los tiles visibles, ordenado por frecuencia, + logo de Google) en cada frame.
  *El repo hoy guarda tiles para iterar rápido; para publicar hay que activar el modo
  efímero + atribución (ver `SKILL.md` → roadmap).*
- **Modo ESTILIZADO (OSM) = redistribuible** bajo **ODbL** (atribuir a OpenStreetMap).
- Texturas/HDRI de **PolyHaven** = **CC0**. Street View = referencia interna (no redistribuir).

## Quickstart
```bash
python3 -m pip install requests
export GOOGLE_MAPS_API_KEY="tu_key"            # Map Tiles API + Street View + Static + Elevation
export MAPS3D_HDRI="$(pwd)/textures/sky.hdr"   # opcional (get_textures.py)

# --- EXACTO (Google 3D Tiles) ---
python3 scripts/fetch_3dtiles.py <LAT> <LON> 380 output/<lugar>/tiles3d --detail 12 --max-tiles 260
blender -b -P scripts/import_3dtiles.py -- output/<lugar>/tiles3d output/<lugar>/tiles3d

# --- ESTILIZADO (OSM) ---
python3 scripts/place_to_3d.py "<lugar>" --radius 350 --no-render --out output/<lugar>
python3 scripts/get_textures.py textures       # PBR + HDRI (opcional)
blender -b -P scripts/world_scene.py -- output/<lugar>/scene.json output/<lugar> <lugar> --export glb
```
Requiere **Blender 5.x** (binario o módulo `bpy`). Probado con `/Applications/Blender.app`.

## Loop de evaluación (el corazón)
Render → bajar la **referencia real** de esa misma vista (Street View / satélite) →
**panel adversarial** de agentes (materiales / geometría / luz / gestalt) que puntúa y
prioriza defectos → arreglar el #1 → re-render → re-evaluar. Protocolo best-practice
(2AFC, pose-match, panel multi-familia, métricas CMMD/CLIP-IQA, regla de corte) en
[`EVALUATION.md`](EVALUATION.md).

## Fuentes de datos
Google 3D Tiles (geometría exacta) · Elevation · Static-Maps satélite · Street View ·
OpenStreetMap · PolyHaven. Qué es real vs inferido → `SKILL.md`.

## Scripts
`place_to_3d.py` (lugar→OSM+StreetView→scene.json) · `fetch_3dtiles.py` (baja 3D Tiles) ·
`import_3dtiles.py` (importa+ilumina+render) · `blender_build.py` (geometría+materiales OSM,
14 colecciones) · `world_scene.py` (orquesta OSM headless) · `render_view.py` (vista para
comparar) · `get_textures.py` (PBR+HDRI) · `live_build.py` (build en Blender vivo via blender-mcp).

## Roadmap (research + review)
- **Motor geométrico:** apoyarse en **OSM2World / BlenderGIS / blosm** para el modo OSM en
  vez de mantener a mano gran parte de `blender_build.py`; separar **fuentes → CityScene
  normalizada → render**.
- **LOD por SSE** (screen-space-error) en 3D Tiles en vez del near-test OBB; **atribución +
  modo efímero** para publicar.
- **Techos reales** (`roof:shape`), **materiales por tag OSM** (`building:material/colour`),
  **cascada de alturas** (levels×3m + jitter), **identidad por edificio** (ID OSM por
  instancia via atributos/Geometry Nodes).
- **Landmarks geofenced / por ID OSM** (no reglas globales: una pasarela ≠ Puente de la Mujer).
- **Perfiles urbanos** (`informal_dense`, `historic_center`, `industrial`, `modern_towers`).
- **Chequeos automáticos** de escena (cámara fuera de edificios, landmarks improbables,
  siluetas/alturas, 4 vistas de control, snapshots de regresión).
- **Imágenes abiertas** (KartaView/Mapillary) y **fotogrametría propia** (OpenDroneMap)
  cuando no hay Google; **py3dtiles** para datasets propios.

## Licencia / atribución
Geometría OSM © colaboradores de OpenStreetMap (**ODbL**). Google 3D Tiles/satélite:
sujeto a los términos de Google Maps Platform (atribución obligatoria, render-only).
Texturas/HDRI PolyHaven: **CC0**.
