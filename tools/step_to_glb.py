#!/usr/bin/env python3
"""Convert a coloured STEP assembly to a GLB for the web viewer.

Usage:
    python tools/step_to_glb.py INPUT.step OUTPUT.glb [--tolerance 0.05]

Loads the assembly with CadQuery (part names and colours survive),
tessellates it, and writes a binary glTF. Any part whose STEP colour has
an alpha below 1 is made transparent in the GLB, which CadQuery's own
export does not do. Needs cadquery.
"""

import argparse
import json
import os
import struct
import sys

import cadquery as cq


def export(src: str, dst: str, tolerance: float, angular: float) -> dict:
    assy = cq.Assembly.load(src)
    alpha = {}

    def visit(node):
        for child in node.children:
            if child.color is not None:
                a = child.color.toTuple()[3]
                if a < 1.0:
                    alpha[child.name] = a
            visit(child)

    visit(assy)
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    assy.export(dst, exportType="GLTF", tolerance=tolerance, angularTolerance=angular)
    return alpha


def patch_alpha(path: str, alpha: dict) -> int:
    """Set alphaMode BLEND and the alpha channel on materials of transparent parts."""
    if not alpha:
        return 0
    with open(path, "rb") as fh:
        magic, version, length = struct.unpack("<III", fh.read(12))
        assert magic == 0x46546C67, "not a GLB"
        json_len, json_type = struct.unpack("<II", fh.read(8))
        js = json.loads(fh.read(json_len))
        rest = fh.read()

    materials = js.get("materials", [])
    meshes = js.get("meshes", [])
    made = {}  # (original material index, part name) -> new material index
    patched = 0
    for node in js.get("nodes", []):
        a = alpha.get(node.get("name"))
        if a is None or "mesh" not in node:
            continue
        for prim in meshes[node["mesh"]].get("primitives", []):
            mi = prim.get("material")
            if mi is None:
                continue
            key = (mi, node["name"])
            existing = materials[mi].get("pbrMetallicRoughness", {}).get("baseColorFactor", [1, 1, 1, 1])
            if existing[3] < 1.0 and materials[mi].get("alphaMode") == "BLEND":
                continue  # the exporter already handled this one
            if key not in made:
                # One transparent copy per part, so other parts keep theirs.
                mat = json.loads(json.dumps(materials[mi]))
                pbr = mat.setdefault("pbrMetallicRoughness", {})
                bcf = pbr.get("baseColorFactor", [0.8, 0.8, 0.8, 1.0])
                pbr["baseColorFactor"] = [bcf[0], bcf[1], bcf[2], a]
                mat["alphaMode"] = "BLEND"
                mat["doubleSided"] = True
                mat["name"] = (mat.get("name") or "mat") + "_" + node["name"]
                materials.append(mat)
                made[key] = len(materials) - 1
                patched += 1
            prim["material"] = made[key]
    js["materials"] = materials

    body = json.dumps(js, separators=(",", ":")).encode("utf-8")
    body += b" " * ((4 - len(body) % 4) % 4)
    with open(path, "wb") as fh:
        total = 12 + 8 + len(body) + len(rest)
        fh.write(struct.pack("<III", magic, version, total))
        fh.write(struct.pack("<II", len(body), json_type))
        fh.write(body)
        fh.write(rest)
    return patched


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--tolerance", type=float, default=0.05, help="mesh tolerance, mm")
    p.add_argument("--angular", type=float, default=0.1, help="angular tolerance, rad")
    a = p.parse_args(argv)
    if not a.output.lower().endswith(".glb"):
        print("error: output must end in .glb", file=sys.stderr)
        return 2
    alpha = export(a.input, a.output, a.tolerance, a.angular)
    n = patch_alpha(a.output, alpha)
    print(f"wrote {a.output}: {os.path.getsize(a.output) // 1024} KB, "
          f"transparent parts: {', '.join(alpha) or 'none'} ({n} patched)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
