#!/usr/bin/env python3
"""Cut a coloured STEP assembly in half for a section view.

Usage:
    python tools/section_step.py INPUT.step OUTPUT.step [--keep +y|-y|+x|-x]

Loads the assembly with CadQuery (names and colours survive), removes
the half of every part on the far side of a plane through the axis, and
writes a new STEP assembly with the same part names and colours. The
default removes everything with y < 0, so the cut face lies on the XZ
plane and faces a viewer standing on the -Y side.

Parts that are empty after the cut are dropped. Needs cadquery.
"""

import argparse
import os
import sys

import cadquery as cq

BIG = 5000.0  # mm, bigger than any instrument we will ever model


def half_space(keep: str) -> cq.Workplane:
    """A huge box covering the half of space that is to be REMOVED."""
    axis, sign = keep[1], keep[0]
    shift = -BIG / 2 if sign == "+" else BIG / 2
    box = cq.Workplane("XY").box(BIG, BIG, BIG)
    return box.translate({"x": (shift, 0, 0), "y": (0, shift, 0), "z": (0, 0, shift)}[axis])


def section(src: str, dst: str, keep: str) -> int:
    assy = cq.Assembly.load(src)
    cutter = half_space(keep).val()
    out = cq.Assembly(name=assy.name + "-SECTION")
    kept = 0

    def visit(node):
        nonlocal kept
        for child in node.children:
            if child.obj is not None and hasattr(child.obj, "cut"):
                shape = child.obj.cut(cutter)
                if shape.Solids():
                    out.add(shape, name=child.name, color=child.color)
                    kept += 1
            visit(child)

    visit(assy)
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    out.export(dst)
    return kept


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--keep", default="+y", choices=["+y", "-y", "+x", "-x"],
                   help="which half of the model to keep (default +y)")
    a = p.parse_args(argv)
    if os.path.abspath(a.input) == os.path.abspath(a.output):
        print("error: output must differ from input", file=sys.stderr)
        return 2
    n = section(a.input, a.output, a.keep)
    print(f"wrote {a.output}: {n} parts kept, half with {a.keep[1]} {'<' if a.keep[0] == '+' else '>'} 0 removed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
