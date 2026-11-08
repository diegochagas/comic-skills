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
from glob import escape as glob_escape
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np
from psd_tools import PSDImage

from previews import DIVERGENT, make_previews

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


ARCHIVE_SUFFIXES = {".rar", ".zip", ".7z"}


def extract_archive(archive, out_root):
    """Extract `archive` into a fresh temp dir under out_root and return
    (temp_dir, psd_dir), descending through single-folder wrappers."""
    # 7z fails on some RAR5 methods; unrar handles every .rar
    if archive.suffix.lower() == ".rar":
        tool, args_for = "unrar", lambda t: ["unrar", "x", "-y", "-idq", str(archive), f"{t}/"]
    else:
        tool, args_for = "7z", lambda t: ["7z", "x", "-y", f"-o{t}", str(archive)]
    if shutil.which(tool) is None:
        raise RuntimeError(f"{tool} is needed to read {archive.suffix} archives")
    out_root.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix="psd-sync-", dir=out_root))
    done = subprocess.run(args_for(temp), capture_output=True, text=True)
    if done.returncode != 0:
        shutil.rmtree(temp, ignore_errors=True)
        raise RuntimeError(f"could not extract {archive}: {done.stderr.strip() or done.stdout[-300:]}")
    psd_dir = temp
    while not any(p.suffix.lower() == ".psd" for p in psd_dir.iterdir()):
        subdirs = [p for p in psd_dir.iterdir() if p.is_dir()]
        if len(subdirs) != 1:
            break
        psd_dir = subdirs[0]
    return temp, psd_dir


def archive_volumes(archive):
    """The archive plus its sibling volumes: `X.part1.rar` -> X.part1.rar, X.part2.rar..."""
    stem = archive.name[:-len(archive.suffix)]
    if not stem.lower().rsplit(".", 1)[-1].startswith("part"):
        return [archive]
    base = stem.rsplit(".", 1)[0]
    siblings = sorted(archive.parent.glob(f"{glob_escape(base)}.part*{archive.suffix}"))
    return siblings or [archive]


def finish_archive(temp, archive, consumed):
    """Drop the temp extraction; on a full apply also trash the archive itself
    (every volume of a multi-part .rar, or the leftover volumes stay orphaned)."""
    if temp is not None:
        shutil.rmtree(temp, ignore_errors=True)
    if consumed:
        for volume in archive_volumes(archive):
            used = trash(volume)
            print(f"{volume} trashed ({used}) - archive fully processed")


def trash_leftovers(src_dir):
    """Trash every .psd still in src_dir (archive mode: leftovers are not kept)."""
    trashed = []
    for p in sorted(src_dir.iterdir()):
        if p.is_file() and p.suffix.lower() == ".psd":
            trash(p)
            trashed.append(p.name)
    return trashed


def trash_source_if_empty(src_dir):
    """If src_dir has nothing left in it, trash it - or its parent, when src_dir
    is that parent's only entry (the common "Name/Name/" download wrapper)."""
    if any(src_dir.iterdir()):
        return None, None
    parent = src_dir.resolve().parent
    siblings = list(parent.iterdir())
    target = parent if len(siblings) == 1 and siblings[0].resolve() == src_dir.resolve() else src_dir
    return target, trash(target)


# ----------------------------------------------------------------------- main
MOVE = {"identical", "same_framing"}


def forced(row, args):
    """True when --include-divergent asks to replace this cut/different page."""
    if args.include_divergent is None or row["status"] not in DIVERGENT:
        return False
    names = {n if n.lower().endswith(".psd") else n + ".psd" for n in args.include_divergent}
    return not names or row["file"] in names


def write_previews(src_dir, dst_dir, rows, out_root, archive):
    """Dry run: side-by-side JPGs of every divergent page so Diego can check by eye."""
    label = archive.name.split(".part")[0].removesuffix(archive.suffix) if archive else src_dir.name
    made = make_previews(src_dir, dst_dir, rows, out_root / f"{label} comparison")
    if made:
        print(f"\npreview comparison ({len(made)} page(s), folder1 left / folder2 right): {made[0].parent}")


def report_empty_source(src_dir):
    """Trash src_dir (or its wrapper) when nothing is left in it; returns what was trashed."""
    target, used = trash_source_if_empty(src_dir)
    if target is None:
        print(f"\nfolder1 not trashed - still has: {', '.join(p.name for p in sorted(src_dir.iterdir()))}")
        return None
    print(f"\n{target} trashed ({used}) - folder1 was fully synced")
    return str(target)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder1", help="new PSDs (source; files move out of here) - a folder, or a .rar/.zip/.7z of PSDs")
    ap.add_argument("folder2", help="PSDs to be replaced (destination)")
    ap.add_argument("--apply", action="store_true",
                    help="actually trash folder2's copy and move folder1's file in")
    ap.add_argument("--only", nargs="+", metavar="NAME",
                    help="limit to these file names (089.psd or 089)")
    ap.add_argument("--include-divergent", nargs="*", metavar="NAME",
                    help="with --apply, also replace pages kept as cut/different: the named ones, "
                         "or all of them when no name is given (only after Diego saw the previews)")
    ap.add_argument("--layer", metavar="NAME",
                    help="compare this layer instead of the auto-detected art layer")
    ap.add_argument("--json", nargs="?", const="", metavar="PATH",
                    help="write the report as JSON (default: <out root>/<folder1 name>-psd-sync.json)")
    args = ap.parse_args()

    src_dir, dst_dir = Path(args.folder1).expanduser(), Path(args.folder2).expanduser()
    out_root = Path(os.environ.get("COMIC_OUTPUT_DIR") or Path.home() / "Downloads")
    archive, temp = None, None
    if src_dir.is_file() and src_dir.suffix.lower() in ARCHIVE_SUFFIXES:
        archive = src_dir.resolve()
        temp, src_dir = extract_archive(archive, out_root)
        print(f"extracted {archive.name} -> {src_dir}\n")
    if not dst_dir.is_dir() or not src_dir.is_dir():
        finish_archive(temp, archive, False)
        sys.exit(f"not a folder: {dst_dir if not dst_dir.is_dir() else src_dir}")
    if src_dir.resolve() == dst_dir.resolve():
        finish_archive(temp, archive, False)
        sys.exit("folder1 and folder2 are the same folder")

    wanted = None
    if args.only:
        wanted = {n if n.lower().endswith(".psd") else n + ".psd" for n in args.only}

    names = sorted(p.name for p in src_dir.iterdir()
                   if p.is_file() and p.suffix.lower() == ".psd"
                   and (wanted is None or p.name in wanted))
    if not names:
        finish_archive(temp, archive, False)
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
        row["action"] = "replace" if row["status"] in MOVE or forced(row, args) else "keep"
        rows.append(row)
        print(f"{name:<24} {row['status']:<14} {row['action']:<8} {row.get('detail','')}")

    to_move = [r for r in rows if r["action"] == "replace"]
    print(f"\n{len(rows)} file(s): {len(to_move)} to replace, {len(rows) - len(to_move)} kept as they are")
    if not args.apply:
        write_previews(src_dir, dst_dir, rows, out_root, archive)

    source_trashed = None

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

        if not args.only:
            for name in trash_leftovers(src_dir):
                print(f"{name:<24} leftover -> trashed")
            if archive is not None:
                source_trashed = str(archive)
            else:
                source_trashed = report_empty_source(src_dir)
    elif to_move:
        note = (f"; the {len(rows) - len(to_move)} kept file(s) would be trashed with the rest of "
                f"folder1 (or the archive) unless --include-divergent names them")
        if args.only:
            note = ""
        print(f"dry run - nothing was moved; re-run with --apply to replace those files{note}")

    if args.json is not None:
        out = Path(args.json).expanduser() if args.json else out_root / f"{(archive.stem if archive else src_dir.name)}-psd-sync.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "folder1": str(src_dir), "folder2": str(dst_dir),
            "applied": bool(args.apply),
            "source_trashed": source_trashed,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "files": rows,
        }, indent=2, ensure_ascii=False))
        print(f"report: {out}")

    if archive is not None:
        finish_archive(temp, archive, bool(args.apply and not args.only))


if __name__ == "__main__":
    main()
