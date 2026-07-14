---
name: maps-to-3d
description: >
  Convierte un lugar de Google Maps en un modelo 3D con colores en Blender y lo
  renderiza para que Claude pueda "verlo". Dado un link de Google Maps, unas
  coordenadas (lat,lng) o el nombre de un lugar, baja la geometria real de la
  zona desde OpenStreetMap (edificios con alturas, calles, agua, parques),
  opcionalmente baja imagenes de Street View y fotos de Google, construye la
  escena 3D en Blender y produce un render PNG + un archivo .blend editable.
  Usar cuando el usuario pasa un lugar/direccion/link de Maps y quiere verlo o
  disenarlo en 3D, en Blender, o como maqueta/render de una zona o ciudad.
  Triggers: "google maps", "street view", "en 3d", "blender", "maqueta 3d",
  "render de este lugar", "reconstrui esta zona", "modelo de la ciudad",
  "lugar a 3d", "verlo caminando".
---

# maps-to-3d — De un lugar de Google Maps a un modelo 3D en Blender

## Que hace
Le pasas un lugar (link de Google Maps / `lat,lng` / nombre) y genera:
- `render.png` — render 3D de la zona con colores (vista 3/4 aerea).
- `model.blend` — el modelo 3D editable en Blender.
- `scene.json` — la geometria intermedia (metros).
- `streetview/` y `photos/` — imagenes reales de Google (si hay API key).

La geometria de los edificios (con sus alturas), calles, agua y parques sale de
**OpenStreetMap** (gratis, sin API key). Street View y las fotos del lugar salen
de **Google Maps Platform** (necesitan `GOOGLE_MAPS_API_KEY`).

## Cuando usar esta skill
Cuando el usuario:
- Pega un link de Google Maps y quiere verlo/disenarlo en 3D o en Blender.
- Pide una maqueta, render o modelo 3D de una zona, barrio o ciudad.
- Quiere "explorar" un lugar en 3D a partir del mapa.

## Requisitos (verificar antes de correr)
1. **Blender** disponible de una de estas dos formas:
   - Binario `blender` en el PATH (o pasar `--blender /ruta/a/blender`), **o**
   - El modulo de Python: `python3 -m pip install bpy` (usa Python 3.11).
   Detectar cual hay: `command -v blender` y `python3 -c "import bpy"`.
2. **Python** con `requests`: `python3 -m pip install requests`.
3. **(Opcional) `GOOGLE_MAPS_API_KEY`** exportada, con Street View Static API,
   Places API y Geocoding API habilitadas. Sin esto igual funciona, pero sin
   imagenes reales de Google y sin buscar lugares por nombre.

## Como usarla (pasos para Claude)

1. **Resolver como se corre Blender.** Si hay binario `blender`, usar
   `--blender $(command -v blender)`. Si no, verificar que `import bpy` funcione
   y correr sin `--blender` (usa el modulo). Si no hay ninguno, instalar bpy
   (`pip install bpy`) o pedirle al usuario la ruta de Blender.

2. **Correr el pipeline** desde el directorio de esta skill:
   ```bash
   python3 scripts/place_to_3d.py "<LUGAR>" --radius 250
   ```
   Donde `<LUGAR>` puede ser:
   - Un link: `"https://maps.app.goo.gl/xxxx"` o `"https://www.google.com/maps/@-34.60,-58.38,17z"`
   - Coordenadas: `"-34.6037,-58.3816"`  (¡entre comillas, por el signo menos!)
   - Un nombre: `"Obelisco, Buenos Aires"`  (necesita `GOOGLE_MAPS_API_KEY`)

   Opciones utiles:
   - `--radius M` — tamano de la zona en metros (default 250). Zona chica de
     detalle: 120-200. Barrio: 300-500. Ojo: mas radio = mas pesado.
   - `--out DIR` — carpeta de salida (default `output/<slug>`).
   - `--no-streetview` — no bajar imagenes de Google.
   - `--no-render` — solo bajar datos (util para inspeccionar `scene.json`).
   - `--blender PATH` — ruta al binario de Blender.

3. **Mirar el resultado.** Usar la tool `Read` sobre `output/<slug>/render.png`
   para ver el modelo 3D. Si hay `streetview/*.jpg` y `photos/*.jpg`, leerlas
   tambien para comparar el modelo con el lugar real y describirle al usuario
   que se ve.

4. **Iterar si hace falta:**
   - Zona muy vacia o muy llena → ajustar `--radius`.
   - Colores → editar las paletas `BUILDING_COLORS` / `VARIED_NEUTRALS` en
     `scripts/place_to_3d.py`, o los materiales/luz en `scripts/blender_build.py`
     (constantes arriba de todo: `SUN_ENERGY`, `EXPOSURE`, `CAM_*`).
   - Angulo/altura de camara → `CAM_ELEV_DEG`, `CAM_AZIM_DEG`, `CAM_DIST_FACTOR`.

5. **Entregar** al usuario el `render.png` (con `SendUserFile`) y avisarle que
   `model.blend` se puede abrir/editar en Blender.

## Notas
- Overpass/OSM a veces se satura: si da timeout, reintentar (el script ya prueba
  dos endpoints). Un par de reintentos suele resolverlo.
- Las alturas salen de los tags `height` / `building:levels` de OSM; si faltan se
  estima por tipo de edificio. No es exacto pero da una maqueta creible.
- 1 unidad de Blender = 1 metro. La escena esta centrada en el lugar (0,0).
