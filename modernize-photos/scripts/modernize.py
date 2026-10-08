#!/usr/bin/env python3
"""Make an old photo look as if it had been taken today with a modern iPhone:
sharp, clean, true-to-life colours, HDR, no grain / fading / cast / damage -
same people, same moment, same framing.

    modernize.py <image-or-folder>... [--backend auto|higgsfield|klein|qwen]
                 [--recursive] [--output DIR] [--keep-bw] [--extra "..."]
                 [--ref img]... [--resolution 2k] [--seed N] [--jobs 4]
                 [--cost] [--force] [--no-crop] [--no-sheets]

Backends:
  auto        (default) Higgsfield when the `higgsfield` CLI is installed,
              logged in and has credits for the whole run; otherwise the local
              ComfyUI model. A photo Higgsfield refuses (NSFW filter) or fails
              on is redone locally, and the run says which ones.
  higgsfield  Nano Banana Pro online (2 credits/photo at 2k, 4 at 4k); photos
              are uploaded. `--cost` prints the estimate and generates nothing.
  klein|qwen  local ComfyUI (FLUX.2 klein ~30 s, Qwen-Image-Edit ~100 s),
              free and offline, lower resolution (~1 MP, scaled back up).

Unlike restore-photos, the whole picture is the model's: nothing of the scan is
pasted back, so faces can change - every result must be looked at.

The scan is straightened and its white borders cut first (restore-photos'
crop.py). Black-and-white photos come back in colour unless --keep-bw.

Results: <out>/modernized/<name>.jpg (q95, EXIF copied from the scan),
<out>/work/<name>.raw.png, <name>.compare.jpg (scan | modern) and
<name>.json (backend, model, seed, prompt), and for a folder
<out>/sheets/qc_NN.jpg. <out> is ~/Downloads/photo-modernize/<folder name>
for a folder, ~/Downloads/photo-modernize for loose images
($PHOTO_MODERNIZE_OUT replaces the root, --output names any directory).
Sources are never written to; existing results are skipped unless --force.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

RESTORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "restore-photos", "scripts")
sys.path.insert(0, os.path.abspath(RESTORE))
import comfy_client  # noqa: E402
import crop as cropper  # noqa: E402
import higgsfield_restore as hf  # noqa: E402
from restore import collect, compare_sheet, save_jpeg, sheets  # noqa: E402

PROMPT = ("Turn this old photo into the same scene photographed today with a modern iPhone (latest Pro model, "
          "main 24 mm camera): tack-sharp focus and fine detail, clean low-noise image with no film grain, "
          "Smart HDR dynamic range with detail in the highlights and shadows, accurate white balance and "
          "natural, true-to-life colours, natural skin texture, crisp edges. Remove everything that makes it "
          "look old: fading, yellowing, colour casts, blur, softness, grain, dust, scratches, stains, creases, "
          "light leaks and the print's paper texture. Keep every person exactly as they are - same face, "
          "identity, age, expression, gaze, hair and pose - and keep their clothes, every object, the place, "
          "the lighting direction, the time of day and the framing. Only the photographic quality changes: "
          "do not modernise clothes, hairstyles, cars or objects, and do not add or remove anyone or anything. "
          "No text, no borders, no watermark.")
COLORIZE = (" The original is black and white: give it realistic, natural colours, as the iPhone would have "
            "captured them in that place and era.")
KEEP_BW = " Keep it black and white, as the iPhone's black-and-white photo style would render it."
REF = (" The other images are photos of the same people: use them only to know exactly what each person looks "
       "like, so every face stays that same person. Do not copy their pose, clothes or background.")


def is_grayscale(bgr):
    """True for B&W and sepia/toned monochrome: almost no hue variation."""
    lab = cv2.cvtColor(cv2.resize(bgr, (256, 256), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2LAB).astype(np.float32)
    a, b = lab[..., 1] - 128, lab[..., 2] - 128
    return float(np.std(a)) < 4 and float(np.std(b)) < 6


def out_root(folder, arg):
    if arg:
        return os.path.abspath(os.path.expanduser(arg))
    root = os.path.abspath(os.path.expanduser(os.environ.get("PHOTO_MODERNIZE_OUT") or "~/Downloads/photo-modernize"))
    return os.path.join(root, os.path.basename(os.path.abspath(folder))) if folder else root


def higgsfield_credits():
    """Available credits, or None when the CLI is missing / not logged in."""
    if not hf.HF:
        return None
    try:
        res = subprocess.run([hf.HF, "account", "status", "--json"], capture_output=True, text=True, timeout=60)
        return float(json.loads(res.stdout)["credits"]) if res.returncode == 0 else None
    except (ValueError, KeyError, TypeError, OSError, subprocess.TimeoutExpired):
        return None


def per_photo_cost(a):
    """Credits one generation costs (Higgsfield's own estimate; 2/4 as fallback)."""
    try:
        with tempfile.TemporaryDirectory(prefix="photo-modernize-") as tmp:
            probe = Path(tmp) / "probe.png"
            cv2.imwrite(str(probe), np.full((64, 64, 3), 128, np.uint8))
            out = hf.run_cli([hf.HF, "generate", "cost", a.model, "--prompt", "x", "--aspect_ratio", "1:1",
                              "--resolution", a.resolution, "--json", "--image-references", str(probe)])
        d = json.loads(out)
        for k in ("credits", "cost", "total", "price"):
            if isinstance(d, dict) and isinstance(d.get(k), (int, float)):
                return float(d[k])
    except (RuntimeError, ValueError, SystemExit):
        pass
    return 4.0 if a.resolution == "4k" else 2.0


def prompt_for(img, a):
    p = a.prompt or PROMPT
    if is_grayscale(img):
        p += KEEP_BW if a.keep_bw else COLORIZE
    return p + (REF if a.ref else "") + (" " + a.extra if a.extra else "")


def load_refs(a):
    refs = []
    for r in a.ref or []:
        ref = cv2.imread(os.path.expanduser(r), cv2.IMREAD_COLOR)
        if ref is None:
            raise SystemExit(f"cannot read reference {r}")
        refs.append(ref)
    return refs


def run_higgsfield(img, prompt, a, name):
    h, w = img.shape[:2]
    aspect, ratio = hf.closest_aspect(w, h)
    canvas, (px, py) = hf.pad_to_aspect(img, ratio)
    ch, cw = canvas.shape[:2]
    with tempfile.TemporaryDirectory(prefix="photo-modernize-") as tmp:
        up = Path(tmp) / f"{name}.png"
        s = min(1.0, hf.UPLOAD_MAX_SIDE / max(cw, ch))
        cv2.imwrite(str(up), canvas if s == 1 else cv2.resize(canvas, (round(cw * s), round(ch * s)),
                                                                interpolation=cv2.INTER_AREA))
        cmd = [hf.HF, "generate", "create", a.model, "--prompt", prompt, "--aspect_ratio", aspect,
               "--resolution", a.resolution, "--json", "--image-references", str(up)]
        for k, ref in enumerate(a.refs):
            rp = Path(tmp) / f"ref{k}.jpg"
            rs = min(1.0, 1536 / max(ref.shape[:2]))
            cv2.imwrite(str(rp), ref if rs == 1 else cv2.resize(ref, None, fx=rs, fy=rs, interpolation=cv2.INTER_AREA))
            cmd += ["--image-references", str(rp)]
        res = hf.run_cli(cmd + ["--wait", "--wait-timeout", "12m"])
        try:
            url = hf.find_url(json.loads(res))
        except ValueError:
            url = None
        if not url:
            raise RuntimeError(f"no image in the answer ({res[:200]})")
        raw = Path(tmp) / "raw"
        urllib.request.urlretrieve(url, raw)
        gen = cv2.imread(str(raw), cv2.IMREAD_COLOR)
    # the answer is usually bigger than the scan: keep its resolution, crop the padding off
    k = gen.shape[1] / cw
    gen = cv2.resize(gen, (round(cw * k), round(ch * k)), interpolation=cv2.INTER_AREA)
    return gen[round(py * k):round((py + h) * k), round(px * k):round((px + w) * k)]


def run_local(img, prompt, a):
    if not comfy_client.available(a.local):
        raise RuntimeError(f"local backend {a.local} unavailable (comfy_client.py --check)")
    with a.lock:   # one ComfyUI job at a time
        return comfy_client.edit(img, prompt, a.local, a.seed, a.mp, a.refs[:2] if a.local == "qwen" else a.refs)


def modernize_one(src, folder, a):
    name = Path(src).name if Path(src).stem in a.clashes else Path(src).stem
    out = Path(out_root(folder, a.output))
    dst = out / "modernized" / f"{name}.jpg"
    if dst.exists() and not a.force:
        return f"skip (exists) {name}"
    scan = cv2.imread(src, cv2.IMREAD_COLOR)
    if scan is None:
        return f"FAILED {name}: cannot read {src}"
    img = scan if a.no_crop else cropper.crop(scan)[0]
    prompt = prompt_for(img, a)
    t0, note = time.time(), ""
    backend, model = ("higgsfield", a.model) if a.use == "higgsfield" else (a.local, a.local)
    if a.use == "higgsfield":
        try:
            gen = run_higgsfield(img, prompt, a, name)
        except RuntimeError as e:
            if not a.fallback:
                return f"FAILED {name}: {e}"
            note = f" (Higgsfield failed: {str(e)[:120]} - done locally)"
            backend, model = a.local, a.local
            gen = run_local(img, prompt, a)
    else:
        gen = run_local(img, prompt, a)
    for d in ("modernized", "work"):
        (out / d).mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out / "work" / f"{name}.raw.png"), gen)
    save_jpeg(str(dst), gen, src)
    compare_sheet(img, gen, str(out / "work" / f"{name}.compare.jpg"))
    json.dump({"source": os.path.abspath(src), "backend": backend, "model": model,
               "seed": None if backend == "higgsfield" else a.seed, "prompt": prompt,
               "size": [gen.shape[1], gen.shape[0]], "seconds": round(time.time() - t0)},
              open(out / "work" / f"{name}.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    return f"-> {dst} [{backend}] ({time.time() - t0:.0f}s){note}"


def pick_backend(a, n):
    """Sets a.use / a.fallback; prints why."""
    if a.backend in ("klein", "qwen"):
        a.use, a.fallback = "local", False
        return
    credits = higgsfield_credits()
    if a.backend == "higgsfield":
        if credits is None:
            sys.exit("higgsfield: CLI missing or not logged in (npm i -g @higgsfield/cli; higgsfield auth login)")
        a.use, a.fallback = "higgsfield", False
        return
    if credits is None:
        print(f"auto: Higgsfield not available (CLI missing or not logged in) -> local {a.local}", flush=True)
        a.use, a.fallback = "local", False
        return
    need = per_photo_cost(a) * n
    if credits < need:
        print(f"auto: Higgsfield has {credits:g} credits, the run needs ~{need:g} -> local {a.local}", flush=True)
        a.use, a.fallback = "local", False
        return
    print(f"auto: Higgsfield {a.model} ({credits:g} credits, run ~{need:g}); refusals fall back to local {a.local}",
          flush=True)
    a.use, a.fallback = "higgsfield", True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--backend", default="auto", choices=("auto", "higgsfield", "klein", "qwen"))
    ap.add_argument("--local", choices=("klein", "qwen"),
                    help="local model for auto / fallback (default klein; --backend klein|qwen sets it)")
    ap.add_argument("--output", help="results folder (default ~/Downloads/photo-modernize[/<folder name>])")
    ap.add_argument("--recursive", action="store_true")
    ap.add_argument("--keep-bw", action="store_true", help="a black-and-white photo stays black and white")
    ap.add_argument("--prompt", help="replace the whole prompt")
    ap.add_argument("--extra", help="text appended to the prompt (what else to change or keep)")
    ap.add_argument("--ref", action="append", help="photo of the same people (identity reference, repeatable)")
    ap.add_argument("--model", default="nano_banana_pro", help="Higgsfield image model")
    ap.add_argument("--resolution", default="2k", choices=("1k", "2k", "4k"), help="Higgsfield resolution")
    ap.add_argument("--seed", type=int, default=42, help="local model seed (another value = another version)")
    ap.add_argument("--mp", type=float, default=1.0, help="local model working size in megapixels")
    ap.add_argument("--jobs", type=int, default=4, help="Higgsfield generations in flight at once")
    ap.add_argument("--no-crop", action="store_true", help="keep white borders / do not straighten")
    ap.add_argument("--no-sheets", action="store_true")
    ap.add_argument("--cost", action="store_true", help="print what the run would use and cost, generate nothing")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    a.local = a.local or (a.backend if a.backend in ("klein", "qwen") else "klein")
    files = collect(a.paths, a.recursive)
    if not files:
        sys.exit("nothing to do")
    stems = [Path(f).stem for f, _ in files]
    a.clashes = {x for x in stems if stems.count(x) > 1}
    todo = [(f, d) for f, d in files if a.force or not (
        Path(out_root(d, a.output)) / "modernized" /
        f"{Path(f).name if Path(f).stem in a.clashes else Path(f).stem}.jpg").exists()]
    if a.cost:
        if a.backend in ("klein", "qwen"):
            print(f"{len(todo)} photo(s) with local {a.local}: free")
        else:
            credits = higgsfield_credits()
            each = per_photo_cost(a) if credits is not None else None
            print(f"{len(todo)} photo(s); Higgsfield {a.model} {a.resolution}: "
                  + (f"{each:g} credits each, ~{each * len(todo):g} total, {credits:g} available"
                     if each is not None else "not available (CLI missing or not logged in)"))
        return
    if not todo:
        print("nothing to do (all done - --force redoes)")
        return
    pick_backend(a, len(todo))
    a.refs = load_refs(a)
    a.lock = threading.Lock()
    failed, local_after = 0, []

    def safe(item):
        try:
            return modernize_one(item[0], item[1], a)
        except (RuntimeError, OSError) as e:
            return f"FAILED {Path(item[0]).stem}: {e}"

    jobs = max(1, a.jobs) if a.use == "higgsfield" else 1
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for i, msg in enumerate(pool.map(safe, todo), 1):
            failed += msg.startswith("FAILED")
            if "done locally" in msg:
                local_after.append(msg)
            print(f"[{i}/{len(todo)}] {msg}", flush=True)
    if not a.no_sheets:
        for root in sorted({out_root(d, a.output) for _, d in todo if d}):
            sheets(root)
    if local_after:
        print(f"{len(local_after)} photo(s) were refused/failed on Higgsfield and done locally")
    if failed:
        print(f"{failed} failed - run again to retry them (finished ones are skipped)")


if __name__ == "__main__":
    main()
