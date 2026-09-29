"""Shared helpers: project discovery, config, charmap matching, page state."""
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent   # generate-comic-page/
ROOT = SKILL.parent                                # repo root
TEMPLATE = SKILL / "_template"
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")


def projects_root() -> Path:
    """Comic projects live OUTSIDE the repo: ~/Downloads/<project>/ unless
    COMIC_PROJECTS_DIR points somewhere else."""
    return Path(os.environ.get("COMIC_PROJECTS_DIR") or Path.home() / "Downloads").expanduser()


def list_projects() -> list[str]:
    root = projects_root()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and (p / "project.json").exists())


def resolve_project(arg: str | None) -> tuple[str, dict, Path]:
    """Return (name, config, project_dir). `arg` is a project name under
    projects_root() or a path to any folder holding a project.json; with no
    arg, use the only project."""
    if arg and (Path(arg).expanduser() / "project.json").exists():
        pdir = Path(arg).expanduser().resolve()
    else:
        names = list_projects()
        if not names:
            sys.exit(f"No projects found under {projects_root()} (need <project>/project.json) — "
                     "create one with new_project.py or pass -p <path to the project folder>")
        if arg is None:
            if len(names) != 1:
                sys.exit(f"Multiple projects exist — specify one of: {', '.join(names)}")
            arg = names[0]
        if arg not in names:
            sys.exit(f"Unknown project '{arg}'. Available under {projects_root()}: {', '.join(names)}")
        pdir = projects_root() / arg
    config = json.loads((pdir / "project.json").read_text(encoding="utf-8"))
    return pdir.name, config, pdir


# ---------------------------------------------------------------- page kinds

def page_kind(cfg: dict, page_no: int, title: str = "") -> str:
    """'cover' | 'editorial' | 'story'. project.json "page_kinds" ({"1": "cover"})
    wins; otherwise the page title decides."""
    kinds = cfg.get("page_kinds", {})
    if str(page_no) in kinds:
        return kinds[str(page_no)]
    t = strip_accents(title or "").upper()
    if re.search(r"\b(COVER|CAPA)\b", t):
        return "cover"
    if "EDITORIAL" in t:
        return "editorial"
    return "story"


# ------------------------------------------------------------------- charmap

def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def load_charmap(pdir: Path) -> dict:
    path = pdir / "charmap.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _matching_entries(charmap: dict, page_text: str) -> list[dict]:
    text = strip_accents(page_text).upper()
    return [e for e in charmap.get("map", [])
            if any(strip_accents(k).upper() in text for k in e["keywords"])]


def resolve_sheets(charmap: dict, page_text: str) -> list[str]:
    sheets: list[str] = []
    for entry in _matching_entries(charmap, page_text):
        for s in entry["sheets"]:
            if s not in sheets:
                sheets.append(s)
    return sheets


def character_notes(charmap: dict, page_text: str) -> list[str]:
    """The "description" of every charmap entry present on the page — what the
    agent wrote down after LOOKING at the model sheets."""
    return [e["description"] for e in _matching_entries(charmap, page_text) if e.get("description")]


def load_scenemap(pdir: Path) -> dict:
    """scenemap.json: like charmap.json, for LOCATIONS (panel mode). Each entry:
    keywords, images (files in refs/scenarios/, pages or crops of the ORIGINAL
    manga found by searching its description set), description, source_pages."""
    path = pdir / "scenemap.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def resolve_scenarios(scenemap: dict, text: str) -> list[dict]:
    return _matching_entries(scenemap, text)


def no_sheet_chars(charmap: dict, page_text: str) -> list[str]:
    text = strip_accents(page_text).upper()
    return [c for c in charmap.get("no_sheet_characters", []) if strip_accents(c).upper() in text]


# --------------------------------------------------------------------- state

def state_path(pdir: Path, issue: str) -> Path:
    return pdir / "work" / issue / "state.json"


def load_state(pdir: Path, issue: str) -> dict:
    path = state_path(pdir, issue)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_state(pdir: Path, issue: str, state: dict) -> None:
    path = state_path(pdir, issue)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def load_job(pdir: Path, issue: str, page: int) -> dict:
    path = pdir / "jobs" / issue / f"page_{page:02d}.json"
    if not path.exists():
        sys.exit(f"No job {path} — run split_scripts.py -p {pdir.name} {issue} first")
    return json.loads(path.read_text(encoding="utf-8"))
