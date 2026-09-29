#!/usr/bin/env python3
"""Compose the panels of a page (panel mode) into the page art.

Each panel's chosen attempt (plan "chosen", else its latest try) is scaled to
cover its box, cropped around its "focus", and pasted on a white page with a
black border; the result is saved as the page's next attempt,
work/<issue>/gen/page_NN_tryK.png, so make_layout.py / build_xcf.py /
page_state.py work on it exactly as on a page drawn in one generation.
A sidecar page_NN_tryK.panels.json records which panel files went in and
where (make_layout.py uses it to pair each panel's balloons with that
panel's lines). Free — no generation.

Usage: assemble_page.py -p <project> <issue> <page> [--preview-only]
  --preview-only   write work/<issue>/panels/page_NN/assembled_preview.jpg
                   instead of a new page attempt (page state untouched)
"""
import argparse
import json
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import load_state, resolve_project, save_state
from gen_panel import latest_try


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", "-p", default=None)
    ap.add_argument("issue")
    ap.add_argument("page", type=int)
    ap.add_argument("--preview-only", action="store_true")
    a = ap.parse_args()

    _name, _cfg, pdir = resolve_project(a.project)
    page_dir = pdir / "work" / a.issue / "panels" / f"page_{a.page:02d}"
    plan = json.loads((page_dir / "plan.json").read_text(encoding="utf-8"))
    W, H = plan["page_size"]
    border = plan.get("border", 5)
    page = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(page)

    used, missing = [], []
    for p in plan["panels"]:
        src = latest_try(p)
        if not src:
            missing.append(p["id"])
            continue
        x, y, w, h = p["box"]
        fx, fy = p.get("focus", [0.5, 0.5])
        art = ImageOps.fit(Image.open(src).convert("RGB"), (w, h), Image.LANCZOS, centering=(fx, fy))
        page.paste(art, (x, y))
        draw.rectangle([x, y, x + w - 1, y + h - 1], outline="black", width=border)
        used.append({"id": p["id"], "file": src, "box": p["box"], "dialogue": p.get("dialogue", [])})
    if missing:
        sys.exit(f"Panels without any attempt yet: {missing} — generate them with gen_panel.py first")

    if a.preview_only:
        dst = page_dir / "assembled_preview.jpg"
        page.save(dst, quality=90)
        print(dst)
        return

    gendir = pdir / "work" / a.issue / "gen"
    gendir.mkdir(parents=True, exist_ok=True)
    tries = [int(m.group(1)) for f in gendir.glob(f"page_{a.page:02d}_try*.png")
             if (m := re.search(r"_try(\d+)\.png$", f.name))]
    dst = gendir / f"page_{a.page:02d}_try{max(tries, default=0) + 1}.png"
    page.save(dst)
    dst.with_suffix(".panels.json").write_text(json.dumps({"panels": used}, ensure_ascii=False, indent=2) + "\n",
                                               encoding="utf-8")

    state = load_state(pdir, a.issue)
    entry = state.setdefault(f"{a.page:02d}", {"status": "pending", "tries": 0, "title": "", "notes": ""})
    entry.update(status="awaiting_review", tries=entry.get("tries", 0) + 1, last_gen=str(dst), stage="art")
    save_state(pdir, a.issue, state)
    print(dst)


if __name__ == "__main__":
    main()
