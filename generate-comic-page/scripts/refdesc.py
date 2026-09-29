"""Comic pages as WORDS instead of images (panel mode).

Reference pages of the original manga, and this comic's own earlier panels,
reach the image model as the descriptions describe-pages wrote for them -
never as attached images. Attached images are only model sheets and
scenarios.

- source set: the description set of the original manga, named in
  project.json "description_sets": {"source": "<set name>"} (find-pages
  resolves the name: ~/Downloads/<name> descriptions/).
- own set: this project's generated panels and pages, described on demand
  by describe-pages into ~/Downloads/<project> descriptions/ (local Ollama,
  free, ~5 s per image).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from common import ROOT

sys.path.insert(0, str(ROOT / "find-pages" / "scripts"))
from find_pages import load_pages, resolve_set  # noqa: E402

DESCRIBE = ROOT / "describe-pages" / "scripts" / "describe_pages.py"
PYTHON = ROOT / "venv" / "bin" / "python"

_cache: dict[str, dict[str, dict]] = {}


def set_index(name_or_dir: str) -> dict[str, dict]:
    """{absolute image path: description} of a description set."""
    if name_or_dir not in _cache:
        _meta, pages = load_pages(resolve_set(name_or_dir))
        _cache[name_or_dir] = {rec["path"]: rec["description"] for rec in pages}
    return _cache[name_or_dir]


def own_set_dir(pdir: Path) -> Path:
    from find_pages import output_root
    return output_root() / f"{pdir.name} descriptions"


def describe_own(pdir: Path, images: list[Path]) -> dict[str, dict]:
    """Descriptions of this project's own images, describing the missing ones
    now. Every image must live under <project>/work/."""
    work = (pdir / "work").resolve()
    set_dir = own_set_dir(pdir)
    have = set_index(str(set_dir)) if (set_dir / "pages.jsonl").exists() else {}
    missing = [p.resolve() for p in images if str(p.resolve()) not in have]
    if missing:
        rels = [str(p.relative_to(work)) for p in missing]
        print(f"describe-pages: describing {len(missing)} own image(s) for continuity…", file=sys.stderr)
        res = subprocess.run([str(PYTHON), str(DESCRIBE), str(work), "--name", pdir.name,
                              "--output", str(set_dir), "--only", *rels],
                             capture_output=True, text=True)
        if res.returncode != 0:
            print(f"WARNING: describe-pages failed, continuity descriptions skipped:\n{res.stderr[-800:]}",
                  file=sys.stderr)
            return {}
        _cache.pop(str(set_dir), None)
        have = set_index(str(set_dir))
    return {str(p.resolve()): have[str(p.resolve())] for p in images if str(p.resolve()) in have}


def format_desc(desc: dict, with_text: bool = False) -> str:
    """One description as prompt prose. The text summary is left out by
    default: talking about lettering invites the model to draw letters."""
    parts = [desc.get("summary", "").strip()]
    if desc.get("setting"):
        parts.append(f"Setting: {desc['setting']}.")
    if desc.get("characters"):
        parts.append("Figures: " + "; ".join(desc["characters"]) + ".")
    if desc.get("objects"):
        parts.append("Drawn objects: " + ", ".join(desc["objects"]) + ".")
    if with_text and desc.get("text_summary"):
        parts.append(f"Text: {desc['text_summary']}")
    return " ".join(p for p in parts if p)


def source_descriptions(cfg: dict, paths: list[str]) -> list[str]:
    name = cfg.get("description_sets", {}).get("source")
    if not paths:
        return []
    if not name:
        sys.exit('panel has source_refs but project.json has no "description_sets": {"source": ...}')
    index = set_index(name)
    out = []
    for p in paths:
        desc = index.get(str(Path(p).expanduser()))
        if desc is None:
            print(f"WARNING: no description of {p} in set {name!r} — skipped", file=sys.stderr)
        else:
            out.append(format_desc(desc))
    return out
