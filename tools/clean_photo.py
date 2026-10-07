#!/usr/bin/env python3
"""Strip every byte of metadata from a photo and write a web-sized JPEG.

Usage:
    python tools/clean_photo.py INPUT OUTPUT [--width 1600] [--quality 85]

How it works: the input is decoded to raw pixels, rotated according to
its EXIF orientation flag so it still looks the right way up, resized if
wider than --width, converted to RGB, and encoded into a brand-new JPEG.
Nothing from the source container is carried across: no EXIF, no GPS,
no camera serial, no embedded thumbnail, no ICC profile, no XMP, no
IPTC. Only pixels survive.

The script refuses to overwrite its own input and refuses to write into
intake/ (that folder is for unreviewed originals only).
"""

import argparse
import os
import sys

from PIL import Image, ImageOps

DEFAULT_WIDTH = 1600
DEFAULT_QUALITY = 85


def clean(src: str, dst: str, max_width: int, quality: int) -> tuple[int, int]:
    """Re-encode src into dst as a metadata-free JPEG. Returns final (w, h)."""
    with Image.open(src) as im:
        # Apply the orientation tag, then forget it along with everything else.
        im = ImageOps.exif_transpose(im)
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        if im.width > max_width:
            new_h = round(im.height * max_width / im.width)
            im = im.resize((max_width, new_h), Image.Resampling.LANCZOS)

        # Copy pixels into a fresh image so no .info dict (EXIF, ICC, XMP,
        # comments) can ride along into the encoder.
        fresh = Image.new(im.mode, im.size)
        fresh.putdata(list(im.getdata()))

    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    fresh.save(
        dst,
        format="JPEG",
        quality=quality,
        optimize=True,
        progressive=True,
        exif=b"",
        icc_profile=None,
    )
    return fresh.size


def verify_no_metadata(path: str) -> list[str]:
    """Return a list of metadata keys still present in the written file."""
    with Image.open(path) as im:
        leftovers = []
        if im.getexif():
            leftovers.append("exif")
        for key in ("icc_profile", "xmp", "photoshop", "comment", "exif"):
            if im.info.get(key):
                leftovers.append(key)
        return leftovers


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("input", help="original photo (any format Pillow can read)")
    p.add_argument("output", help="destination .jpg, usually under img/ or parts/")
    p.add_argument("--width", type=int, default=DEFAULT_WIDTH,
                   help=f"maximum width in pixels (default {DEFAULT_WIDTH})")
    p.add_argument("--quality", type=int, default=DEFAULT_QUALITY,
                   help=f"JPEG quality 1-95 (default {DEFAULT_QUALITY})")
    a = p.parse_args(argv)

    src = os.path.abspath(a.input)
    dst = os.path.abspath(a.output)
    if not os.path.isfile(src):
        print(f"error: input not found: {a.input}", file=sys.stderr)
        return 2
    if src == dst:
        print("error: output must not be the same file as input", file=sys.stderr)
        return 2
    if "intake" in os.path.normpath(dst).split(os.sep):
        print("error: refusing to write into intake/; that folder is for "
              "unreviewed originals only", file=sys.stderr)
        return 2
    if not dst.lower().endswith((".jpg", ".jpeg")):
        print("error: output must end in .jpg", file=sys.stderr)
        return 2

    w, h = clean(src, dst, a.width, a.quality)
    leftovers = verify_no_metadata(dst)
    if leftovers:
        print(f"error: metadata survived in {a.output}: {leftovers}", file=sys.stderr)
        os.remove(dst)
        return 1

    print(f"wrote {a.output}  {w}x{h}  {os.path.getsize(dst) // 1024} KB, no metadata")
    print("now LOOK at the image and describe the background to the owner "
          "before committing it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
