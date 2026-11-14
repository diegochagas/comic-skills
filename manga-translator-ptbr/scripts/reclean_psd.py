#!/usr/bin/env python3
"""Add a freshly cleaned layer to PSDs that are already translated: the
text of the page as scanned is erased again with the current process (solid
fill on plain balloons, local Qwen-Image-Edit over art - inpaint_qwen.py) and
the result goes in as a new raster layer right above the layer edited so far.
Text layers, adjustment layers and every other layer stay as they are.

    reclean_psd.py <psd> [<psd> ...] [--output DIR] [--name "Qwen clean"]
                   [--source LAYER] [--above LAYER] [--box x,y,w,h ...]
                   [--spread LEFT_LAYER RIGHT_LAYER] [--fold auto|X]

  --source  layer holding the page as scanned (default "Original", else the
            bottom raster layer)
  --above   layer the new one is put on top of (default: the raster layer
            right above the source, else the source)
  --box     text the detector misses, in the source picture's pixels (the
            joined picture for --spread): the box is selected and the model
            erases the text in it, as a selection drawn by hand in GIMPhoto
  --spread  the page is a double-page spread kept as two page layers: they
            are joined first with no visible fold (split-scans'
            spread_seam.py) and the text is erased on the joined picture
  --fold    the page is a double-page spread in ONE scan (the source layer):
            it is cut at its fold (auto = found, or a column) and the two
            halves joined again the same way, with no visible fold

Output: <DIR>/<name>.psd (default ~/Downloads/<psd folder name> recleaned/,
COMIC_OUTPUT_DIR replaces ~/Downloads). The input PSDs are not modified.
Example: reclean_psd.py ~/Downloads/Portuguese/056-057.psd --spread "Layer 2" "Layer 1" --above "Layer 3 copy"
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "split-scans", "scripts"))
PSD_IO = os.path.join(REPO, "split-scans", "scripts", "psd_io.mjs")
ADD_LAYER = os.path.join(HERE, "add_raster_layer.mjs")
NODE = [os.environ.get("NODE", "node"), "--max-old-space-size=4096"]


def default_out(psd_path):
    root = os.environ.get("COMIC_OUTPUT_DIR") or os.path.expanduser("~/Downloads")
    return os.path.join(root, os.path.basename(os.path.dirname(os.path.abspath(psd_path))) + " recleaned")


def dump_layers(psd_path, work):
    subprocess.run(NODE + [PSD_IO, "dump", psd_path, work], check=True, capture_output=True)
    return json.load(open(os.path.join(work, "layers.json")))["layers"]


def layer_named(layers, name):
    found = [la for la in layers if la["name"] == name and la["kind"] == "raster"]
    if not found:
        rasters = ", ".join(la["name"] for la in layers if la["kind"] == "raster")
        sys.exit(f'no raster layer named "{name}" (raster layers: {rasters})')
    return found[0]


def read_bgr(layer):
    img = cv2.imread(layer["png"], cv2.IMREAD_UNCHANGED)
    return np.ascontiguousarray(img[..., :3])


ONE_SCAN_MAX_DY = 30  # px the two halves of one scan may be out of line vertically


def _join(left, right, max_dy=None):
    import spread_seam
    pic, info = spread_seam.join_spread(left, right, max_dy=max_dy, progress=lambda i, n, f: print(
        f"  fold tile {i}/{n}: {f:.2f} of the fold left", flush=True))
    print(f"  spread joined ({info['align']}): turn {info['theta']} deg, dy {info['dy']}, gap {info['gap']}, "
          f"bands {info['bands']}, fold redrawn by {info['method']}")
    if info["gap"]:
        print(f"  NOTE: {info['gap']} px of drawing were missing at the fold: the right page and its text "
              f"boxes sit {info['gap']} px further right than in the file", flush=True)
    return pic, info


def source_picture(layers, opts):
    """(picture BGR, left, top, source layer) of the page as scanned."""
    if opts.spread:
        left, right = (layer_named(layers, n) for n in opts.spread)
        pic, info = _join(read_bgr(left), read_bgr(right))
        return pic, left["left"], left["top"] + info["top"], left
    rasters = [la for la in layers if la["kind"] == "raster"]
    src = layer_named(layers, opts.source) if opts.source else next(
        (la for la in rasters if la["name"] == "Original"), rasters[0])
    pic = read_bgr(src)
    if not opts.fold:
        return pic, src["left"], src["top"], src
    import spread_align
    x = spread_align.fold_column(pic) if opts.fold == "auto" else int(opts.fold)
    print(f"  fold at column {x}")
    pic, info = _join(pic[:, :x], pic[:, x:], ONE_SCAN_MAX_DY)
    return pic, src["left"], src["top"] + info["top"], src


def erase_text(pic, boxes, work):
    """The picture with its text erased (boxes first, then what is detected)."""
    import detect_text
    import inpaint_qwen
    if boxes and inpaint_qwen.available():
        pic = inpaint_qwen.erase_boxes(pic, boxes)
    elif boxes:
        print("  --box needs the local Qwen model (ComfyUI not available): boxes skipped", file=sys.stderr)
    path = os.path.join(work, "page.png")
    cv2.imwrite(path, pic)
    detect_text.detect_page(detect_text.make_session(), path, os.path.join(work, "detect"))
    return os.path.join(work, "detect", "page_cleaned.png")


def above_layer(layers, src, opts):
    if opts.above:
        return opts.above
    top_level = [la for la in layers if la["kind"] == "raster"]
    i = top_level.index(src)
    return top_level[i + 1]["name"] if i + 1 < len(top_level) else src["name"]


def reclean(psd_path, opts):
    out_dir = opts.output or default_out(psd_path)
    out = os.path.join(out_dir, os.path.basename(psd_path))
    with tempfile.TemporaryDirectory(prefix="reclean_") as work:
        layers = dump_layers(psd_path, work)
        pic, left, top, src = source_picture(layers, opts)
        cleaned = erase_text(pic, opts.box, work)
        subprocess.run(NODE + [ADD_LAYER, psd_path, cleaned, out, "--name", opts.name,
                               "--above", above_layer(layers, src, opts),
                               "--left", str(left), "--top", str(top)], check=True)
    return out


def parse_box(text):
    parts = [int(v) for v in text.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(f"--box {text!r}: expected x,y,w,h")
    return parts


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("psds", nargs="+")
    ap.add_argument("--output")
    ap.add_argument("--name", default="Qwen clean")
    ap.add_argument("--source")
    ap.add_argument("--above")
    ap.add_argument("--box", type=parse_box, action="append", default=[])
    ap.add_argument("--spread", nargs=2, metavar=("LEFT_LAYER", "RIGHT_LAYER"))
    ap.add_argument("--fold", metavar="auto|X")
    opts = ap.parse_args()
    for psd_path in opts.psds:
        print(f"{os.path.basename(psd_path)}:", flush=True)
        print(f"  -> {reclean(psd_path, opts)}")


if __name__ == "__main__":
    main()
