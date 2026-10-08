#!/usr/bin/env python3
"""Restore scanned prints with Higgsfield (online, paid: credits) instead of
the local models - for a side-by-side comparison with restore.py's results.

    higgsfield_restore.py <image-or-folder>... --output DIR [--model nano_banana_pro]
                          [--resolution 2k] [--jobs 4] [--cost] [--force] [--no-crop]
                          [--strict] [--ref img]... [--extra "..."] [--cut t,r,b,l] [--angle deg]

Each scan is straightened and its white borders cut (crop.py, as in
restore.py), mirror-padded to the nearest aspect ratio the model accepts,
sent with a restoration prompt, and the answer is scaled back and cropped to
the scan's exact geometry. The whole picture is the model's: nothing is
composited back from the scan, so faces can change - review every result.

Results: DIR/restored/<name>.jpg (q95, EXIF copied; <name>.<ext>.jpg when
two scans differ only by extension), DIR/originals/<name>.*
(copy of the scan, so the review page can pair them), DIR/work/<name>.raw.png.
Existing results are skipped unless --force. --cost prints the credit
estimate and generates nothing. Needs the `higgsfield` CLI, logged in
(`higgsfield auth login`); photos are uploaded to Higgsfield.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crop as cropper  # noqa: E402
from restore import collect, save_jpeg  # noqa: E402

HF = shutil.which("higgsfield")
ASPECTS = ["1:1", "3:2", "2:3", "4:3", "3:4", "4:5", "5:4", "9:16", "16:9", "21:9"]
UPLOAD_MAX_SIDE = 3072
PROMPT = ("Restore this scanned old family photo print. Remove all damage: white blotches and flakes of lost "
          "emulsion, water stains, chemical burns, gold and orange metallic specks, rusty spots, scratches, "
          "creases, tears, light leaks and faded or colour-cast patches. Fill damaged or missing areas with "
          "what was naturally there, continuing the surrounding scene. Give it natural, even, balanced colours "
          "as a well-kept print from the same era, with its original lighting (a night photo stays a night "
          "photo). Keep every person exactly as they are - same faces, identity, age, expression, gaze, hair, "
          "pose and clothes - and keep every object, the background and the framing. Do not add or remove "
          "people or objects. No text, no borders.")


STRICT = ("Restore this scanned old family photo print by ONLY removing the damage - white blotches and flakes, "
          "stains, chemical burns, gold and orange metallic specks, rusty spots, scratches, tears, light leaks and "
          "colour casts - and correcting the colours to natural, balanced ones with the original lighting. This is "
          "a repair, not a new picture: do not redraw, move, restyle or replace anyone or anything. Every face, eye, "
          "mouth, smile, hairline and expression stays exactly as in this photo, pixel for pixel where it is "
          "undamaged; fill only the damaged or missing spots so they match their surroundings. Keep it sharp, keep "
          "the film grain, keep the framing. Do not add or remove people or objects. No text, no borders.")
REF = (" The other images are photos of the same people taken the same day, undamaged: use them only to know "
       "exactly what each person looks like, so every face you repair is that same person. Do not copy their "
       "pose, clothes or background.")


def closest_aspect(w, h):
    def ratio(a):
        x, y = a.split(":")
        return int(x) / int(y)
    name = min(ASPECTS, key=lambda a: abs(np.log((w / h) / ratio(a))))
    return name, ratio(name)


def pad_to_aspect(img, ratio):
    h, w = img.shape[:2]
    cw, ch = (w, round(w / ratio)) if w / h > ratio else (round(h * ratio), h)
    cw, ch = max(cw, w), max(ch, h)
    x, y = (cw - w) // 2, (ch - h) // 2
    return cv2.copyMakeBorder(img, y, ch - h - y, x, cw - w - x, cv2.BORDER_REFLECT_101), (x, y)


def find_url(o):
    if isinstance(o, str) and o.startswith("http") and \
            o.lower().split("?")[0].endswith((".png", ".jpg", ".jpeg", ".webp")):
        return o
    for v in (o.values() if isinstance(o, dict) else o if isinstance(o, list) else []):
        u = find_url(v)
        if u:
            return u
    return None


def run_cli(cmd):
    """The CLI's stdout; retries transient failures (503, flaky content filter)."""
    err = ""
    for attempt in range(4):
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        out = (res.stdout or "").strip()
        err = ((res.stderr or "").strip() + " " + out).strip()
        if res.returncode == 0 and out:
            return out
        if re.search(r"unauthori[sz]ed|not logged in|auth login|\b401\b", err, re.I):
            raise SystemExit(f"higgsfield: not logged in - run `higgsfield auth login` ({err[:200]})")
        if not any(t in err.lower() for t in ("503", "unavailable", "nsfw", "timeout", "temporarily", "429")):
            raise RuntimeError(err[:400])
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"retries exhausted: {err[:300]}")


def restore_one(src, a):
    # two scans with the same name and different extensions (x.jpg, x.png)
    # must not overwrite each other: those keep their extension in the name
    name = Path(src).name if Path(src).stem in a.clashes else Path(src).stem
    out = Path(a.output)
    dst = out / "restored" / f"{name}.jpg"
    if dst.exists() and not (a.force or a.cost):
        return f"skip (exists) {name}"
    scan = cv2.imread(src, cv2.IMREAD_COLOR)
    if scan is None:
        return f"cannot read {src}"
    if a.angle:
        scan = cropper.rotate(scan, a.angle)
    img = scan if a.no_crop else cropper.crop(scan)[0]
    if a.cut:
        t, r, b, l = (float(v) for v in a.cut.split(","))
        H, W = img.shape[:2]
        img = img[round(H * t):H - round(H * b), round(W * l):W - round(W * r)]
    h, w = img.shape[:2]
    aspect, ratio = closest_aspect(w, h)
    canvas, (px, py) = pad_to_aspect(img, ratio)
    ch, cw = canvas.shape[:2]
    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="photo-restore-hf-") as tmp:
        up = Path(tmp) / f"{name}.png"
        s = min(1.0, UPLOAD_MAX_SIDE / max(cw, ch))
        cv2.imwrite(str(up), canvas if s == 1 else cv2.resize(canvas, (round(cw * s), round(ch * s)),
                                                                interpolation=cv2.INTER_AREA))
        prompt = (a.prompt or (STRICT if a.strict else PROMPT)) + (REF if a.ref else "") + \
            (" " + a.extra if a.extra else "")
        cmd = [HF, "generate", "cost" if a.cost else "create", a.model, "--prompt", prompt,
               "--aspect_ratio", aspect, "--resolution", a.resolution, "--json", "--image-references", str(up)]
        for k, r in enumerate(a.ref or []):
            ref = cv2.imread(os.path.expanduser(r), cv2.IMREAD_COLOR)
            if ref is None:
                raise RuntimeError(f"cannot read reference {r}")
            rp = Path(tmp) / f"ref{k}.jpg"
            rs = min(1.0, 1536 / max(ref.shape[:2]))
            cv2.imwrite(str(rp), ref if rs == 1 else cv2.resize(ref, None, fx=rs, fy=rs, interpolation=cv2.INTER_AREA))
            cmd += ["--image-references", str(rp)]
        if a.cost:
            return f"{name}: {run_cli(cmd)}"
        res = run_cli(cmd + ["--wait", "--wait-timeout", "12m"])
        try:
            url = find_url(json.loads(res))
        except ValueError:
            url = None
        if not url:
            return f"FAILED {name}: no image in the answer ({res[:200]})"
        raw_file = Path(tmp) / "raw"
        urllib.request.urlretrieve(url, raw_file)
        gen = cv2.imread(str(raw_file), cv2.IMREAD_COLOR)
    gen = cv2.resize(gen, (cw, ch), interpolation=cv2.INTER_LANCZOS4 if gen.shape[1] < cw else cv2.INTER_AREA)
    gen = gen[py:py + h, px:px + w]
    for d in ("restored", "work", "originals"):
        (out / d).mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out / "work" / f"{name}.raw.png"), gen)
    save_jpeg(str(dst), gen, src)
    copy = out / "originals" / Path(src).name
    if not copy.exists():
        shutil.copy2(src, copy)
    return f"-> {dst} ({time.time() - t0:.0f}s)"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--output", required=True, help="results folder (restored/, originals/, work/)")
    ap.add_argument("--recursive", action="store_true")
    ap.add_argument("--model", default="nano_banana_pro", help="Higgsfield image model (default Nano Banana Pro)")
    ap.add_argument("--resolution", default="2k", choices=("1k", "2k", "4k"))
    ap.add_argument("--prompt", help="replace the restoration prompt")
    ap.add_argument("--jobs", type=int, default=4, help="generations in flight at once")
    ap.add_argument("--no-crop", action="store_true")
    ap.add_argument("--strict", action="store_true",
                    help="repair-only prompt: remove damage and fix colours, never redraw faces or the scene")
    ap.add_argument("--ref", action="append",
                    help="photo of the same people from the same day (identity reference, repeatable, up to 13)")
    ap.add_argument("--extra", help="text appended to the prompt (one image: what else to fix)")
    ap.add_argument("--cut", help="top,right,bottom,left fractions to cut after the automatic crop")
    ap.add_argument("--angle", type=float, default=0.0, help="rotate the scan by this many degrees first "
                    "(cv2 convention: positive = counter-clockwise)")
    ap.add_argument("--cost", action="store_true", help="print the credit estimate, generate nothing")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if not HF:
        sys.exit("higgsfield CLI not found (npm i -g @higgsfield/cli, then `higgsfield auth login`)")
    a.output = os.path.abspath(os.path.expanduser(a.output))
    files = [f for f, _ in collect(a.paths, a.recursive)]
    stems = [Path(f).stem for f in files]
    a.clashes = {x for x in stems if stems.count(x) > 1}
    if not files:
        sys.exit("nothing to do")
    failed = 0
    with ThreadPoolExecutor(max_workers=max(1, a.jobs)) as pool:
        for i, msg in enumerate(pool.map(lambda f: _safe(restore_one, f, a), files), 1):
            failed += msg.startswith("FAILED")
            print(f"[{i}/{len(files)}] {msg}", flush=True)
    if failed:
        print(f"{failed} failed - run again to retry them (finished ones are skipped)")


def _safe(fn, f, a):
    try:
        return fn(f, a)
    except RuntimeError as e:
        return f"FAILED {Path(f).stem}: {e}"


if __name__ == "__main__":
    main()
