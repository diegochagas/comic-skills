#!/usr/bin/env python3
"""Give chosen restored photos the metadata of their originals, plus what
the file names say, ready to go back into the photo library.

    finalize.py <list.json> --output DIR [--names FILE] [--tag Restaurada]

<list.json> is a list of {"chosen": <restored file>, "original": <scan>}
(the scan as it is in the library; its "<scan>.xmp" sidecar is read too
when it exists). Each result is DIR/<original file name> (same name and format as the scan):

  1. every tag of the original and of its sidecar is copied (dates, camera
     or scanner, GPS, keywords, description), except the image geometry;
  2. from the file name "YYYY-MM-DD <title> (n)":
       - the date becomes DateTimeOriginal/CreateDate (noon) only when the
         original has none;
       - <title> becomes the title and, when there is none, the description;
       - people listed in the names file become keywords and PersonInImage;
       - a place listed there sets City/State/Country/Location and, when the
         original has no GPS, the place's approximate (city-level) GPS;
  3. the tag --tag (default "Restaurada") is added to the keywords, so the
     restored copies can be found in the library.

Names file (default ~/.config/photo-restore/names.json, kept outside the repo):
  {"people": ["Ana", ...],
   "places": {"<text in file names>": {"city": "", "state": "", "country": "",
              "lat": 0.0, "lon": 0.0, "location": ""}}}
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

NAMES = Path.home() / ".config/photo-restore/names.json"
GEOMETRY = ["ImageWidth", "ImageHeight", "ExifImageWidth", "ExifImageHeight", "Orientation",
            "ThumbnailImage", "PreviewImage"]


def parse_name(stem):
    """(date 'YYYY:MM:DD' or None, title) from 'YYYY-MM-DD title (3)+1'."""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})\s*(.*)$", stem)
    date, rest = (f"{m.group(1)}:{m.group(2)}:{m.group(3)}", m.group(4)) if m else (None, stem)
    title = re.sub(r"\s*\(\s*\d+\s*\)(\+\d+)?\s*$", "", rest).strip()
    return date, title


def read_tags(path):
    out = subprocess.run(["exiftool", "-j", "-n", "-DateTimeOriginal", "-GPSLatitude", "-Description",
                          "-ImageDescription", "-Title", str(path)], capture_output=True, text=True)
    try:
        return json.loads(out.stdout)[0]
    except (ValueError, IndexError):
        return {}


def original_offset(original, sidecar):
    for src in ([sidecar] if sidecar.exists() else []) + [Path(original)]:
        out = subprocess.run(["exiftool", "-j", "-OffsetTimeOriginal", "-XMP-exif:DateTimeOriginal",
                              "-XMP-photoshop:DateCreated", str(src)], capture_output=True, text=True)
        try:
            t = json.loads(out.stdout)[0]
        except (ValueError, IndexError):
            continue
        if t.get("OffsetTimeOriginal"):
            return t["OffsetTimeOriginal"]
        for k in ("DateTimeOriginal", "DateCreated"):
            m = re.search(r"([+-]\d{2}:\d{2})$", str(t.get(k, "")))
            if m:
                return m.group(1)
    return None


def finalize(chosen, original, dst, names, tag):
    if dst.suffix.lower() in (".jpg", ".jpeg"):
        shutil.copy2(chosen, dst)
    else:   # keep the original's format (a .png scan stays .png), so names never clash
        import cv2
        cv2.imwrite(str(dst), cv2.imread(str(chosen), cv2.IMREAD_COLOR))
    args = ["exiftool", "-q", "-m", "-overwrite_original", "-P",
            "-TagsFromFile", str(original), "-all:all", "-unsafe", "-icc_profile"]
    args += [f"--{t}" for t in GEOMETRY]
    sidecar = Path(str(original) + ".xmp")
    if sidecar.exists():
        args += ["-TagsFromFile", str(sidecar), "-xmp:all"]
    subprocess.run(args + [str(dst)], check=True)

    # the capture time's UTC offset: EXIF OffsetTime* or, from Immich's sidecar,
    # the offset of its XMP DateTimeOriginal/DateCreated - without it Immich
    # reads the time as UTC and shifts the photo by hours
    offset = original_offset(original, sidecar)
    if offset:
        subprocess.run(["exiftool", "-q", "-m", "-overwrite_original", "-P", f"-OffsetTimeOriginal={offset}",
                        f"-OffsetTimeDigitized={offset}", f"-OffsetTime={offset}", str(dst)], check=True)

    have = read_tags(dst)
    date, title = parse_name(Path(original).stem)
    edits = []
    if date and not have.get("DateTimeOriginal"):
        edits += [f"-DateTimeOriginal={date} 12:00:00", f"-CreateDate={date} 12:00:00"]
    if title:
        edits += [f"-XMP-dc:Title={title}", f"-IPTC:ObjectName={title}"]
        if not (have.get("Description") or have.get("ImageDescription")):
            edits += [f"-XMP-dc:Description={title}", f"-EXIF:ImageDescription={title}",
                      f"-IPTC:Caption-Abstract={title}"]
    words = f" {title} "
    people = [p for p in names.get("people", []) if re.search(rf"\b{re.escape(p)}\b", words)]
    for p in people:
        edits += [f"-XMP-dc:Subject-={p}", f"-XMP-dc:Subject+={p}",
                  f"-XMP-iptcExt:PersonInImage-={p}", f"-XMP-iptcExt:PersonInImage+={p}"]
    for key, place in names.get("places", {}).items():
        if key.lower() in words.lower():
            for field, tags in (("city", ["XMP-photoshop:City", "IPTC:City"]),
                                ("state", ["XMP-photoshop:State", "IPTC:Province-State"]),
                                ("country", ["XMP-photoshop:Country", "IPTC:Country-PrimaryLocationName"]),
                                ("location", ["XMP-iptcCore:Location", "IPTC:Sub-location"])):
                if place.get(field):
                    edits += [f"-{t}={place[field]}" for t in tags]
            if "lat" in place and not have.get("GPSLatitude"):
                lat, lon = place["lat"], place["lon"]
                edits += [f"-GPSLatitude={abs(lat)}", f"-GPSLatitudeRef={'S' if lat < 0 else 'N'}",
                          f"-GPSLongitude={abs(lon)}", f"-GPSLongitudeRef={'W' if lon < 0 else 'E'}"]
            break
    if tag:
        edits += [f"-XMP-dc:Subject-={tag}", f"-XMP-dc:Subject+={tag}",
                  f"-XMP-digiKam:TagsList-={tag}", f"-XMP-digiKam:TagsList+={tag}"]
    if edits:
        subprocess.run(["exiftool", "-q", "-m", "-overwrite_original", "-P", "-codedcharacterset=utf8"]
                       + edits + [str(dst)], check=True)
    return {"date_from_name": bool(date and not have.get("DateTimeOriginal")), "title": title,
            "people": people}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("list")
    ap.add_argument("--output", required=True)
    ap.add_argument("--names", default=str(NAMES))
    ap.add_argument("--tag", default="Restaurada", help="keyword added to every result ('' for none)")
    a = ap.parse_args()
    if not shutil.which("exiftool"):
        sys.exit("exiftool is required (apt install libimage-exiftool-perl)")
    names = json.loads(Path(a.names).read_text(encoding="utf-8")) if Path(a.names).exists() else {}
    out = Path(os.path.expanduser(a.output))
    out.mkdir(parents=True, exist_ok=True)
    items = json.loads(Path(a.list).read_text(encoding="utf-8"))
    for i, it in enumerate(items, 1):
        dst = out / Path(it["original"]).name
        info = finalize(it["chosen"], it["original"], dst, names, a.tag)
        print(f"[{i}/{len(items)}] {dst.name}  title='{info['title']}' people={info['people']}"
              + ("  date from name" if info["date_from_name"] else ""), flush=True)


if __name__ == "__main__":
    main()
