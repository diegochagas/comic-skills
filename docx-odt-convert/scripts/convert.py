#!/usr/bin/env python3
"""Convert Word .docx/.doc documents to LibreOffice .odt and .odt back to .docx,
through a private headless LibreOffice, fixing what a straight conversion
leaves broken:

  - every "Heading N" style that carries no outline level gets level N (files
    written by generators such as the `docx` npm package have none, and
    LibreOffice's table of contents then comes out empty);
  - every index (table of contents, ...) is updated after layout, twice, so
    the TOC lists the chapters WITH page numbers (a generated .docx only has an
    empty TOC field until someone updates it by hand in Word);
  - optional --drop-paragraph PREFIX removes hint paragraphs such as
    '(No Word, clique ... "Atualizar campo" ...)'.

Usage:
  python3 convert.py <file-or-folder> [--to odt|docx] [--recursive]
        [--output DIR | --in-place [--trash]] [--overwrite] [--drop-paragraph PREFIX]...

Output: <root>/<source name> converted/, <root> = $COMIC_OUTPUT_DIR or
~/Downloads; --output replaces the whole path. Inputs are never modified,
except with --in-place: the new file is written next to its source (for a
document library the user wants switched to the other format) and --trash then
moves the source to the desktop trash (gio trash) once the new file exists.

Needs LibreOffice (`soffice`) and its Python binding `uno` (Debian/Ubuntu
package python3-uno), which only the SYSTEM python3 has: run with python3, not
with the repo venv (the script re-launches itself with /usr/bin/python3 when
started from an interpreter without uno). An open LibreOffice window is not
disturbed: the conversion uses its own temporary user profile.
Exit status 0 when every file converted, 2 otherwise.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    import uno
    from com.sun.star.beans import PropertyValue
except ImportError:
    if sys.executable != "/usr/bin/python3" and Path("/usr/bin/python3").exists():
        os.execv("/usr/bin/python3", ["/usr/bin/python3", *sys.argv])
    print("FAIL the LibreOffice Python binding (uno) is missing: sudo apt install python3-uno")
    sys.exit(2)

TO_ODT = (".docx", ".doc")
TO_DOCX = (".odt",)
FILTER = {"odt": "writer8", "docx": "MS Word 2007 XML"}


def prop(name, value):
    p = PropertyValue()
    p.Name, p.Value = name, value
    return p


class Office:
    """A private headless LibreOffice reached through a named pipe."""

    def __init__(self):
        self.profile = tempfile.mkdtemp(prefix="docx-odt-convert-profile-")
        pipe = "docx_odt_convert_%d" % os.getpid()
        self.proc = subprocess.Popen(
            ["soffice", "--headless", "--invisible", "--nologo", "--norestore", "--nodefault",
             "-env:UserInstallation=" + Path(self.profile).as_uri(),
             "--accept=pipe,name=%s;urp;StarOffice.ComponentContext" % pipe],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        local = uno.getComponentContext()
        resolver = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
        for _ in range(120):
            try:
                ctx = resolver.resolve("uno:pipe,name=%s;urp;StarOffice.ComponentContext" % pipe)
                break
            except Exception:
                time.sleep(0.5)
        else:
            self.close()
            raise RuntimeError("LibreOffice did not start")
        self.desktop = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)

    def close(self):
        try:
            self.desktop.terminate()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=30)
        except Exception:
            self.proc.kill()
        shutil.rmtree(self.profile, ignore_errors=True)

    def convert(self, src: Path, dst: Path, fmt: str, drop: list[str]) -> str:
        doc = self.desktop.loadComponentFromURL(src.as_uri(), "_blank", 0, (prop("Hidden", True),))
        if doc is None:
            raise RuntimeError("LibreOffice could not open the file")
        try:
            dropped = 0
            if drop:
                victims = []
                enum = doc.Text.createEnumeration()
                while enum.hasMoreElements():
                    par = enum.nextElement()
                    if par.supportsService("com.sun.star.text.Paragraph") and any(par.getString().startswith(p) for p in drop):
                        victims.append(par)
                for par in victims:
                    par.dispose()
                dropped = len(victims)
            fixed = 0
            styles = doc.getStyleFamilies().getByName("ParagraphStyles")
            for n in range(1, 7):
                name = "Heading %d" % n
                if styles.hasByName(name) and styles.getByName(name).OutlineLevel == 0:
                    styles.getByName(name).OutlineLevel = n
                    fixed += 1
            idx = doc.getDocumentIndexes()
            for _ in range(2):  # the second pass settles page numbers once the TOC itself has taken its pages
                for i in range(idx.getCount()):
                    idx.getByIndex(i).update()
                doc.refresh()
            ctrl = doc.getCurrentController()
            pages = ctrl.getPropertyValue("PageCount") if ctrl else "?"
            tmp = dst.with_name(dst.name + ".part")
            doc.storeToURL(tmp.as_uri(), (prop("FilterName", FILTER[fmt]),))
            os.replace(tmp, dst)
            notes = ["%s pages" % pages]
            if idx.getCount():
                notes.append("%d index(es) updated" % idx.getCount())
            if fixed:
                notes.append("outline level set on %d heading style(s)" % fixed)
            if dropped:
                notes.append("%d paragraph(s) dropped" % dropped)
            return ", ".join(notes)
        finally:
            doc.close(True)


def collect(src: Path, recursive: bool, to: str | None) -> list[Path]:
    exts = (TO_ODT if to != "docx" else ()) + (TO_DOCX if to != "odt" else ())
    if src.is_file():
        return [src] if src.suffix.lower() in exts else []
    it = src.rglob("*") if recursive else src.glob("*")
    return sorted(p for p in it if p.is_file() and p.suffix.lower() in exts and not p.name.startswith((".~lock", "~$")))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="a .docx/.doc/.odt file, or a folder of them")
    ap.add_argument("--to", choices=["odt", "docx"], help="only convert in this direction (default: .docx/.doc -> .odt and .odt -> .docx)")
    ap.add_argument("--recursive", action="store_true", help="include subfolders (structure is kept)")
    ap.add_argument("--output", help="output folder (default '<root>/<source name> converted')")
    ap.add_argument("--in-place", action="store_true", help="write each new file next to its source instead of a new folder")
    ap.add_argument("--trash", action="store_true", help="with --in-place: move each source to the trash after a successful conversion")
    ap.add_argument("--overwrite", action="store_true", help="redo files whose converted copy already exists")
    ap.add_argument("--drop-paragraph", action="append", default=[], metavar="PREFIX",
                    help="remove every paragraph that starts with this text (repeatable)")
    a = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)

    if a.trash and not a.in_place:
        print("FAIL --trash only goes with --in-place")
        return 2
    if a.in_place and a.output:
        print("FAIL use either --in-place or --output")
        return 2
    if not shutil.which("soffice"):
        print("FAIL soffice (LibreOffice) not found in PATH")
        return 2
    src = Path(a.source).expanduser().resolve()
    if not src.exists():
        print(f"FAIL {src} does not exist")
        return 2
    files = collect(src, a.recursive, a.to)
    if not files:
        print(f"FAIL no .docx/.doc/.odt to convert in {src}")
        return 2
    base = src.parent if src.is_file() else src
    if a.in_place:
        out_dir = None
    else:
        root = Path(os.environ.get("COMIC_OUTPUT_DIR") or Path.home() / "Downloads").expanduser()
        out_dir = Path(a.output).expanduser().resolve() if a.output else root / f"{src.stem if src.is_file() else src.name} converted"
        if out_dir == base:
            print("FAIL the output folder is the source folder; use --in-place for that")
            return 2

    jobs = []
    for f in files:
        fmt = "docx" if f.suffix.lower() in TO_DOCX else "odt"
        folder = f.parent if out_dir is None else out_dir / f.parent.relative_to(base)
        dst = folder / (f.stem + "." + fmt)
        if dst.exists() and not a.overwrite:
            print(f"skip {f.name}: {dst} exists (--overwrite to redo)")
            continue
        jobs.append((f, dst, fmt))
    failed = []
    if jobs:
        office = Office()
        try:
            for i, (f, dst, fmt) in enumerate(jobs, 1):
                dst.parent.mkdir(parents=True, exist_ok=True)
                t = time.time()
                try:
                    info = office.convert(f, dst, fmt, a.drop_paragraph)
                except Exception as e:
                    failed.append(f)
                    print(f"[{i}/{len(jobs)}] FAIL {f.name}: {e}")
                    continue
                print(f"[{i}/{len(jobs)}] {f.name} -> {dst.name} ({info}, {time.time() - t:.0f}s)")
                if a.trash and dst.exists() and dst.stat().st_size > 0:
                    r = subprocess.run(["gio", "trash", str(f)], capture_output=True, text=True)
                    if r.returncode:
                        print(f"    could not trash {f.name}: {r.stderr.strip()}")
        finally:
            office.close()
    where = "next to the sources" if out_dir is None else str(out_dir)
    print(f"\n{len(jobs) - len(failed)} converted, {len(failed)} failed, {len(files) - len(jobs)} skipped -> {where}")
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
