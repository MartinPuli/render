#!/usr/bin/env python3
"""
get_textures.py — baja texturas CC0 de PolyHaven para el modo texturizado.

Uso:
    python3 scripts/get_textures.py [carpeta_destino]   # default: ./textures
    export MAPS3D_TEXTURES="$(pwd)/textures"

Con MAPS3D_TEXTURES seteada, blender_build.py usa asfalto real en las calles y
pavimento real en el piso (en vez de colores planos). Todo CC0 (uso libre).
"""
import os
import sys

try:
    import requests
except ImportError:
    sys.exit("Falta 'requests': python3 -m pip install requests")

# archivo local -> asset de PolyHaven
ASSETS = {
    "asphalt.jpg": "asphalt_02",
    "pavement.jpg": "pavement_02",
    "concrete.jpg": "concrete_wall_008",
}
RES = "1k"  # resolucion (1k liviano; 2k/4k para mas detalle)


def diffuse_url(asset, res=RES):
    d = requests.get(f"https://api.polyhaven.com/files/{asset}", timeout=30).json()
    return d["Diffuse"][res]["jpg"]["url"]


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "textures"
    os.makedirs(out, exist_ok=True)
    for fname, asset in ASSETS.items():
        dest = os.path.join(out, fname)
        try:
            url = diffuse_url(asset)
            print(f"bajando {asset} ({RES}) -> {dest}")
            r = requests.get(url, timeout=90)
            r.raise_for_status()
            with open(dest, "wb") as f:
                f.write(r.content)
        except Exception as e:
            print(f"  (falló {asset}: {e})")
    print("\nListo. Ahora:")
    print(f'  export MAPS3D_TEXTURES="{os.path.abspath(out)}"')
    print("y volvé a correr place_to_3d.py / blender_build.py")


if __name__ == "__main__":
    main()
