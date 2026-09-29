#!/usr/bin/env python3
"""Generate ONE attempt of ONE panel of a page (panel mode) through the Higgsfield CLI.

The panel comes from <project>/work/<issue>/panels/page_NN/plan.json
(plan_panels.py drafts it). What the image model gets:

  IMAGES (attached)   the model sheets of the characters in the panel
                      (charmap.json) and the scenario images of its location
                      (scenemap.json: pages/crops of the ORIGINAL manga)
  WORDS (prompt)      issue preamble + page style, the panel's own prompt with
                      EMPTY balloons, the character design lock, the scenario
                      descriptions, the describe-pages descriptions of the
                      panel's source_refs (original manga pages) and of this
                      comic's earlier panels (continuity) — comic pages are
                      never attached as images in panel mode

Output: work/<issue>/panels/page_NN/panel_KK_tryT.png (+ .prompt.txt with the
exact prompt and reference list), appended to the panel's "tries" in the
plan. Prints the PNG path. One call = one generation = credits spent.

Usage:
  gen_panel.py -p <project> <issue> <page> <panel> [--fix "..."] [--extra-ref IMG ...]
               [--no-continuity] [--model M] [--quality Q] [--resolution 1k|2k] [--cost] [--dry-run]
  gen_panel.py -p <project> <issue> <page> <panel> --edit-from IMG --instruction "one change"
"""
import argparse
import json
import math
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (character_notes, load_charmap, load_job, load_scenemap, load_state, resolve_project,
                    resolve_sheets)
from gen_page import DESIGN_LOCK, HF, MAX_REFS, NO_LABELS, find_url, run_generation
from refdesc import describe_own, format_desc, source_descriptions

ASPECTS = ["1:1", "3:2", "2:3", "4:3", "3:4", "16:9", "9:16", "21:9", "27:16", "16:27", "9:8", "8:9", "4:5", "5:4"]

SUFFIX_PANEL = (
    "FORMAT — this image is ONE SINGLE comic panel, drawn full-bleed to every edge: no panel "
    "borders, no gutters, no frame, no second panel, no page layout. "
    "DO NOT RENDER ANY TEXT: draw each balloon asked for above in the right shape for its type "
    "(smooth oval for speech, cloud with bubble trail for thought, spiky burst for screams, "
    "rectangular box for captions), tail pointing at its speaker, CLOSED black outline, sized as "
    "asked, kept at least 5% away from the image edges — and leave the inside of every balloon "
    "and box COMPLETELY EMPTY, pure flat white, no letters, no gibberish. Sound effects drawn as "
    "art only where the panel asks for one. " + NO_LABELS)

SCENARIO_LOCK = (
    "SCENARIO LOCK — the attached scenario images are pages of the ORIGINAL manga showing this "
    "location. Draw the place the way they draw it: terrain, vegetation, architecture, props and "
    "how the backgrounds are inked and toned. Any figures in those images are NOT part of this "
    "panel. Written notes on the location:\n")

SOURCE_INTRO = (
    "STAGING REFERENCES — written descriptions of real pages of the original manga (no image "
    "attached). Use them for camera angle, body language, mood and how much background to show; "
    "do not copy their characters:\n")

CONTINUITY_INTRO = (
    "CONTINUITY — written descriptions of the panels that come before this one in the comic. "
    "Keep what they establish (poses carried over, where each figure is, time of day, damage, "
    "props in hand):\n")


def closest_aspect(w: float, h: float, allowed: list[str]) -> str:
    target = math.log(w / h)
    return min(allowed, key=lambda a: abs(math.log(int(a.split(":")[0]) / int(a.split(":")[1])) - target))


def page_style(prompt: str) -> str:
    m = re.search(r"^\s*STYLE:\s*(.+)$", prompt, re.M)
    return m.group(1).strip() if m else ""


def latest_try(panel: dict) -> str | None:
    tries = [t for t in panel.get("tries", []) if Path(t).exists()]
    return panel.get("chosen") or (tries[-1] if tries else None)


def continuity_images(pdir: Path, cfg: dict, issue: str, page: int, plan: dict, panel: dict) -> list[Path]:
    imgs: list[Path] = []
    state = load_state(pdir, issue)
    prev = state.get(f"{page - 1:02d}", {})
    if prev.get("status") == "approved" and prev.get("art") and Path(prev["art"]).exists():
        imgs.append(Path(prev["art"]))
    earlier = [latest_try(p) for p in plan["panels"] if p["id"] < panel["id"]]
    imgs += [Path(t) for t in earlier if t][-cfg.get("continuity_panels", 4):]
    imgs += [Path(c).expanduser() for c in panel.get("continuity", [])]
    return list(dict.fromkeys(imgs))


def build_prompt(job: dict, cfg: dict, plan: dict, panel: dict, notes: list[str], scen: list[dict],
                 sources: list[str], continuity: list[str], fix: str | None) -> str:
    # the issue preamble talks about whole pages; "panel_style" replaces it in panel mode
    parts = [cfg.get("panel_style") or job.get("preamble", "")]
    style = page_style(job["prompt"])
    if style:
        parts.append(f"PAGE STYLE: {style}")
    parts.append(f"THIS PANEL (panel {panel['id']} of {len(plan['panels'])} on the page): {panel['prompt']}")
    if notes:
        parts.append(DESIGN_LOCK + "\n".join(f"- {n}" for n in notes))
    if scen:
        parts.append(SCENARIO_LOCK + "\n".join(f"- {e.get('description', e['keywords'][0])}" for e in scen))
    if sources:
        parts.append(SOURCE_INTRO + "\n".join(f"- {s}" for s in sources))
    if continuity:
        parts.append(CONTINUITY_INTRO + "\n".join(f"- {c}" for c in continuity))
    parts.append(SUFFIX_PANEL)
    if fix:
        parts.append("CORRECTION FOR THIS ATTEMPT (the previous attempt got this wrong — fix it, "
                     "keep everything else as specified): " + fix)
    text = "\n\n".join(p for p in parts if p)
    label = cfg.get("style_label")
    if label:
        text = re.sub(rf"\b{re.escape(label)}\b", "the established art style", text)
    return text


def build_edit_prompt(instruction: str) -> str:
    return ("Edit the FIRST attached image (one comic panel). Make exactly this ONE change: "
            f"{instruction}\n\nDO NOT CHANGE anything else: same composition, characters, outfits, "
            "background and balloons; balloons stay EMPTY. The other attached images are model "
            "sheets and scenario references only. " + NO_LABELS)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", "-p", default=None)
    ap.add_argument("issue")
    ap.add_argument("page", type=int)
    ap.add_argument("panel", type=int)
    ap.add_argument("--fix")
    ap.add_argument("--extra-ref", action="append", default=[])
    ap.add_argument("--no-continuity", action="store_true")
    ap.add_argument("--edit-from")
    ap.add_argument("--instruction")
    ap.add_argument("--model")
    ap.add_argument("--quality", choices=["low", "medium", "high"])
    ap.add_argument("--resolution", choices=["1k", "2k", "4k"])
    ap.add_argument("--cost", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if bool(a.edit_from) != bool(a.instruction):
        ap.error("--edit-from and --instruction go together")

    _name, cfg, pdir = resolve_project(a.project)
    job = load_job(pdir, a.issue, a.page)
    page_dir = pdir / "work" / a.issue / "panels" / f"page_{a.page:02d}"
    plan_path = page_dir / "plan.json"
    if not plan_path.exists():
        sys.exit(f"No panel plan {plan_path} — run plan_panels.py first")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    panel = next((p for p in plan["panels"] if p["id"] == a.panel), None)
    if panel is None:
        sys.exit(f"Panel {a.panel} is not in {plan_path}")

    charmap, scenemap = load_charmap(pdir), load_scenemap(pdir)
    who = panel["prompt"] + "\n" + " ".join(panel.get("characters", []))
    sheets = resolve_sheets(charmap, who)
    scen = [e for e in scenemap.get("map", []) if e["keywords"][0] in panel.get("scenarios", [])]
    scen_imgs = [str(pdir / "refs" / "scenarios" / f) for e in scen for f in e.get("images", [])]

    if a.edit_from:
        prompt = build_edit_prompt(a.instruction)
    else:
        sources = source_descriptions(cfg, panel.get("source_refs", []))
        cont: list[str] = []
        if not a.no_continuity and not a.cost:
            imgs = continuity_images(pdir, cfg, a.issue, a.page, plan, panel)
            descs = describe_own(pdir, imgs) if imgs else {}
            cont = [format_desc(descs[str(p.resolve())]) for p in imgs if str(p.resolve()) in descs]
        prompt = build_prompt(job, cfg, plan, panel, character_notes(charmap, who), scen, sources, cont, a.fix)

    ordered = ([a.edit_from] if a.edit_from else []) + \
        [str(pdir / "refs" / "model-sheets" / s) for s in sheets] + scen_imgs + \
        [str(Path(r).expanduser()) for r in a.extra_ref]
    refs: list[str] = []
    for r in ordered:
        if not Path(r).exists():
            print(f"WARNING: reference not found, skipped: {r}", file=sys.stderr)
        elif r not in refs:
            refs.append(r)
    if len(refs) > MAX_REFS:
        print("WARNING: dropped references: " + ", ".join(Path(r).name for r in refs[MAX_REFS:]), file=sys.stderr)
        refs = refs[:MAX_REFS]

    x, y, w, h = panel["box"]
    aspect = closest_aspect(w, h, cfg.get("aspect_ratios", ASPECTS))
    model = a.model or cfg.get("model", "gpt_image_2_5")
    resolution = a.resolution or cfg.get("panel_resolution", "1k")
    if a.dry_run:
        print(f"# panel {a.panel}  box {w}x{h} -> aspect {aspect}  model {model} {resolution}  references ({len(refs)}):")
        print("\n".join(f"#   {r}" for r in refs))
        print(prompt)
        return

    cmd = [HF, "generate", "cost" if a.cost else "create", model, "--prompt", prompt,
           "--aspect_ratio", aspect, "--resolution", resolution, "--json"]
    if model.startswith("gpt_image"):
        cmd += ["--quality", a.quality or cfg.get("quality", "low")]
    for r in refs:
        cmd += ["--image-references", r]
    if a.cost:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        print(res.stdout.strip() or res.stderr.strip())
        return

    out = run_generation(cmd + ["--wait", "--wait-timeout", "10m"])
    try:
        url = find_url(json.loads(out))
    except ValueError as e:
        sys.exit(f"PARSE_FAIL {e} {out[:400]}")
    if not url:
        sys.exit(f"NO_URL {out[:400]}")

    n = len(list(page_dir.glob(f"panel_{a.panel:02d}_try*.png"))) + 1
    dst = page_dir / f"panel_{a.panel:02d}_try{n}.png"
    while dst.exists():
        n += 1
        dst = page_dir / f"panel_{a.panel:02d}_try{n}.png"
    urllib.request.urlretrieve(url, dst)
    dst.with_suffix(".prompt.txt").write_text(
        f"# aspect {aspect}  model {model} {resolution}\n# references:\n"
        + "".join(f"#   {r}\n" for r in refs) + "\n" + prompt + "\n", encoding="utf-8")

    plan = json.loads(plan_path.read_text(encoding="utf-8"))      # re-read: the agent may have edited it meanwhile
    for p in plan["panels"]:
        if p["id"] == a.panel:
            p.setdefault("tries", []).append(str(dst))
            p["chosen"] = None
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(dst)


if __name__ == "__main__":
    main()
