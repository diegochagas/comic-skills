#!/usr/bin/env python3
"""Compare same-named PSDs in two folders by their art layer and, when the art
was not cropped, replace the copy in folder 2 with the one from folder 1.

    venv/bin/python psd-sync/scripts/sync_psds.py <folder1> <folder2> [--apply]

Dry run by default: it only prints what it would do. With --apply, for every
pair whose art layer has the same framing, the folder-2 file goes to the trash
(gio trash / trash-put, never rm) and the folder-1 file is moved in its place.
Pairs whose art was cut (sides, top or bottom) - or that don't match at all -
are left untouched in both folders.

The "art layer" is the bottom-most pixel layer that covers the canvas (the
scan under the lettering); --layer picks another one by name.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from psd_tools import PSDImage

# best normalized cross-correlation accepted as "this is the same artwork"
MATCH_MIN = 0.90
# margins below this many pixels (in the larger image's own scale) are rounding
MARGIN_MIN_PX = 4
# longest side used for the alignment search (coarse pass, then fine pass)
COARSE_MAX = 320
WORK_MAX = 800
# smallest crop looked for: art keeping at least this much of a side
MIN_CROP_FACTOR = 0.5
COARSE_STEPS = 13
REFINE_ROUNDS = 2
# scores within this of the best count as a tie (the least-cropped fit wins)
SCORE_SLACK = 0.02
# mean abs difference (0..1) under which two same-sized layers are identical
IDENTICAL_TOL = 0.002


# ---------------------------------------------------------------- PSD reading
def iter_layers(node):
    for layer in node:
        if layer.is_group():
            yield from iter_layers(layer)
        else:
            yield layer


def art_layer(psd, name=None):
    """Bottom-most pixel layer covering the canvas, or the named one."""
    pixels = [l for l in iter_layers(psd) if l.kind == "pixel" and l.width and l.height]
    if name is not None:
        for layer in pixels:
            if layer.name == name:
                return layer
        raise ValueError(f"no pixel layer named {name!r}")
    cw, ch = psd.width, psd.height
    for layer in pixels:  # psd-tools iterates bottom to top
        if layer.width >= cw * 0.9 and layer.height >= ch * 0.9:
            return layer
    if pixels:
        return max(pixels, key=lambda l: l.width * l.height)
    return None


def to_gray(array):
    array = np.asarray(array, dtype=np.float32)
    if array.ndim == 3:
        if array.shape[2] >= 3:
            array = array[:, :, :3] @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
        else:
            array = array[:, :, 0]
    return array


def read_art(path, name=None):
    psd = PSDImage.open(path)
    layer = art_layer(psd, name)
    if layer is None:
        gray = to_gray(np.asarray(psd.composite(), dtype=np.float32) / 255.0)
        return gray, "<composite>"
    return to_gray(layer.numpy()), layer.name


# ------------------------------------------------------------------ comparing
def shrink(gray, scale):
    """Resize by `scale`; both images of a pair share one scale so that an
    uncropped overlay still lines up 1:1 at the working resolution."""
    if scale >= 1.0:
        return gray
    return cv2.resize(gray, (max(1, int(round(gray.shape[1] * scale))),
                             max(1, int(round(gray.shape[0] * scale)))),
                      interpolation=cv2.INTER_AREA)


def place(template, target, factors):
    """Best score/offset for `template`, resized by each factor, inside `target`.

    A factor that makes the template fill `target` means nothing was cropped;
    a smaller one means the art was cropped (and possibly resized afterwards).
    When several factors score the same, the largest one wins - a small patch
    of art can correlate well anywhere, the full-size overlay is the honest fit.
    """
    hits = []
    th, tw = template.shape
    bh, bw = target.shape
    for factor in sorted({round(f, 5) for f in factors}):
        w, h = int(round(tw * factor)), int(round(th * factor))
        if w < 16 or h < 16 or w > bw or h > bh:
            continue
        resized = cv2.resize(template, (w, h), interpolation=cv2.INTER_AREA)
        _, score, _, loc = cv2.minMaxLoc(cv2.matchTemplate(target, resized, cv2.TM_CCOEFF_NORMED))
        hits.append({"score": float(score), "factor": factor,
                     "x": loc[0], "y": loc[1], "w": w, "h": h})
    if not hits:
        return None
    top = max(h["score"] for h in hits)
    return max((h for h in hits if h["score"] >= top - SCORE_SLACK), key=lambda h: h["factor"])


def fit_factor(template_shape, target_shape):
    """Scale at which `template` fills `target` - the no-crop case."""
    return min(target_shape[1] / template_shape[1], target_shape[0] / template_shape[0])


def factor_range(template_shape, target_shape, steps):
    """Scales to try: from `fit` (nothing cropped) down to MIN_CROP_FACTOR of
    it (half of a side cropped away), plus both exact side ratios."""
    fit = fit_factor(template_shape, target_shape)
    return list(np.linspace(fit * MIN_CROP_FACTOR, fit, steps)) + [
        target_shape[1] / template_shape[1], target_shape[0] / template_shape[0]]


def align(gray_a, gray_b):
    """Find how folder-1 art (a) and folder-2 art (b) overlap.

    Coarse pass over a range of scales on small copies, then a fine pass at the
    working resolution that refines the scale twice - the correlation peak over
    scale is sharp, a 1% error already costs a couple of pixels across a page.
    Returns the winning direction (which image is contained in the other), its
    placement and the scale the margins are measured in.
    """
    longest = max(*gray_a.shape, *gray_b.shape)
    coarse_scale = min(1.0, COARSE_MAX / longest)
    coarse = [shrink(gray_a, coarse_scale), shrink(gray_b, coarse_scale)]

    guesses = []
    for direction, (template, target) in (("folder1", (0, 1)), ("folder2", (1, 0))):
        t, g = coarse[template], coarse[target]
        found = place(t, g, factor_range(t.shape, g.shape, COARSE_STEPS))
        if found:
            guesses.append((direction, found))
    if not guesses:
        return None

    direction, guess = max(guesses, key=lambda x: x[1]["score"])
    work_scale = min(1.0, WORK_MAX / longest)
    # a touch of blur so a sub-pixel misalignment doesn't sink the correlation
    small_a, small_b = (cv2.GaussianBlur(shrink(g, work_scale), (0, 0), 1.0)
                        for g in (gray_a, gray_b))
    template, target = (small_a, small_b) if direction == "folder1" else (small_b, small_a)
    fit = fit_factor(template.shape, target.shape)

    step = fit * 0.01
    # 1.0 = both files at the same resolution (cropped but never resized),
    # fit = the template fills the target (nothing cropped): always worth trying
    candidates = {guess["factor"], 1.0, fit} | {guess["factor"] + d * step for d in range(-6, 7)}
    found = place(template, target, [f for f in candidates if f <= fit]) or guess
    for _ in range(REFINE_ROUNDS):
        if found["score"] >= 0.995:
            break
        step /= 4
        better = place(template, target,
                       [found["factor"] + d * step for d in range(-4, 5) if found["factor"] + d * step <= fit])
        if better and better["score"] > found["score"]:
            found = better
    return direction, found, target.shape, work_scale


def margins(placement, big_shape, work_scale):
    """Pixels of the containing image (original scale) left outside the placed art."""
    bh, bw = big_shape
    sides = {"left": placement["x"], "top": placement["y"],
             "right": bw - (placement["x"] + placement["w"]),
             "bottom": bh - (placement["y"] + placement["h"])}
    return {side: max(0, int(round(v / work_scale))) for side, v in sides.items()}


def compare(gray_a, gray_b):
    """Classify folder-1 art (a) against folder-2 art (b)."""
    if gray_a.shape == gray_b.shape:
        diff = float(np.abs(gray_a - gray_b).mean())
        if diff <= IDENTICAL_TOL:
            return {"status": "identical", "score": 1.0, "detail": "same size, same pixels"}

    aligned = align(gray_a, gray_b)
    if aligned is None:
        return {"status": "different", "score": 0.0, "detail": "images cannot be aligned"}
    direction, placement, big_shape, work_scale = aligned
    score = placement["score"]
    if score < MATCH_MIN:
        return {"status": "different", "score": score,
                "detail": f"best match only {score:.2f} - not the same artwork"}

    cut = {side: px for side, px in margins(placement, big_shape, work_scale).items()
           if px >= MARGIN_MIN_PX}
    if cut:
        sides = ", ".join(f"{side} {px}px" for side, px in cut.items())
        return {"status": "cut", "score": score, "cut_in": direction, "cut_sides": cut,
                "detail": f"{direction} art is cut: missing {sides}"}

    if gray_a.shape == gray_b.shape:
        return {"status": "same_framing", "score": score, "detail": "same framing, pixels edited"}
    return {"status": "same_framing", "score": score,
            "detail": f"same framing, resized {gray_a.shape[1]}x{gray_a.shape[0]} "
                      f"vs {gray_b.shape[1]}x{gray_b.shape[0]}"}


# -------------------------------------------------------------------- actions
def trash(path):
    for cmd in (["gio", "trash", "--", str(path)], ["trash-put", "--", str(path)]):
        if shutil.which(cmd[0]) is None:
            continue
        done = subprocess.run(cmd, capture_output=True, text=True)
        if done.returncode == 0:
            return cmd[0]
        last = done.stderr.strip()
        break
    else:
        last = "no gio or trash-put found"
    raise RuntimeError(f"could not trash {path}: {last}")


def replace(src, dst):
    size = src.stat().st_size
    used = trash(dst)
    shutil.move(str(src), str(dst))
    if dst.stat().st_size != size:
        raise RuntimeError(f"{dst} is {dst.stat().st_size} bytes after the move, expected {size}")
    return used


# ----------------------------------------------------------------------- main
MOVE = {"identical", "same_framing"}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder1", help="new PSDs (source; files move out of here)")
    ap.add_argument("folder2", help="PSDs to be replaced (destination)")
    ap.add_argument("--apply", action="store_true",
                    help="actually trash folder2's copy and move folder1's file in")
    ap.add_argument("--only", nargs="+", metavar="NAME",
                    help="limit to these file names (089.psd or 089)")
    ap.add_argument("--layer", metavar="NAME",
                    help="compare this layer instead of the auto-detected art layer")
    ap.add_argument("--json", nargs="?", const="", metavar="PATH",
                    help="write the report as JSON (default: <out root>/<folder1 name>-psd-sync.json)")
    args = ap.parse_args()

    src_dir, dst_dir = Path(args.folder1).expanduser(), Path(args.folder2).expanduser()
    for d in (src_dir, dst_dir):
        if not d.is_dir():
            sys.exit(f"not a folder: {d}")
    if src_dir.resolve() == dst_dir.resolve():
        sys.exit("folder1 and folder2 are the same folder")

    wanted = None
    if args.only:
        wanted = {n if n.lower().endswith(".psd") else n + ".psd" for n in args.only}

    names = sorted(p.name for p in src_dir.iterdir()
                   if p.is_file() and p.suffix.lower() == ".psd"
                   and (wanted is None or p.name in wanted))
    if not names:
        sys.exit(f"no .psd files in {src_dir}")

    rows = []
    for name in names:
        src, dst = src_dir / name, dst_dir / name
        row = {"file": name}
        if not dst.exists():
            row.update(status="no_counterpart", detail=f"{name} is not in {dst_dir}")
        else:
            try:
                gray_a, layer_a = read_art(src, args.layer)
                gray_b, layer_b = read_art(dst, args.layer)
                row.update(compare(gray_a, gray_b), layer1=layer_a, layer2=layer_b)
            except Exception as exc:  # unreadable PSD, missing layer...
                row.update(status="error", detail=f"{type(exc).__name__}: {exc}")
        row["action"] = "replace" if row["status"] in MOVE else "keep"
        rows.append(row)
        print(f"{name:<24} {row['status']:<14} {row['action']:<8} {row.get('detail','')}")

    to_move = [r for r in rows if r["action"] == "replace"]
    print(f"\n{len(rows)} file(s): {len(to_move)} to replace, {len(rows) - len(to_move)} kept as they are")

    if args.apply:
        print()
        for row in to_move:
            src, dst = src_dir / row["file"], dst_dir / row["file"]
            try:
                used = replace(src, dst)
                row["applied"] = f"old copy trashed with {used}, new file moved in"
                print(f"{row['file']:<24} replaced ({used})")
            except Exception as exc:
                row["applied"] = f"FAILED: {exc}"
                row["action"] = "keep"
                print(f"{row['file']:<24} FAILED: {exc}")
    elif to_move:
        print("dry run - nothing was moved; re-run with --apply to replace those files")

    if args.json is not None:
        out = Path(args.json).expanduser() if args.json else Path(
            os.environ.get("COMIC_OUTPUT_DIR") or Path.home() / "Downloads"
        ) / f"{src_dir.name}-psd-sync.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "folder1": str(src_dir), "folder2": str(dst_dir),
            "applied": bool(args.apply),
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "files": rows,
        }, indent=2, ensure_ascii=False))
        print(f"report: {out}")


if __name__ == "__main__":
    main()
