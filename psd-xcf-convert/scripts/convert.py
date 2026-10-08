#!/usr/bin/env python3
"""Convert Photoshop PSDs to GIMP XCFs and GIMP XCFs to Photoshop PSDs, keeping
the text EDITABLE on the other side: every .psd/.psb in the path becomes an
.xcf and every .xcf becomes a .psd, in a new folder in ~/Downloads.

  Type layers <-> native GIMP text layers (same font, size, colour,
      justification, tracking, leading, paragraph box or point text, rotation,
      mixed bold/italic/colour/size runs)
  Layer Styles stroke / drop shadow <-> Filters > Text Styling (gegl:styles)
      outline / shadow, on text layers and on any other layer
  pixels, groups, masks, blend modes, opacity, visibility: GIMP's own PSD
      import / export

Usage:
  python convert.py <file-or-folder> [--to xcf|psd] [--recursive] [--preview]
        [--output DIR] [--overwrite] [--keep-raster] [--font-map "PS-Name=GIMP Font Name"]...

Output: <root>/<source name> converted/, <root> = $COMIC_OUTPUT_DIR or
~/Downloads; --output replaces the whole path. Inputs are never modified.

Pipeline (one headless GIMP start per batch of files, ~15 s each start):
  .psd: psd_text_info.mjs (ag-psd: text + layer styles -> JSON)
        -> gimp_convert_job.py (GIMP loads the PSD, swaps the rasterized
           text for text layers, adds Text Styling filters, saves .xcf)
  .xcf: gimp_convert_job.py (describes text layers + filters, exports a PSD)
        -> write_psd_text.mjs (ag-psd: rasterized text -> Type layers + Layer Styles)

Fonts are matched through fontconfig (PostScript name <-> family + style).
A font that is not installed is replaced by fontconfig's closest match and
reported; the original name is kept inside the XCF so the way back restores
it. GIMP command: GIMPhoto (flatpak io.github.diegochagas.GIMPhoto); GIMP_CMD overrides (GIMP 3 only).
Exit status 0 when every file converted, 2 otherwise. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
JOB_SCRIPT = HERE / "gimp_convert_job.py"
PSD_EXT = {".psd", ".psb"}
XCF_EXT = {".xcf"}
BATCH = 15                                   # files per GIMP start


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# ------------------------------------------------------------------ fonts

class Fonts:
    """fontconfig's view of the installed fonts: PostScript name <-> the
    "Family Style" names GIMP 3 lists."""

    def __init__(self) -> None:
        self.entries: list[dict] = []
        try:
            out = subprocess.run(["fc-list", "-f", "%{family}\t%{style}\t%{fullname}\t%{postscriptname}\n"],
                                 capture_output=True, text=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError):
            out = ""
        seen = set()
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) != 4 or line in seen:
                continue
            seen.add(line)
            fam, sty, full, ps = ([p.strip().replace("\\-", "-") for p in x.split(",") if p.strip()] for x in parts)
            if fam:
                self.entries.append({"family": fam, "style": sty or ["Regular"], "fullname": full, "ps": ps[0] if ps else ""})
        self.by_ps = {norm(e["ps"]): e for e in self.entries if e["ps"]}
        self.by_name: dict[str, dict] = {}
        for e in self.entries:
            for f in e["family"]:
                for s in e["style"]:
                    self.by_name.setdefault(norm(f + s), e)
                if any(norm(s) in ("regular", "normal", "book", "roman") for s in e["style"]):
                    self.by_name.setdefault(norm(f), e)
            for n in e["fullname"]:
                self.by_name.setdefault(norm(n), e)

    @staticmethod
    def gimp_names(e: dict) -> list[str]:
        names = [f"{f} {s}" for f in e["family"] for s in e["style"]] + e["fullname"] + e["family"]
        return list(dict.fromkeys(names))

    def _fc_match(self, pattern: str) -> dict | None:
        try:
            out = subprocess.run(["fc-match", "-f", "%{family}\t%{style}\t%{fullname}\t%{postscriptname}", pattern],
                                 capture_output=True, text=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError):
            return None
        parts = out.split("\t")
        if len(parts) != 4:
            return None
        fam, sty, full, ps = ([p.strip() for p in x.split(",") if p.strip()] for x in parts)
        return {"family": fam, "style": sty or ["Regular"], "fullname": full, "ps": ps[0] if ps else ""} if fam else None

    def to_gimp(self, psname: str, font_map: dict[str, str]) -> dict:
        """{"gimp": [candidate names], "substitute": bool} for a Photoshop font name."""
        if psname in font_map:
            e = self.by_name.get(norm(font_map[psname]))
            return {"gimp": [font_map[psname]] + (self.gimp_names(e) if e else []), "substitute": False}
        e = self.by_ps.get(norm(psname)) or self.by_name.get(norm(psname))
        if e:
            return {"gimp": self.gimp_names(e), "substitute": False}
        # not installed: "Arial-BoldMT" -> family "Arial", style "Bold" -> fontconfig's closest font
        fam, _, sty = psname.partition("-")
        strip = lambda s: re.sub(r"(PS)?MT$|PS$", "", s)
        fam = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", strip(fam))
        sty = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", strip(sty)).strip()
        m = self._sibling(fam, sty) or self._fc_match(fam + (f":style={sty}" if sty else ""))
        return {"gimp": self.gimp_names(m) if m else [], "substitute": True}

    @staticmethod
    def _flags(style: str) -> tuple[bool, bool]:
        s = norm(style)
        return (any(k in s for k in ("bold", "black", "heavy")), any(k in s for k in ("italic", "oblique")))

    def _sibling(self, fam: str, sty: str) -> dict | None:
        """An installed font of the same type family ("CCWildWordsLower-BoldItalic"
        missing, "CCWildWords Bold Italic" installed) beats fontconfig's generic
        sans: same bold/italic first, then the longest shared family name."""
        want, key, best = self._flags(sty), norm(fam), None
        for e in self.entries:
            for f in e["family"]:
                n = norm(f)
                if len(n) >= 5 and (key.startswith(n) or n.startswith(key)):
                    score = (self._flags(" ".join(e["style"]) + " " + f[len(fam):]) == want, min(len(n), len(key)))
                    if best is None or score > best[0]:
                        best = (score, e)
        return best[1] if best else None

    def family_variant(self, e: dict, bold: bool, italic: bool) -> dict | None:
        def flags(x: dict) -> tuple[bool, bool]:
            return self._flags(" ".join(x["style"]))
        have = flags(e)
        want = (have[0] or bold, have[1] or italic)
        for x in self.entries:
            if set(x["family"]) & set(e["family"]) and flags(x) == want and x["ps"]:
                return x
        return None

    def to_ps(self, run: dict, font_back: dict[str, str]) -> tuple[str, bool, bool, str | None]:
        """(PostScript name, fauxBold, fauxItalic, note) for a run described by GIMP."""
        name = run.get("gimp_font") or ""
        bold, italic = bool(run.get("bold")), bool(run.get("italic"))
        if name in font_back and not bold and not italic:
            return font_back[name], False, False, None      # the font the PSD originally asked for
        e = self.by_ps.get(norm(run.get("psname") or "")) or self.by_name.get(norm(name))
        note = None
        if e is None:
            e = self._fc_match(name) if name else None
            note = f'font "{name}" not found by fontconfig: wrote "{e["ps"] if e else "ArialMT"}"'
        if e is None or not e["ps"]:
            return run.get("psname") or "ArialMT", bold, italic, note
        if bold or italic:
            v = self.family_variant(e, bold, italic)
            if v:
                return v["ps"], False, False, note
            return e["ps"], bold, italic, note                # no such cut installed: Photoshop's faux style
        return e["ps"], False, False, note


# ------------------------------------------------------------------- GIMP

def run_gimp(tasks: list[dict], work: Path, timeout: int) -> list[str]:
    job_path = work / "job.json"
    log_path = Path(str(job_path) + ".log")
    log_path.unlink(missing_ok=True)
    job_path.write_text(json.dumps({"tasks": tasks}))
    gimp_cmd = shlex.split(os.environ.get("GIMP_CMD", "flatpak run io.github.diegochagas.GIMPhoto"))
    env = None
    if gimp_cmd[0] == "flatpak":
        gimp_cmd = gimp_cmd[:2] + [f"--env=CONVERT_JOB={job_path}"] + gimp_cmd[2:]
    else:
        env = {**os.environ, "CONVERT_JOB": str(job_path)}
    # -i no UI, -d no brushes/patterns; NOT -f: fonts are required for text layers
    cmd = gimp_cmd + ["-id", "--batch-interpreter=python-fu-eval",
                      "-b", f"exec(open({str(JOB_SCRIPT)!r}).read())", "--quit"]
    try:
        subprocess.run(cmd, capture_output=True, timeout=timeout, check=False, env=env)
    except subprocess.TimeoutExpired:
        lines = log_path.read_text().splitlines() if log_path.exists() else []
        return lines + [f"FATAL GIMP timed out after {timeout}s"]
    except OSError as e:
        return [f"FATAL could not start GIMP ({e}); set GIMP_CMD or install GIMPhoto (io.github.diegochagas.GIMPhoto)"]
    if not log_path.exists():
        return ["FATAL GIMP produced no log; is GIMPhoto (io.github.diegochagas.GIMPhoto) installed?"]
    return log_path.read_text().splitlines()


def node(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["node", "--max-old-space-size=8192", str(HERE / script), *args],
                          capture_output=True, text=True, cwd=HERE.parent)


# ------------------------------------------------------------------- main

def collect(src: Path, recursive: bool, to: str | None) -> list[Path]:
    want = (PSD_EXT if to == "xcf" else XCF_EXT if to == "psd" else PSD_EXT | XCF_EXT)
    if src.is_file():
        return [src] if src.suffix.lower() in PSD_EXT | XCF_EXT else []
    it = src.rglob("*") if recursive else src.iterdir()
    return sorted(p for p in it if p.is_file() and p.suffix.lower() in want and not p.name.startswith("."))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="a .psd/.psb/.xcf file, or a folder of them")
    ap.add_argument("--to", choices=["xcf", "psd"], help="only convert in this direction (default: both)")
    ap.add_argument("--recursive", action="store_true", help="include subfolders (structure is kept)")
    ap.add_argument("--output", help="output folder (default '<root>/<source name> converted')")
    ap.add_argument("--overwrite", action="store_true", help="redo files that already exist in the output folder")
    ap.add_argument("--preview", action="store_true", help="also write _preview/<file>.jpg, GIMP's render of the XCF side")
    ap.add_argument("--keep-raster", action="store_true",
                    help="PSD->XCF: keep Photoshop's rendering of each text as a hidden layer under the new text layer")
    ap.add_argument("--font-map", action="append", default=[], metavar="PSNAME=GIMP NAME",
                    help='PSD->XCF: use this GIMP font for a Photoshop font, e.g. "ArialMT=Liberation Sans Regular"')
    ap.add_argument("--keep-work", action="store_true", help="keep the .work-* folder (job, log, GIMP's raw PSD export) for debugging")
    ap.add_argument("--timeout", type=int, help="seconds per GIMP batch (default 120 + 90 per file)")
    a = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)          # progress per batch even when piped to a log

    src = Path(a.source).expanduser().resolve()
    if not src.exists():
        print(f"FAIL {src} does not exist")
        return 2
    files = collect(src, a.recursive, a.to)
    if not files:
        print(f"FAIL no .psd/.psb/.xcf to convert in {src}")
        return 2
    root = Path(os.environ.get("COMIC_OUTPUT_DIR") or Path.home() / "Downloads").expanduser()
    out_dir = Path(a.output).expanduser().resolve() if a.output else root / f"{src.stem if src.is_file() else src.name} converted"
    base = src.parent if src.is_file() else src
    if out_dir == base:
        print("FAIL the output folder is the source folder; pick another --output")
        return 2
    font_map = dict(m.split("=", 1) for m in a.font_map if "=" in m)
    for tool in ("node", "fc-list"):
        if not shutil.which(tool):
            print(f"FAIL {tool} not found in PATH")
            return 2
    if not (HERE.parent / "node_modules" / "ag-psd").exists():
        print(f"FAIL ag-psd is not installed: run {HERE.parent / 'setup.sh'}")
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)
    fonts = Fonts()
    todo = []
    for f in files:
        to_xcf = f.suffix.lower() in PSD_EXT
        out = out_dir / f.relative_to(base).with_suffix(".xcf" if to_xcf else ".psd")
        if out.exists() and not a.overwrite:
            print(f"SKIP {f.name}: {out.name} already in the output folder (--overwrite to redo)")
            continue
        todo.append((f, out, to_xcf))
    failed: list[str] = []
    done = 0
    # the work folder lives inside the output folder: a place flatpak GIMP can always reach
    work = Path(tempfile.mkdtemp(prefix=".work-", dir=out_dir))
    try:
        for start in range(0, len(todo), BATCH):
            chunk = todo[start:start + BATCH]
            tasks = []
            for i, (f, out, to_xcf) in enumerate(chunk):
                preview = str(out_dir / "_preview" / (out.relative_to(out_dir).as_posix().replace("/", "__") + ".jpg")) if a.preview else None
                if to_xcf:
                    info = work / f"{start + i}_info.json"
                    r = node("psd_text_info.mjs", str(f), str(info))
                    if r.returncode != 0:
                        failed.append(f.name)
                        print(f"FAIL {f.name}: could not read the PSD ({(r.stderr or r.stdout).strip().splitlines()[-1:]})")
                        continue
                    d = json.loads(info.read_text())
                    names = {run["font"] for l in d["layers"] if l.get("text") for run in l["text"]["runs"]}
                    tasks.append({"mode": "psd2xcf", "src": str(f), "info": str(info), "out": str(out), "preview": preview,
                                  "keep_raster": a.keep_raster, "fonts": {n: fonts.to_gimp(n, font_map) for n in names}})
                else:
                    tasks.append({"mode": "xcf2psd", "src": str(f), "out_psd": str(work / f"{start + i}_gimp.psd"),
                                  "out_info": str(work / f"{start + i}_info.json"), "preview": preview, "final": str(out)})
            if not tasks:
                continue
            lines = run_gimp(tasks, work, a.timeout or 120 + 90 * len(tasks))
            status: dict[str, list[str]] = {}
            last = None
            for line in lines:
                m = re.match(r"(OK|FAIL|NOTE) (.+?)(?: -> |: |$)", line)
                if m:
                    last = status.setdefault(m.group(2), [])
                    last.append(line)
                elif last and last[-1].startswith("FAIL") and line != "DONE":
                    last[-1] += " | " + line.strip()              # traceback lines of that FAIL
            if any(l.startswith("FATAL") for l in lines):
                print("\n".join(l for l in lines if not l.startswith(("OK", "NOTE", "DONE"))))
            for t in tasks:
                name = Path(t["src"]).name
                mine = status.get(t["src"], [])
                notes = [l.split(": ", 1)[1] for l in mine if l.startswith("NOTE")]
                ok = any(l.startswith("OK") for l in mine)
                summary = ""
                if ok and t["mode"] == "psd2xcf":
                    summary = next(l for l in mine if l.startswith("OK")).rsplit(": ", 1)[1]
                elif ok:
                    d = json.loads(Path(t["out_info"]).read_text())
                    for l in d["layers"]:
                        for run in (l.get("text") or {}).get("runs", []):
                            ps, fb, fi, note = fonts.to_ps(run, (l["text"].get("font_back") or {}))
                            run.update(font=ps, fauxBold=fb, fauxItalic=fi)
                            if note:
                                notes.append(note)
                    Path(t["out_info"]).write_text(json.dumps(d))
                    Path(t["final"]).parent.mkdir(parents=True, exist_ok=True)
                    r = node("write_psd_text.mjs", t["out_psd"], t["out_info"], t["final"])
                    try:
                        res = json.loads(r.stdout.strip().splitlines()[-1])
                    except (ValueError, IndexError):
                        err = [l for l in (r.stderr or r.stdout).strip().splitlines() if l.strip() and not l.lstrip().startswith("at ")]
                        res = {"problems": ["write_psd_text.mjs: " + " / ".join(err[-3:])[:400]]}
                    ok =r.returncode == 0 and not res.get("problems")
                    if not ok:
                        mine = mine + ["FAIL " + "; ".join(res.get("problems") or ["write_psd_text.mjs failed"])]
                    else:
                        notes += res.get("problems", [])
                    summary = f"{res.get('text', 0)} editable Type layer(s), {res.get('styled', 0)} layer(s) with Layer Styles"
                    if not a.keep_work:
                        Path(t["out_psd"]).unlink(missing_ok=True)
                        Path(t["out_psd"][:-4] + "_full.psd").unlink(missing_ok=True)
                target = Path(t.get("out") or t["final"])
                if ok:
                    done += 1
                    print(f"OK {name} -> {target.name}: {summary}")
                else:
                    failed.append(name)
                    target.unlink(missing_ok=True)
                    detail = [l for l in mine if l.startswith("FAIL")] or [l for l in lines if l.startswith(("FAIL", "FATAL"))][-1:]
                    print(f"FAIL {name}: " + (" | ".join(detail) or "GIMP did not report this file"))
                for n in dict.fromkeys(notes):
                    print(f"   note: {n}")
    finally:
        if a.keep_work:
            print(f"work folder kept: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)

    print(f"\n{done} converted, {len(failed)} failed, {len(files) - len(todo)} skipped -> {out_dir}")
    if failed:
        print("failed: " + ", ".join(failed))
    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())
