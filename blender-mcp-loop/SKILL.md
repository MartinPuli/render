---
name: blender-mcp-loop
description: >
  Reconstruye un lugar de Google Maps en un Blender VIVO usando el servidor
  blender-mcp (ahujasid/blender-mcp), y lo va refinando en un loop: construye la
  zona, saca un screenshot del viewport, lo compara con las fotos reales de
  Street View, y ajusta (alturas, colores, materiales, cielo HDRI, modelos de
  landmarks) una y otra vez hasta que el render se parezca lo maximo posible a la
  realidad. Usar cuando el usuario tiene blender-mcp conectado y quiere ver/armar
  un lugar en su Blender local, iterar sobre el modelo, o "que quede igual al
  lugar real". Triggers: "blender-mcp", "blender vivo", "en mi blender", "loop",
  "que quede igual a la realidad", "indistinguible", "refina el modelo",
  "street view vs blender", "landmark 3d".
---

# blender-mcp-loop — Reconstruir un lugar en Blender vivo y refinar hasta que se parezca

Esta skill es el complemento "en vivo" de **maps-to-3d**. En vez de renderizar
headless, usa el MCP **blender-mcp** para construir la escena dentro del Blender
del usuario y **refinarla en un ciclo cerrado** comparando contra Street View.

## Requisitos (verificar primero)
1. **blender-mcp conectado.** El usuario tiene que:
   - Instalar el addon `addon.py` en Blender (Edit → Preferences → Add-ons →
     Install), activar **"Interface: Blender MCP"**, y en la barra lateral del
     3D View (tecla N → pestaña BlenderMCP) apretar **"Connect to Claude"**.
   - Tener el MCP corriendo (`uvx blender-mcp`) y habilitado en el cliente.
   - Verificalo llamando a `get_scene_info`. Si falla, pará y pedile al usuario
     que conecte blender-mcp (no intentes seguir sin el MCP).
2. **Los scripts de maps-to-3d** presentes en el repo (carpeta hermana
   `../scripts/`): `place_to_3d.py`, `blender_build.py`, `live_build.py`.
3. **Python + requests** para bajar los datos: `pip install requests`.
4. **(Opcional pero MUY recomendado) `GOOGLE_MAPS_API_KEY`** — sin las fotos de
   Street View no hay con qué comparar en el loop. Si no hay key, avisá que el
   loop va "a ciegas" (solo mejoras generales) y ofrecé configurarla.
5. Anotá la **ruta absoluta del repo** (`REPO`); la vas a usar en el código que
   mandás a Blender.

> ⚠ **CRÍTICO — no mates la conexión MCP.** NUNCA corras
> `bpy.ops.wm.read_factory_settings()` en el Blender vivo: resetea Blender y
> desregistra el addon blender-mcp, cortando la conexión (después `get_scene_info`
> falla). Para limpiar la escena usá `blender_build.clear_scene()` (borra objetos
> y datos, deja el addon intacto). `live_build.build(clear=True)` ya lo hace así.
>
> 💡 **Foto-realismo (recomendado).** Antes de construir, bajá texturas PBR + HDRI
> (`python3 REPO/scripts/get_textures.py REPO/textures`) y, en el código que mandás
> a Blender, seteá `os.environ["MAPS3D_TEXTURES"]` y `os.environ["MAPS3D_HDRI"]` y
> `blender_build.TEXTURE_DIR`/`HDRI_PATH` **antes** de `build_scene`. Da asfalto/
> hormigón/corteza reales + cielo con nubes (reflejos en vidrio y agua).
>
> 🖼 **Alternativa headless:** `scripts/world_scene.py` arma la escena completa
> (colecciones + cámaras + luz + export + reporte) sin depender del MCP vivo.

## El loop (pasos para Claude)

### Paso 0 — Verificar el MCP
`get_scene_info`. Si responde, blender-mcp está vivo. Si no, pará y pedí conexión.

### Paso 1 — Bajar los datos del área
Corré (en la terminal, no en Blender):
```bash
python3 REPO/scripts/place_to_3d.py "<LUGAR>" --radius 250 --no-render \
        --out REPO/output/<slug>
```
Esto genera `scene.json`, y si hay API key, `streetview/heading_{000,090,180,270}.jpg`
y `photos/*.jpg`. Guardá la ruta del `scene.json` y de las imágenes.
El punto geocodificado queda en el origen (0,0) de la escena, en metros.

### Paso 2 — Construir en el Blender vivo
Con `execute_blender_code`, mandá este bootstrap (reemplazá `REPO` y `<slug>`,
y elegí el heading de un Street View que tengas, ej. 90):
```python
import sys; sys.path.append(r"REPO/scripts")
import importlib, live_build; importlib.reload(live_build)
info = live_build.build(r"REPO/output/<slug>/scene.json",
                        heading=90, sv_xy=(0.0, -8.0))
print(info)
```
- `heading` = mismo rumbo que la foto de Street View con la que vas a comparar.
- `sv_xy` = posición de la cámara en metros. Si el origen cae dentro de un
  edificio/monumento, corré la cámara unos metros (usá `live_build.building_at(
  scene_path, 0, 0)` para detectarlo) hasta la calle.

### Paso 3 — Realismo base (blender-mcp)
- **Cielo/iluminación:** con PolyHaven, buscá y bajá un HDRI de cielo acorde a la
  foto (despejado / nublado / atardecer) y ponelo como world. Da luz realista y
  arregla el cielo apagado del setup básico.
  (tools: `get_polyhaven_categories("hdris")`, `search_polyhaven_assets`,
  `download_polyhaven_asset`.)
- **Texturas (opcional):** PolyHaven tiene asfalto, veredas, hormigón; aplicalas
  al piso/calles y a fachadas para acercarte a la realidad (`set_texture`).

### Paso 4 — Screenshot del viewport
`get_viewport_screenshot`. Guardá la imagen.

### Paso 5 — Comparar sim vs realidad
Leé con `Read` la foto real (`streetview/heading_090.jpg`) **y** el screenshot.
Comparalos punto por punto y hacé una lista concreta de diferencias:
- **Landmarks mal:** ¿algún edificio icónico salió como una caja genérica? (ej:
  el Obelisco sale 9 m con techito, cuando es una aguja de ~67 m). Es lo primero
  a arreglar.
- **Alturas/proporciones** de los edificios del frente.
- **Colores y materiales** (fachadas, vidriado, techos).
- **Cielo/luz** (dirección del sol, tono).
- **Cosas que faltan** (árboles, cartelería, la calle en primer plano).

### Paso 6 — Refinar (acá está la magia del loop)
Aplicá los arreglos con `execute_blender_code` y los tools del MCP:
- **Corregir alturas:** reescribí la altura de edificios clave y reconstruí, o
  editá el objeto directo en Blender.
- **Traer el landmark real** (lo que más acerca a "indistinguible"):
  - **Sketchfab:** `search_sketchfab_models("Obelisco Buenos Aires")` →
    `download_sketchfab_model` → posicionarlo en el origen y escalarlo.
  - **Hyper3D Rodin:** `generate_hyper3d_model_via_images` pasándole la foto de
    Street View del landmark → `poll_rodin_job_status` → `import_generated_asset`
    → ubicarlo y escalarlo. Borrá la caja genérica que lo representaba.
- **Materiales/colores:** ajustá con `apply_materials` o por código para igualar
  la foto (ej. edificios blancos, vidrio azulado, cartel LED).
- **Cámara:** afiná posición/heading/FOV para encuadrar como la foto.
- **Vegetación/props:** sumá árboles (PolyHaven/Sketchfab) donde la foto los tenga.

### Paso 7 — Repetir hasta que se parezca
Volvé al Paso 4 (screenshot → comparar → refinar). **Criterio de corte:** parás
cuando (a) las proporciones y los landmarks coinciden, (b) los colores/materiales
y el cielo son creíbles, y (c) a primera vista el screenshot pasa por una foto del
lugar — o cuando el usuario diga "listo". Hacé típicamente 3–6 pasadas; en cada
una mostrale al usuario el screenshot y qué cambiaste. Si algo no cierra por falta
de datos (fachadas exactas), decilo en vez de dar vueltas infinitas.

## Notas de honestidad
- "Indistinguible de la realidad" es la **meta** del loop, no una garantía: la
  base viene de OSM (volúmenes + alturas) y de lo que consigas en PolyHaven /
  Sketchfab / Hyper3D. Los landmarks quedan muy bien; una cuadra genérica sin
  fachadas fotográficas queda "creíble", no idéntica.
- `execute_blender_code` corre Python arbitrario en el Blender del usuario:
  **guardá el .blend antes** y avisá.
- Todo corre **en local** (el MCP se conecta al Blender del usuario); esta skill
  no puede ejecutarse en un entorno headless sin el addon conectado.

## Un ciclo de ejemplo (Obelisco)
1. `place_to_3d.py "Obelisco, Buenos Aires" --radius 250` → datos + Street View.
2. `live_build.build(..., heading=90, sv_xy=(0,-8))` → ciudad + cámara en la calle.
3. PolyHaven HDRI de cielo despejado.
4. Screenshot → comparar con `heading_090.jpg`.
5. Detectás: el Obelisco es una cajita → Sketchfab "Obelisco Buenos Aires",
   importás, lo parás en (0,0) escalado a ~67 m; subís la altura de un par de
   torres; ponés vidrio azulado en un edificio; sumás los carteles LED.
6. Screenshot → comparar → ajustar → repetir hasta que pase por foto.
