#!/usr/bin/env python3
"""GIMP-format twin of build_translated_psd.mjs: builds an .xcf with layers
"Original", "Copy" and one NATIVE GIMP text layer "Text N" per block (fixed
paragraph box, font, size, colour, justification, rotation), by driving
headless GIMPhoto (flatpak GIMP 3) with gimp_xcf_job.py. Same blocks JSON, same flags.

Usage (one page):
  python build_translated_xcf.py <source_image> <blocks.json> <out.xcf>
        [--font CCWildWords-Regular] [--no-copy] [--copy-image <png>]
        [--placeholder [text]] [--preview <jpg>]

Usage (batch - GIMP takes ~15 s to start, so group pages when you can):
  python build_translated_xcf.py --job <pages.json>
  pages.json: [{"source": ..., "blocks": ..., "out": ..., "copy_image": ...,
                "with_copy": true, "placeholder": null, "font": ..., "preview": ...}, ...]

Exit status 0 when every page logged OK; the per-page OK/FAIL lines are
printed. GIMP command: GIMPhoto (flatpak io.github.diegochagas.GIMPhoto) by default; set GIMP_CMD to a
different launcher (e.g. GIMP_CMD="gimp-3.0" for a native install).
No Python deps beyond the standard library.
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

HERE = Path(__file__).resolve().parent
JOB_SCRIPT = HERE / "gimp_xcf_job.py"
DEFAULT_PLACEHOLDER = "Lorem ipsum dolor sit amet, consectetur adipiscing elit."


def run_gimp(pages: list[dict], timeout: int) -> tuple[list[str], bool]:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, prefix="xcf_job_") as f:
        json.dump({"pages": pages}, f)
        job_path = f.name
    log_path = job_path + ".log"
    gimp_cmd = shlex.split(os.environ.get("GIMP_CMD", "flatpak run io.github.diegochagas.GIMPhoto"))
    if gimp_cmd[0] == "flatpak":
        gimp_cmd = gimp_cmd[:2] + [f"--env=XCF_JOB={job_path}"] + gimp_cmd[2:]
        env = None
    else:
        env = {**os.environ, "XCF_JOB": job_path}
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?")
    ap.add_argument("blocks", nargs="?")
    ap.add_argument("out", nargs="?")
    ap.add_argument("--job", help="JSON list of pages (batch mode)")
    ap.add_argument("--font", default="CCWildWords-Regular")
    ap.add_argument("--no-copy", action="store_true")
    ap.add_argument("--copy-image")
    ap.add_argument("--placeholder", nargs="?", const=DEFAULT_PLACEHOLDER, default=None)
    ap.add_argument("--preview", help="also write a flattened JPG rendered by GIMP")
    ap.add_argument("--timeout", type=int, default=900, help="seconds for the whole GIMP run")
    a = ap.parse_args()

    if a.job:
        pages = json.load(open(a.job))
        if isinstance(pages, dict):
            pages = pages["pages"]
    else:
        if not (a.source and a.blocks and a.out):
            ap.error("need <source> <blocks.json> <out.xcf>, or --job <pages.json>")
        pages = [{"source": a.source, "blocks": a.blocks, "out": a.out, "copy_image": a.copy_image,
                  "with_copy": not a.no_copy, "placeholder": a.placeholder, "font": a.font,
                  "preview": a.preview}]
    for p in pages:
        for k in ("source", "blocks", "out", "copy_image", "preview"):
            if p.get(k):
                p[k] = str(Path(p[k]).expanduser().resolve())
        p.setdefault("with_copy", True)
        p.setdefault("font", a.font)
        if not Path(p["source"]).exists():
            print(f"FAIL missing source {p['source']}")
            return 1
        if not Path(p["blocks"]).exists():
            print(f"FAIL missing blocks json {p['blocks']}")
            return 1

    lines, ok = run_gimp(pages, a.timeout)
    for l in lines:
        if l != "DONE":
            print(l)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
