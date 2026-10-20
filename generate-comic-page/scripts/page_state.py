#!/usr/bin/env python3
"""Read and update the per-page review state (<project>/work/<issue>/state.json).

Statuses: pending -> awaiting_review (generated, waiting for Diego) -> approved.
Cover/editorial pages also carry a "stage": "art" (textless illustration under
review) then "layout" (PSD with the editable text under review).

Usage:
  page_state.py -p <project> next <issue>
      the page to work on now: the first awaiting_review page, else the first
      pending one (prints page number, kind, status, title)
  page_state.py -p <project> show <issue> [page]
  page_state.py -p <project> set <issue> <page> [--status S] [--stage art|layout] [--note "..."]
  page_state.py -p <project> approve <issue> <page> [--file IMG] [--psd FILE] [--note "..."]
      copies the lettered preview (default: work/<issue>/layout/page_NN_preview.jpg,
      with its out/psd/<issue>/page_NN.psd) to work/<issue>/approved/page_NN.jpg
      and marks the page approved. Refuses when the PSD was not built, or was
      built from an older attempt than the latest art. Legacy "lettering": "ai"
      projects approve the latest gen/page_NN_tryK.png instead. Run it ONLY
      after Diego said the page is OK.
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import IMAGE_EXTS, load_job, load_state, page_kind, resolve_project, save_state

STATUSES = ["pending", "awaiting_review", "approved", "needs_review"]


def latest_gen(pdir: Path, issue: str, page: int) -> Path | None:
    gens = [(int(m.group(1)), f) for f in (pdir / "work" / issue / "gen").glob(f"page_{page:02d}_try*.png")
            if (m := re.search(r"_try(\d+)\.png$", f.name))]
    return max(gens)[1] if gens else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", "-p", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("next"); s.add_argument("issue")
    s = sub.add_parser("show"); s.add_argument("issue"); s.add_argument("page", type=int, nargs="?")
    s = sub.add_parser("set"); s.add_argument("issue"); s.add_argument("page", type=int)
    s.add_argument("--status", choices=STATUSES); s.add_argument("--stage", choices=["art", "layout"])
    s.add_argument("--note")
    s = sub.add_parser("approve"); s.add_argument("issue"); s.add_argument("page", type=int)
    s.add_argument("--file"); s.add_argument("--psd"); s.add_argument("--note")
    a = ap.parse_args()

    _name, cfg, pdir = resolve_project(a.project)
    state = load_state(pdir, a.issue)
    if not state:
        sys.exit(f"No state for issue {a.issue} — run split_scripts.py -p {pdir.name} {a.issue} first")

    if a.cmd == "next":
        for wanted in ("awaiting_review", "pending"):
            for key in sorted(state):
                if state[key]["status"] == wanted:
                    e = state[key]
                    kind = page_kind(cfg, int(key), e.get("title", ""))
                    stage = f" stage={e['stage']}" if e.get("stage") else ""
                    print(f"page {key}  kind={kind}  status={wanted}{stage}  tries={e.get('tries', 0)}  title={e.get('title', '')}")
                    return
        print(f"ALL DONE — every page of issue {a.issue} is approved"
              if all(e["status"] == "approved" for e in state.values())
              else "nothing pending (remaining pages are needs_review)")
        return

    if a.cmd == "show":
        keys = [f"{a.page:02d}"] if a.page else sorted(state)
        print(json.dumps({k: state[k] for k in keys if k in state}, ensure_ascii=False, indent=2))
        return

    key = f"{a.page:02d}"
    if key not in state:
        sys.exit(f"Page {key} is not in issue {a.issue}")
    entry = state[key]

    if a.cmd == "set":
        if a.status:
            entry["status"] = a.status
        if a.stage:
            entry["stage"] = a.stage
        if a.note is not None:
            entry["notes"] = a.note
    else:  # approve
        job = load_job(pdir, a.issue, a.page)
        kind = job.get("kind") or page_kind(cfg, a.page, job.get("title", ""))
        needs_psd = kind != "story" or cfg.get("lettering", "psd") != "ai"
        src = Path(a.file).expanduser() if a.file else None
        psd = Path(a.psd).expanduser() if a.psd else None
        if needs_psd:
            src = src or pdir / "work" / a.issue / "layout" / f"page_{a.page:02d}_preview.jpg"
            psd = psd or pdir / "out" / "psd" / a.issue / f"page_{a.page:02d}.psd"
            if not src.exists() or not psd.exists():
                sys.exit(f"Page {key} has no lettered PSD yet ({psd}) — run make_layout.py + build_psd.py, "
                         "show Diego the preview, then approve")
            art = latest_gen(pdir, a.issue, a.page)
            if not a.file and art and art.stat().st_mtime > psd.stat().st_mtime:
                sys.exit(f"{psd.name} is older than the latest art {art.name} — rebuild the PSD from it "
                         "(make_layout.py --force, build_psd.py) or pass --file/--psd explicitly")
        else:
            src = src or latest_gen(pdir, a.issue, a.page)
        if not src or not src.exists():
            sys.exit(f"Nothing to approve: {src or 'no generated file for this page'}")
        if src.suffix.lower() not in IMAGE_EXTS:
            sys.exit(f"--file must be an image ({', '.join(IMAGE_EXTS)}) — the PSD preview JPG")
        approved = pdir / "work" / a.issue / "approved"
        approved.mkdir(parents=True, exist_ok=True)
        for old in approved.glob(f"page_{a.page:02d}.*"):
            old.unlink()
        dst = approved / f"page_{a.page:02d}{src.suffix.lower()}"
        shutil.copy2(src, dst)
        entry.update(status="approved", approved_file=str(dst))
        if psd:
            entry["psd"] = str(psd.resolve())
            layout = pdir / "work" / a.issue / "layout" / f"page_{a.page:02d}.layout.json"
            if layout.exists():     # the textless art behind the PSD: the continuity reference for later pages
                entry["art"] = json.loads(layout.read_text(encoding="utf-8")).get("art")
        if a.note is not None:
            entry["notes"] = a.note
        print(f"approved -> {dst}")

    save_state(pdir, a.issue, state)
    print(json.dumps({key: entry}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
