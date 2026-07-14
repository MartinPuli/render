# OSM2World (motor alternativo OPCIONAL)

Este proyecto genera ciudades 3D con un **generador procedural propio** (en
`scripts/`, construido con `bpy`). Ese generador es el **motor por defecto** y
**no se elimina**.

[OSM2World](https://osm2world.org/) es un conversor externo, maduro, de datos
OpenStreetMap a geometria 3D (edificios con techos, calles, materiales por tags,
etc.). Se ofrece aca como **motor alternativo, totalmente OPT-IN**: solo se usa
si vos lo instalas y lo activas por variable de entorno. Si no esta disponible,
el pipeline cae automaticamente al generador procedural.

## ¿Por que es opcional?

- **Pros:** conversion real OSM -> 3D lista para usar (incluye techos y
  materiales), proyecto maduro, cobertura amplia de tags OSM.
- **Contras:** depende de la **JVM** (necesita `java` + un `.jar` descargado
  aparte, no es `pip install`), da menos control nativo desde Blender (malla ya
  "horneada") y trae un **pipeline de materiales separado** que no se integra con
  nuestros perfiles de zona / texturas CC0 / ventanas procedurales.

Por eso el generador procedural sigue siendo el default y OSM2World queda como
extra para quien lo quiera.

## Descargar OSM2World

1. Entra a la pagina oficial: https://osm2world.org/ (seccion Download).
2. Baja la ultima release estable (un `.zip`/`.tar` con el `OSM2World.jar`).
3. Descomprimilo en una carpeta a tu eleccion, por ejemplo:

   ```
   ~/tools/osm2world/OSM2World.jar
   ```

Necesitas ademas un **Java** instalado (JRE/JDK 17+ recomendado). Verificalo con:

```bash
java -version
```

Este repo **no descarga** OSM2World por vos: es un paso manual y opt-in.

## Activar OSM2World

Definí la variable de entorno `OSM2WORLD_JAR` apuntando al `.jar`. Opcionalmente
podes fijar el binario de java con `JAVA_BIN` (si no, se usa el `java` del PATH):

```bash
export OSM2WORLD_JAR="$HOME/tools/osm2world/OSM2World.jar"
# opcional, si java no esta en el PATH:
export JAVA_BIN="/usr/bin/java"
```

El adaptador considera OSM2World "disponible" solo si:

- `OSM2WORLD_JAR` (o el argumento `jar=`) apunta a un archivo existente, **y**
- hay un binario `java` localizable (`shutil.which('java')` o `JAVA_BIN`).

## Ejecutarlo

Desde Python, via el adaptador `scripts/osm2world_adapter.py`:

```python
import osm2world_adapter as o2w

if o2w.osm2world_available():
    obj = o2w.run_osm2world("zona.osm", "zona.obj")   # luego importar el OBJ
else:
    ...  # fallback: generador procedural integrado (default)
```

`run_osm2world` construye el comando:

```
java -jar $OSM2WORLD_JAR --input zona.osm --output zona.obj [extra_args...]
```

y lo corre con `subprocess.run(..., check=True)`, devolviendo la ruta del OBJ.

Si OSM2World **no** esta disponible, `run_osm2world(...)` lanza
`OSM2WorldUnavailable`, que el caller debe capturar para caer al generador
procedural.

Tambien podes pasar rutas/flags explicitos sin variables de entorno:

```python
o2w.run_osm2world(
    "zona.osm", "zona.obj",
    jar="/ruta/OSM2World.jar",
    java="/usr/bin/java",
    extra_args=["--config", "standard.properties"],
)
```

## Resumen

- El **generador procedural propio es el default** y no se borra nada.
- OSM2World es un **extra opt-in** activado con `OSM2WORLD_JAR`.
- Sin jar/java disponible, el pipeline sigue funcionando con el motor procedural.
