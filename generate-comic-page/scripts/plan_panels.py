#!/usr/bin/env python3
"""Draft the PANEL PLAN of a page (panel mode, project.json "generation": "panels").

In panel mode a page is not drawn in one generation: every panel is its own
generation (gen_panel.py) and assemble_page.py composes them into the page.
This writes the first draft of <project>/work/<issue>/panels/page_NN/plan.json:

  page_size / margin / gutter / border   the page canvas, in px
  panels[]:
    id          reading order
    box         [x, y, w, h] in page px — each panel is generated at the
                supported aspect ratio closest to its box, then cropped to it
    focus       [fx, fy] 0..1 — which part of the generation to keep when cropping
    beat        the script beat this panel draws (verbatim)
    prompt      what to draw, in prose: the beat with every quoted line replaced
                by an EMPTY balloon of the right size — the model never sees the text
    dialogue    the exact lines of this panel (copied from the job, in order)
    scenarios   scenemap.json entries (first keyword) for the location
    source_refs pages of the ORIGINAL manga (paths from find-pages) whose
                descriptions guide staging/camera — sent as words, not images
    continuity  extra own images (earlier panels/pages) to describe in the prompt;
                earlier panels of this page and the previous approved page are added
                automatically by gen_panel.py
    tries / chosen   filled by gen_panel.py / by the agent

One panel per STORY BEATS bullet, rows of two, equal heights. It is only a
draft: the agent rewrites prompts, merges or splits beats, resizes boxes
(a splash panel, a wide establishing shot), fills source_refs with
find-pages, and checks the scenarios before generating anything.

Usage: plan_panels.py -p <project> <issue> <page> [--force]
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import load_job, load_scenemap, resolve_project, resolve_scenarios

QUOTE_RE = re.compile(r"[\"“]([^\"”]{2,400})[\"”]")
BEAT_RE = re.compile(r"^\s*[-*•]\s+(.+)$")


def beats_of(prompt: str) -> list[str]:
    """Bullets under STORY BEATS (or every bullet of the page when there is no such header)."""
    m = re.search(r"STORY BEATS[^\n]*\n(.*)", prompt, re.S | re.I)
    body = m.group(1) if m else prompt
    return [b.group(1).strip() for line in body.splitlines() if (b := BEAT_RE.match(line))]


def empty_balloons(beat: str) -> str:
    def repl(m: re.Match) -> str:
        words = len(m.group(1).split())
        return f"(an EMPTY balloon sized for about {words} word{'s' if words != 1 else ''})"
    return QUOTE_RE.sub(repl, beat)


def grid(n: int, W: int, H: int, margin: int, gutter: int) -> list[list[int]]:
    per_row = 2 if n <= 8 else 3
    rows = [per_row] * (n // per_row) + ([n % per_row] if n % per_row else [])
    if len(rows) > 1 and rows[-1] == 1:          # a lone panel reads better at the top (establishing shot)
        rows = [1] + rows[:-1]
    rh = (H - 2 * margin - gutter * (len(rows) - 1)) / len(rows)
    boxes, y = [], margin
    for cols in rows:
        cw = (W - 2 * margin - gutter * (cols - 1)) / cols
        for c in range(cols):
            boxes.append([round(margin + c * (cw + gutter)), round(y), round(cw), round(rh)])
        y += rh + gutter
    return boxes


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", "-p", default=None)
    ap.add_argument("issue")
    ap.add_argument("page", type=int)
    ap.add_argument("--force", action="store_true", help="overwrite an existing plan")
    a = ap.parse_args()

    _name, cfg, pdir = resolve_project(a.project)
    job = load_job(pdir, a.issue, a.page)
    pdir_page = pdir / "work" / a.issue / "panels" / f"page_{a.page:02d}"
    plan_path = pdir_page / "plan.json"
    if plan_path.exists() and not a.force:
        sys.exit(f"{plan_path} exists (the agent may have edited it) — use --force to redraft")

    beats = beats_of(job["prompt"])
    if not beats:
        sys.exit("No bullet beats found in the page script — write plan.json by hand")
    W, H = cfg.get("page_size", [1365, 2048])
    margin, gutter = cfg.get("page_margin", 60), cfg.get("panel_gutter", 30)
    scenemap = load_scenemap(pdir)
    page_scen = [e["keywords"][0] for e in resolve_scenarios(scenemap, job["prompt"])]
    exact = job.get("dialogue_exact", [])

    panels = []
    for i, (beat, box) in enumerate(zip(beats, grid(len(beats), W, H, margin, gutter)), 1):
        quoted = [q.strip() for q in QUOTE_RE.findall(beat)]
        lines = [s for q in quoted for s in exact if s == q]
        own_scen = [e["keywords"][0] for e in resolve_scenarios(scenemap, beat)]
        panels.append({"id": i, "box": box, "focus": [0.5, 0.5], "beat": beat,
                       "prompt": empty_balloons(beat), "dialogue": lines,
                       "scenarios": own_scen or page_scen, "source_refs": [], "continuity": [],
                       "tries": [], "chosen": None})
    plan = {"issue": a.issue, "page": a.page, "page_size": [W, H], "margin": margin, "gutter": gutter,
            "border": cfg.get("panel_border", 5), "panels": panels}
    pdir_page.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    placed = [s for p in panels for s in p["dialogue"]]
    print(f"{len(panels)} panel(s) drafted -> {plan_path}")
    for p in panels:
        print(f"  {p['id']:>2} box={p['box']} lines={len(p['dialogue'])} scenarios={p['scenarios']}  {p['beat'][:70]}")
    for s in exact:
        if s not in placed:
            print(f"  NOT IN ANY PANEL (page caption or quoted outside a beat?): {s!r}")
    if not scenemap.get("map"):
        print("  no scenemap.json entries yet — find the location in the source description set "
              "(find_pages.py <set> <terms> --field setting --show), look at the hits, "
              "import_refs.py --scenario, register it in scenemap.json")


if __name__ == "__main__":
    main()
