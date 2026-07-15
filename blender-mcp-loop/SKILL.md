---
name: blender-mcp-loop
description: Construye y refina lugares reales dentro de un Blender vivo conectado por blender-mcp. Usar cuando el usuario pide convertir una ubicación, coordenadas o link de Google Maps en una escena Blender, especialmente si dice “Blender MCP”, “en mi Blender”, “one shot”, “que se parezca al lugar”, “Street View vs Blender”, aeropuerto, barrio, campus, puerto o infraestructura. Ejecuta preflight, backup, adquisición OSM/Google, capas semánticas, build seguro, cámara, dos renders, inspección visual, autocorrección, validación y guardado sin desconectar el addon.
---

# Blender MCP loop

Operar un loop engineering cerrado hasta aprobar la escena o restaurar el mejor checkpoint. “One shot” describe la experiencia del usuario —no tiene que dirigir las iteraciones—, no una única ejecución interna. Usar `maps-to-3d` para los datos y este skill para operar el Blender vivo.

## Reglas críticas

- Verificar primero con `get_scene_info` o una consulta equivalente.
- Detenerse sólo si Blender MCP no responde. Explicar cómo conectar el addon.
- Guardar un backup antes de limpiar.
- Usar `blender_build.clear_scene()` o `live_build.build(clear=True)`.
- Nunca ejecutar `bpy.ops.wm.read_factory_settings()` en Blender vivo: desregistra el addon y corta MCP.
- Mantener API keys sólo en variables de entorno. No escribirlas en archivos, logs, código ni respuesta. Si el usuario pegó una key en el chat, recomendar rotarla/restringirla al terminar.
- No afirmar exactitud fotográfica cuando OSM sólo aporta plantas y alturas estimadas.

## Gate de generalización

Antes de modificar el skill o sus scripts, clasificar el cambio:

- **Core general:** se activa por schema, tags OSM, métricas o capacidades de Blender. Puede entrar al core.
- **Perfil semántico:** aeropuerto, puerto, ferrocarril, campus, costa, etc. Debe depender de tags, no del nombre del lugar.
- **Ajuste de escena:** cámara, material o asset elegido para una ejecución. Guardarlo en `output/<slug>` o en `scene.json`; no convertirlo en default global.
- **Pack opcional:** landmark/asset propietario o local. Cargarlo explícitamente; nunca añadir su nombre o coordenadas al registro por defecto.

Prohibir en el core cualquier regla del tipo `si ciudad == X`, geocerca preinstalada, nombre de landmark o coordenada concreta. Para promover una heurística, exigir un test sintético por tags y al menos un contraejemplo no relacionado. Una mejora visual en una sola escena no demuestra generalidad.

## Transporte MCP

Preferir las herramientas nativas `get_scene_info`, `execute_code`/`execute_blender_code` y `get_viewport_screenshot`.

Si el cliente no expone esas herramientas pero el addon escucha en `127.0.0.1:9876`, usar el cliente instalado por blender-mcp:

```python
from blender_mcp.server import BlenderConnection
c = BlenderConnection("127.0.0.1", 9876)
try:
    result = c.send_command("execute_code", {"code": code})
finally:
    c.disconnect()
```

No confundir ausencia de una tool visible con ausencia del servidor: comprobar el puerto antes de pedir intervención.

## Loop engineering obligatorio

Aplicar siempre esta máquina de estados:

`preflight → baseline → observe → score → rank defect → hypothesize → change one family → checkpoint → compare delta → accept/continue/restore best`

No saltar de baseline a entrega. `one_shot_payload` sólo construye la iteración cero.

### 1. Preflight y backup

1. Consultar la escena activa y anotar archivo, objetos, materiales y cámara.
2. Crear `<out>/pre_<slug>.blend` antes del clear.
3. Confirmar rutas absolutas del repo, `scripts/`, texturas y HDRI.
4. No pedir confirmaciones si el lugar y Blender conectado ya están claros.

### 2. Elegir radio y modo

Usar estos valores iniciales, salvo que el usuario indique otro alcance:

| Lugar | Radio |
|---|---:|
| Landmark/edificio | 250–350 m |
| Barrio pequeño | 500–700 m |
| Aeropuerto/campus/puerto | 900–1200 m |
| Distrito amplio | 1200–1800 m |

Usar OSM editable por defecto en Blender vivo. Usar Google Photorealistic 3D Tiles sólo si el usuario prioriza fotogrametría exacta y la Map Tiles API está habilitada.

### 3. Generar datos completos

Ejecutar desde la terminal:

```bash
python3 REPO/scripts/place_to_3d.py "<LUGAR>" --radius <M> --no-render --out REPO/output/<slug>
```

El generador debe producir `scene.json` con edificios, calles, agua/verde y `special_features`. Actualmente reconoce aeropuertos y conserva pistas, taxiways, plataformas y helipuertos con semántica propia. Street View usa sólo panoramas exteriores; la imagen satelital es la referencia aérea.

Inspeccionar el resumen antes del build:

- Si `scene_kind=airport`, exigir `special_feature_count > 0` cuando el encuadre contiene pistas/plataformas.
- Si no hay edificios ni calles, no construir una escena vacía: ampliar radio o revisar geocodificación.
- Usar `data_quality.building_heights` para distinguir alturas explícitas de estimadas.
- No usar una foto interior para validar una fachada exterior.

### 4. Construir el baseline

Generar el código con `scripts/mcp_loop.py` y enviarlo completo a Blender:

```python
from mcp_loop import one_shot_payload
code = one_shot_payload(
    scene_path="REPO/output/<slug>/scene.json",
    out_dir="REPO/output/<slug>",
    slug="<slug>_mcp",
    scripts_dir="REPO/scripts",
    textures_dir="REPO/textures",
    hdri_path="REPO/textures/sky.hdr",
    engine="AUTO",
    samples=48,
    res=(1400, 1000),
)
```

El payload realiza backup, clear seguro, build, selección compatible de Eevee/Cycles, aérea + oblicua, restauración de cámara, guardado y validación. Exigir `BASELINE_READY`; `ONE_SHOT_OK` queda sólo por compatibilidad.

### 5. Inicializar el estado medible

Usar `scripts/loop_engineering.py` en el proceso host:

```python
import loop_engineering as le
state = le.new_state(
    project="<slug>",
    references=["reference_satellite.png"],
    max_iterations=6,
    target_score=85,
    min_dimension=70,
    min_delta=1.0,
    patience=2,
)
```

Persistirlo como `loop_state.json` después de cada observación con `le.save_state`. Nunca guardar el estado sólo en memoria conversacional.

### 6. Observar y puntuar

Abrir ambos renders y, si existe, la referencia satelital o Street View del mismo punto. Evaluar en este orden:

1. `semantics`: capas críticas presentes y correctamente tipadas.
2. `geometry`: escala, alturas, cubiertas, densidad y artefactos.
3. `framing`: sujeto legible, sin cortes, misma vista que la referencia.
4. `materials`: paleta, rugosidad, vidrio, asfalto, agua y vegetación.
5. `lighting`: exposición, sombras, cielo y contraste.
6. `realism`: lectura global y giveaways procedurales.

Asignar 0–100 a las seis dimensiones. Registrar defectos como `{category, severity, impact, fix}`. Marcar `critical` cualquier falta semántica, escena vacía, cámara inválida, geometría rota o render quemado/negro.

```python
state = le.record_iteration(
    state,
    scores=scores,
    defects=defects,
    change=None,  # baseline
    artifacts={"render": "...", "blend": "..."},
)
le.save_state(state, "<out>/loop_state.json")
```

### 7. Formular y aplicar un cambio controlado

Consultar `le.decision(state)`. Si devuelve `correct_one_family`, tomar sólo `next_defect` y formular:

- Observación verificable.
- Hipótesis causal.
- Una familia de cambio: `semantics`, `geometry`, `framing`, `materials`, `lighting` o `realism`.
- Resultado esperado y métrica que debe subir.

No mezclar cámara + materiales + luz en una iteración: impide atribuir el delta. Aplicar el fix por MCP, luego congelar el artefacto:

```python
code = mcp_loop.iteration_payload("<out>", iteration=N)
```

Abrir el nuevo PNG, puntuarlo contra la misma referencia/cámara y registrar:

```python
state = le.record_iteration(
    state, scores=new_scores, defects=new_defects,
    change={"category": "framing", "hypothesis": "...", "fix": "..."},
    artifacts={"render": "loop_01.png", "blend": "loop_01.blend"},
)
```

### 8. Decidir por evidencia

- `deliver`: aceptar sólo con score ponderado ≥85, ninguna dimensión <70 y cero defectos críticos.
- `correct_one_family`: continuar automáticamente hasta seis iteraciones.
- `restore_best`: al llegar al máximo o acumular dos deltas <1 punto, abrir el `.blend` de `best_iteration` con `restore_checkpoint_payload` y verificar MCP nuevamente.

Si una corrección baja el score, no acumularla como nueva base ganadora. Mantener el checkpoint anterior y cambiar de hipótesis.


## Correcciones deterministas

| Síntoma | Corrección |
|---|---|
| Vista demasiado abierta | Acercar cámara y mover `Target` al sujeto; conservar una aérea general |
| Pasto domina la imagen | Bajar saturación/luminancia; en aeropuertos no dispersar árboles sobre grass/meadow |
| Aeropuerto parece barrio | Forzar techos planos y paleta vidrio/acero/hormigón; usar `special_features` |
| Pistas ausentes | Revisar radio y que la consulta incluya `aeroway`; no inventarlas manualmente antes de reconsultar |
| Motor no existe | Probar `BLENDER_EEVEE_NEXT`, `BLENDER_EEVEE`, luego `CYCLES` |
| Look de color no existe | Probar `Medium High Contrast`, variante AgX, luego `None` |
| Cámara dentro de edificio | Usar `citycamera.safe_street_point` |
| Street View no coincide | Confirmar `source=outdoor`, ubicación, heading y FOV; si no hay exterior, usar satélite/fotos |
| Landmark genérico | Reemplazar sólo con asset verificable de Hyper3D/Sketchfab o modelado procedural específico |

## Contrato de aceptación

No declarar “listo” hasta comprobar:

- MCP sigue respondiendo.
- Existe backup, `.blend`, render aéreo, render oblicuo y reporte; cada archivo es no vacío.
- Hay cámara activa, meshes y materiales.
- La cámara aérea queda activa al guardar.
- La escena especial conserva sus capas críticas.
- Los renders fueron abiertos e inspeccionados, no sólo generados.
- Existe `loop_state.json` con baseline, scores, deltas, cambios y `best_iteration`.
- El resultado entregado corresponde al checkpoint ganador, no necesariamente al último.
- La entrega distingue datos reales, estimaciones y proxies.

Entregar links absolutos al `.blend`, ambos PNG, `scene.json` y reporte. Mostrar el mejor render en la respuesta.

## Límites honestos

- OSM aporta trazado real, pero muchas alturas/fachadas son estimadas.
- Street View puede no tener cobertura exterior.
- Los aviones/autos/árboles procedurales aportan escala, no estado real del lugar.
- “One shot” significa un loop engineering autónomo sin microgestión del usuario, no una sola llamada ni una garantía de réplica perfecta sin datos suficientes.
