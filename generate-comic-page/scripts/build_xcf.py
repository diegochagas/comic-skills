#!/usr/bin/env python3
"""Build the GIMP file of a page: the textless art (or a
flat paper colour) at the bottom and every piece of text as a NATIVE GIMP
text layer on top — Diego can retype, restyle and move each one in GIMP.
Used for EVERY page: story pages (one CCWildWords text box per empty balloon,
layout drafted by make_layout.py), covers and editorials.
Drives headless GIMPhoto (flatpak GIMP 3) with gimp_layout_job.py.

Usage:
  build_xcf.py <layout.json>              build the XCF (+ preview JPG)
  build_xcf.py --list-fonts [filter]      fonts GIMP can see, e.g. --list-fonts wild

layout.json (paths relative to the layout file; see SKILL.md for the workflow):
  {
    "art": "../gen/page_01_try2.png",          the approved textless art, OR
    "background": {"color": "#efe6cf", "size": [1365, 2048]},   a flat page
    "out": "Megaman17_cover.xcf",
    "preview": "Megaman17_cover_preview.jpg",   flattened render to look at
    "units": "fraction",                         boxes as 0..1 of the page (default) or "px"
    "uniform_size": false,                       true = comic lettering: every auto-sized text gets the
                                                 SAME size (the 25th percentile of the per-box fits),
                                                 except boxes too small for it, which shrink to fit
    "max_size": null,                            px cap for auto-sized text (make_layout.py: 1.6% of page height)
    "layers": [                                  bottom -> top
      {"name": "Logo", "text": "NOVAS AVENTURAS\\nDE MEGAMAN",
       "box": [0.05, 0.02, 0.90, 0.17],          x, y, width, height
       "font": "Impact Regular",                 exact name from --list-fonts
       "size": null,                             px; null = largest size that fits the box
       "color": "#ffd400", "align": "center",    left | center | right | fill
       "valign": "middle",                       top | middle | bottom inside the box
       "line_spacing": -8, "letter_spacing": 0,
       "outline": {"color": "#000000", "width": 6}},
      {"name": "Fan art", "image": "fanart.png", "box": [0.62, 0.70, 0.30, 0.22]}
    ]
  }

Exit status 0 when GIMP reloaded the saved XCF and found every layer, with
every text layer still editable and holding the exact text. The log lines
(chosen font sizes, missing fonts, possible overflow) are printed.
GIMP command: GIMPhoto (flatpak io.github.diegochagas.GIMPhoto) by default; GIMP_CMD overrides it (e.g.
GIMP_CMD="gimp-3.0"). No Python deps beyond the standard library + Pillow.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

JOB_SCRIPT = Path(__file__).resolve().parent / "gimp_layout_job.py"


def run_gimp(job: dict, timeout: int) -> tuple[list[str], bool]:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, prefix="layout_job_") as f:
        json.dump(job, f)
        job_path = f.name
    log_path = job_path + ".log"
    gimp_cmd = shlex.split(os.environ.get("GIMP_CMD", "flatpak run io.github.diegochagas.GIMPhoto"))
    if gimp_cmd[0] == "flatpak":
        gimp_cmd = gimp_cmd[:2] + [f"--env=LAYOUT_JOB={job_path}"] + gimp_cmd[2:]
        env = None
    else:
        env = {**os.environ, "LAYOUT_JOB": job_path}
    # -i no UI, -d no brushes/patterns; NOT -f: fonts are required for text layers
    cmd = gimp_cmd + ["-id", "--batch-interpreter=python-fu-eval",
                      "-b", f"exec(open({str(JOB_SCRIPT)!r}).read())", "--quit"]
    try:
        subprocess.run(cmd, capture_output=True, timeout=timeout, check=False, env=env)
    except subprocess.TimeoutExpired:
        return [f"FAIL GIMP timed out after {timeout}s (job {job_path})"], False
    if not os.path.exists(log_path):
        return [f"FAIL GIMP produced no log (job {job_path}); is GIMPhoto (io.github.diegochagas.GIMPhoto) installed?"], False
    lines = Path(log_path).read_text().splitlines()
    ok = "DONE" in lines and not any(l.startswith(("FAIL", "FATAL")) for l in lines)
    if ok:
        os.unlink(job_path)
        os.unlink(log_path)
    return lines, ok


def canvas_size(layout: dict, base: Path) -> tuple[int, int]:
    if layout.get("art"):
        from PIL import Image
        with Image.open(base / layout["art"]) as im:
            return im.size
    return tuple(layout["background"]["size"])


def resolve(layout: dict, base: Path) -> dict:
    if bool(layout.get("art")) == bool(layout.get("background")):
        sys.exit('layout needs exactly one of "art" (image path) or "background" ({"color", "size"})')
    cw, ch = canvas_size(layout, base)
    px = layout.get("units", "fraction") == "px"
    names: set[str] = set()
    layers = []
    for item in layout.get("layers", []):
        if ("text" in item) == ("image" in item):
            sys.exit(f'layer {item.get("name")!r}: needs exactly one of "text" or "image"')
        if not item.get("name") or item["name"] in names | {"Art", "Paper"}:
            sys.exit(f'layer name {item.get("name")!r} is missing, duplicated or reserved')
        names.add(item["name"])
        x, y, w, h = item["box"]
        if not px:
            if max(x, y, w, h) > 1.5:
                sys.exit(f'layer {item["name"]!r}: box {item["box"]} looks like pixels — set "units": "px"')
            x, y, w, h = x * cw, y * ch, w * cw, h * ch
        out = {**item, "box": [round(x), round(y), max(1, round(w)), max(1, round(h))]}
        if "image" in item:
            out["image"] = str((base / item["image"]).resolve())
            if not Path(out["image"]).exists():
                sys.exit(f'layer {item["name"]!r}: image not found: {out["image"]}')
        else:
            out.setdefault("font", layout.get("font", "CCWildWords Regular"))
        layers.append(out)
    if not any("text" in l for l in layers):
        sys.exit("layout has no text layers")
    return {
        "art": str((base / layout["art"]).resolve()) if layout.get("art") else None,
        "background": layout.get("background"),
        "out": str((base / layout["out"]).resolve()),
        "preview": str((base / layout["preview"]).resolve()) if layout.get("preview") else None,
        "uniform_size": bool(layout.get("uniform_size")),
        "max_size": layout.get("max_size"),
        "layers": layers,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("layout", nargs="?")
    ap.add_argument("--list-fonts", nargs="?", const="", default=None, metavar="FILTER")
    ap.add_argument("--timeout", type=int, default=600, help="seconds for the GIMP run")
    a = ap.parse_args()

    if a.list_fonts is not None:
        lines, ok = run_gimp({"list_fonts": a.list_fonts}, a.timeout)
        print("\n".join(l[5:] for l in lines if l.startswith("FONT ")) if ok else "\n".join(lines))
        return 0 if ok else 1
    if not a.layout:
        ap.error("layout.json or --list-fonts required")

    path = Path(a.layout).expanduser().resolve()
    job = resolve(json.loads(path.read_text(encoding="utf-8")), path.parent)
    lines, ok = run_gimp(job, a.timeout)
    print("\n".join(l for l in lines if l != "DONE"))
    if ok and job["preview"]:
        print(f"preview: {job['preview']}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
