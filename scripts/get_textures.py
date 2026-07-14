#!/usr/bin/env python3
"""
get_textures.py — baja texturas PBR CC0 (PolyHaven) + un HDRI de cielo, para el
modo FOTO-REAL de la skill. Todo CC0 (uso libre), via la API publica de PolyHaven.

Uso:
    python3 scripts/get_textures.py [carpeta_destino]   # default: ./textures
    export MAPS3D_TEXTURES="$(pwd)/textures"
    export MAPS3D_HDRI="$(pwd)/textures/sky.hdr"

Con esas variables, blender_build.py usa:
  - asfalto / hormigon / corteza con mapas PBR reales (color+rugosidad+normal),
  - un HDRI de cielo real (con nubes) como iluminacion y fuente de reflejos
    (clave para que el vidrio y el agua se vean reales).

Descarga por textura: <name>_diff.jpg, <name>_rough.jpg, <name>_nor.jpg
Descarga de cielo:    sky.hdr
"""
import os
import sys

try:
    import requests
except ImportError:
    sys.exit("Falta 'requests': python3 -m pip install requests")

RES = os.environ.get("MAPS3D_TEXRES", "2k")   # 1k liviano; 2k recomendado; 4k pesado

# nombre local -> asset de PolyHaven (texturas PBR)
TEXTURES = {
    "asphalt": "asphalt_02",
    "concrete": "concrete_floor_02",   # vereda/plaza gris (hormigon liso)
    "bark": "bark_brown_02",
}
# mapa PolyHaven -> sufijo local y formatos preferidos
MAP_KEYS = [
    ("Diffuse", "diff", ("jpg", "png")),
    ("Rough", "rough", ("jpg", "png")),
    ("nor_gl", "nor", ("jpg", "png", "exr")),
]
# HDRIs candidatos (cielo de dia despejado con algo de nubes); se prueba en orden
HDRI_CANDIDATES = [
    "kloofendal_48d_partly_cloudy_puresky",
    "qwantani_puresky",
    "kloppenheim_06_puresky",
    "spaichingen_hill_puresky",
    "kloofendal_43d_clear_puresky",
]


def _files(asset):
    return requests.get(f"https://api.polyhaven.com/files/{asset}", timeout=30).json()


def _pick(node, res, fmts):
    """De un nodo {res: {fmt: {url}}} devuelve (url, fmt) para res+formato preferido."""
    resd = node.get(res) or next(iter(node.values()), None)
    if not resd:
        return None
    for fmt in fmts:
        if fmt in resd and "url" in resd[fmt]:
            return resd[fmt]["url"], fmt
    for fmt, v in resd.items():
        if isinstance(v, dict) and "url" in v:
            return v["url"], fmt
    return None


def _dl(url, dest):
    r = requests.get(url, timeout=180)
    r.raise_for_status()
    with open(dest, "wb") as f:
        f.write(r.content)


def fetch_texture(name, asset, out):
    try:
        data = _files(asset)
    except Exception as e:
        print(f"  (API falló para {asset}: {e})")
        return
    for mapkey, suffix, fmts in MAP_KEYS:
        node = data.get(mapkey)
        if not node:
            continue
        picked = _pick(node, RES, fmts)
        if not picked:
            continue
        url, fmt = picked
        ext = "jpg" if fmt in ("jpg", "png") else fmt
        dest = os.path.join(out, f"{name}_{suffix}.{ext}")
        if os.path.isfile(dest) and os.path.getsize(dest) > 1024:
            print(f"  {asset}: {mapkey} ya existe -> {os.path.basename(dest)} (skip)")
            continue
        try:
            print(f"  {asset}: {mapkey} ({RES},{fmt}) -> {os.path.basename(dest)}")
            _dl(url, dest)
        except Exception as e:
            print(f"    (falló {mapkey}: {e})")


def fetch_hdri(out):
    for ext in ("hdr", "exr"):
        p = os.path.join(out, "sky." + ext)
        if os.path.isfile(p) and os.path.getsize(p) > 1024:
            print(f"HDRI ya existe -> {os.path.basename(p)} (skip)")
            return p
    for asset in HDRI_CANDIDATES:
        try:
            data = _files(asset)
            node = data.get("hdri")
            if not node:
                continue
            picked = _pick(node, "2k", ("hdr", "exr"))
            if not picked:
                continue
            url, fmt = picked
            dest = os.path.join(out, "sky." + ("hdr" if fmt == "hdr" else "exr"))
            print(f"HDRI {asset} (2k,{fmt}) -> {os.path.basename(dest)}")
            _dl(url, dest)
            return dest
        except Exception as e:
            print(f"  (HDRI {asset} falló: {e})")
    print("  (no pude bajar ningun HDRI)")
    return None


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "textures"
    os.makedirs(out, exist_ok=True)
    print(f"Bajando texturas PBR CC0 ({RES}) a {out}/ ...")
    for name, asset in TEXTURES.items():
        fetch_texture(name, asset, out)
    sky = fetch_hdri(out)
    print("\nListo. Exportá:")
    print(f'  export MAPS3D_TEXTURES="{os.path.abspath(out)}"')
    if sky:
        print(f'  export MAPS3D_HDRI="{os.path.abspath(sky)}"')


if __name__ == "__main__":
    main()
