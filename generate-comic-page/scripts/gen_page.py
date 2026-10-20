#!/usr/bin/env python3
"""Generate ONE attempt of ONE comic page through the Higgsfield CLI.

Builds the prompt (issue preamble + page prompt + character design lock +
lettering/textless suffix), attaches the references (model sheets, extra
refs, style refs — 14 max), runs the generation, downloads the result to
<project>/work/<issue>/gen/page_NN_tryK.png (K = next free number) and marks
the page `awaiting_review` in state.json. Prints the local PNG path.

One call = one generation = credits spent. There is no batch mode on purpose:
Diego reviews every page before the next one is generated.

Usage:
  gen_page.py -p <project> <issue> <page> [--fix "what to correct"]
              [--extra-ref IMG ...] [--prompt-file FILE]
              [--model gpt_image_2_5] [--quality low|medium|high]
              [--cost] [--dry-run]
  gen_page.py -p <project> <issue> <page> --edit-from IMG --instruction "one change"

  --fix          targeted correction appended to the normal prompt (full reroll)
  --extra-ref    approved pages / new examples to attach for this attempt
  --prompt-file  use this exact prompt instead of the built one
  --edit-from    EDIT mode: IMG goes in as the first reference and the prompt
                 is the single change in --instruction (everything else locked)
  --cost         ask Higgsfield what this generation would cost, spend nothing
  --dry-run      print the prompt and the reference list, spend nothing

Every page is generated TEXTLESS — story pages with EMPTY balloons/caption
boxes, covers and editorials (see common.page_kind) with clean areas for the
type — and its text is added afterwards as editable Photoshop text layers
(make_layout.py + build_psd.py). Only a project whose project.json says
"lettering": "ai" (legacy) has the model render the dialogue itself.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (character_notes, load_charmap, load_job, load_state, page_kind,
                    resolve_project, resolve_sheets, save_state)

HF = shutil.which("higgsfield") or str(Path.home() / "hf/node_modules/.bin/higgsfield")
MAX_REFS = 14
LANGS = {"en": "English", "pt-BR": "Brazilian Portuguese", "es": "Spanish", "ja": "Japanese"}

NO_LABELS = (
    "CRITICAL: do NOT print any character names, role labels, scene titles, stage directions, "
    "panel notes, style names or descriptive words as text on the art (never draw words like "
    "'STYLE', 'PAGE', 'PANEL' or any label). No watermarks, no signatures, no page numbers.")

SUFFIX_AI = (
    "Render ALL dialogue and captions in {lang} EXACTLY as written, letter by letter, inside the "
    "balloons/captions. Comic book page, portrait. The ONLY text anywhere on the page is the exact "
    "dialogue and caption strings specified above; caption and location boxes contain ONLY those "
    "exact strings — never prepend/append extra words. Spell every word correctly. " + NO_LABELS)

SUFFIX_MANUAL = (
    "IMPORTANT — DO NOT RENDER ANY TEXT. Draw every speech balloon, thought balloon, scream balloon "
    "and caption box in the correct position, with the correct shape for its type (smooth oval for "
    "speech, cloud with bubble trail for thought, spiky burst for screams, rectangular box for "
    "captions/narration) and the tail pointing at the correct speaker — but leave the inside of "
    "every balloon and caption COMPLETELY EMPTY, pure flat white, no letters, no words, no "
    "gibberish, no pseudo-text. Every balloon and caption box has a CLOSED black outline, stays fully "
    "inside its panel (never overlapping the white gutter or the page margin) and is sized "
    "generously so the dialogue fits when added later. Also leave logo/title areas and any signs empty. Sound-effect onomatopoeia drawn as "
    "stylized art is allowed ONLY where the script explicitly asks for an SFX. Comic book page, "
    "portrait. " + NO_LABELS)

SUFFIX_COVER = (
    "IMPORTANT — TEXTLESS COVER ART. Every logo, title, issue number, price, credit line or other "
    "text mentioned above is added LATER as editable type: draw NONE of it — zero letters, zero "
    "numbers, no pseudo-text, no empty banner/box standing in for the logo. Instead compose the "
    "illustration so those areas stay calm and uncluttered (low-detail background such as sky, "
    "gradient or shadow): a band across the top ~20% of the page for the logo, and room near the "
    "bottom or a side for the title burst and small print. A title burst/starburst SHAPE may be "
    "drawn only if the script asks for one, and it must be EMPTY. Full-bleed comic book cover, "
    "portrait. " + NO_LABELS)

SUFFIX_EDITORIAL = (
    "IMPORTANT — TEXTLESS PAGE FRAME. This is a typography page whose text is added LATER as "
    "editable type: draw ONLY the visual frame — paper background/texture, decorative header band, "
    "rules and borders, and the framed illustration/photo boxes WITH their drawings — and leave "
    "every text area (header, columns, captions, footer) COMPLETELY EMPTY: zero letters, no "
    "pseudo-text, no grey 'lorem' lines. Keep the empty text areas flat and light so dark type "
    "stays legible on top. Magazine page, portrait. " + NO_LABELS)

DESIGN_LOCK = (
    "CHARACTER DESIGN LOCK — the attached model sheets are the ONLY authority for how these "
    "characters look. Copy face, hairstyle, hair colour, eye colour, outfit shapes, colours and "
    "emblems exactly; do not redesign, modernise, simplify or mix designs between characters. "
    "The model sheets give identity only — the drawing style follows the base style above.\n")


def build_prompt(job: dict, cfg: dict, kind: str, notes: list[str], fix: str | None) -> str:
    prompt = job["prompt"]
    # the "=== PAGE N — TITLE ===" header would otherwise leak onto the art
    prompt = re.sub(r"^\s*===\s*PAGE\b.*?===\s*\n?", "", prompt, count=1, flags=re.I)
    parts = [job.get("preamble", ""), prompt]
    if notes:
        parts.append(DESIGN_LOCK + "\n".join(f"- {n}" for n in notes))
    if kind == "cover":
        parts.append(SUFFIX_COVER)
    elif kind == "editorial":
        parts.append(SUFFIX_EDITORIAL)
    elif cfg.get("lettering", "psd") != "ai":
        parts.append(SUFFIX_MANUAL)
    else:                                   # legacy: text baked into the image by the model
        lang = cfg.get("language", job.get("language", "en"))
        parts.append(SUFFIX_AI.format(lang=LANGS.get(lang, lang)))
    if fix:
        parts.append("CORRECTION FOR THIS ATTEMPT (the previous attempt got this wrong — fix it, "
                     "keep everything else as specified): " + fix)
    text = "\n\n".join(p for p in parts if p)
    # a style label ("NAM STYLE") tends to get printed on the page: neutralise it
    label = cfg.get("style_label")
    if label:
        text = re.sub(rf"\b{re.escape(label)}\b", "the established art style", text)
    return text


def build_edit_prompt(instruction: str) -> str:
    return ("Edit the FIRST attached image (a finished comic page). Make exactly this ONE change: "
            f"{instruction}\n\nDO NOT CHANGE anything else: same layout, same panels, same "
            "characters, outfits, colours, backgrounds, balloons and every other piece of text, "
            "letter by letter. The other attached images are character model sheets, for "
            "reference only. " + NO_LABELS)


def collect_refs(pdir: Path, job: dict, sheets: list[str], extra: list[str],
                 first: str | None) -> list[str]:
    ordered: list[str] = [first] if first else []
    ordered += [str(pdir / "refs" / "model-sheets" / f) for f in sheets]
    ordered += extra
    ordered += [str(pdir / "refs" / "style" / f) for f in job.get("style_refs", [])]
    refs: list[str] = []
    for r in ordered:
        if not Path(r).exists():
            print(f"WARNING: reference not found, skipped: {r}", file=sys.stderr)
        elif r not in refs:
            refs.append(r)
    if len(refs) > MAX_REFS:
        print(f"WARNING: {len(refs)} references, the model takes {MAX_REFS} — dropped: "
              + ", ".join(Path(r).name for r in refs[MAX_REFS:]), file=sys.stderr)
    return refs[:MAX_REFS]


def find_url(o):
    if isinstance(o, str) and o.startswith("http") and \
            o.lower().split("?")[0].endswith((".png", ".jpg", ".jpeg", ".webp")):
        return o
    for v in (o.values() if isinstance(o, dict) else o if isinstance(o, list) else []):
        u = find_url(v)
        if u:
            return u
    return None


def run_generation(cmd: list[str]) -> str:
    """Run the CLI, retrying transient failures (503, intermittent nsfw filter)."""
    err = ""
    for _ in range(4):
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=660)
        out = (res.stdout or "").strip()
        err = ((res.stderr or "").strip() + " " + out).strip()
        if res.returncode == 0 and out:
            return out
        if re.search(r"unauthori[sz]ed|not logged in|auth login|\b401\b", err, re.I):
            sys.exit(f"ERROR auth — ask Diego to run `higgsfield auth login`: {err[:300]}")
        if not any(t in err.lower() for t in ("503", "service unavailable", "nsfw", "timeout", "temporarily")):
            sys.exit(f"ERROR {res.returncode} {err[:500]}")
        time.sleep(4)
    sys.exit(f"ERROR retries-exhausted {err[:300]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", "-p", default=None)
    ap.add_argument("issue")
    ap.add_argument("page", type=int)
    ap.add_argument("--fix")
    ap.add_argument("--extra-ref", action="append", default=[])
    ap.add_argument("--prompt-file")
    ap.add_argument("--edit-from")
    ap.add_argument("--instruction")
    ap.add_argument("--model", default=None)
    ap.add_argument("--quality", default=None, choices=["low", "medium", "high"])
    ap.add_argument("--cost", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if bool(a.edit_from) != bool(a.instruction):
        ap.error("--edit-from and --instruction go together")

    _name, cfg, pdir = resolve_project(a.project)
    job = load_job(pdir, a.issue, a.page)
    kind = job.get("kind") or page_kind(cfg, a.page, job.get("title", ""))
    # charmap is re-read on every attempt so freshly imported examples count
    charmap = load_charmap(pdir)
    sheets = list(dict.fromkeys(job.get("model_sheets", []) + resolve_sheets(charmap, job["prompt"])))

    if a.edit_from:
        prompt = build_edit_prompt(a.instruction)
    elif a.prompt_file:
        prompt = Path(a.prompt_file).read_text(encoding="utf-8")
    else:
        prompt = build_prompt(job, cfg, kind, character_notes(charmap, job["prompt"]), a.fix)
    refs = collect_refs(pdir, job, sheets, [str(Path(r).expanduser()) for r in a.extra_ref], a.edit_from)

    model = a.model or cfg.get("model", "gpt_image_2_5")
    if a.dry_run:
        print(f"# kind: {kind}   model: {model}   references ({len(refs)}):")
        print("\n".join(f"#   {r}" for r in refs))
        print(prompt)
        return

    cmd = [HF, "generate", "cost" if a.cost else "create", model, "--prompt", prompt,
           "--aspect_ratio", job.get("aspect", cfg.get("aspect", "2:3")), "--resolution", "2k", "--json"]
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

    gendir = pdir / "work" / a.issue / "gen"
    gendir.mkdir(parents=True, exist_ok=True)
    tries = [int(m.group(1)) for f in gendir.glob(f"page_{a.page:02d}_try*.png")
             if (m := re.search(r"_try(\d+)\.png$", f.name))]
    dst = gendir / f"page_{a.page:02d}_try{max(tries, default=0) + 1}.png"
    urllib.request.urlretrieve(url, dst)

    state = load_state(pdir, a.issue)
    entry = state.setdefault(f"{a.page:02d}", {"status": "pending", "tries": 0,
                                               "title": job.get("title", ""), "notes": ""})
    entry.update(status="awaiting_review", tries=entry.get("tries", 0) + 1, last_gen=str(dst))
    entry["stage"] = "art"
    save_state(pdir, a.issue, state)
    print(dst)


if __name__ == "__main__":
    main()
