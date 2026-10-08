#!/usr/bin/env python3
"""Replace photos in Immich with their restored versions, through Immich's
API (never by touching the library folder, which would leave Immich's
database out of sync).

    immich_replace.py <folder> [--dry-run] [--only NAME]... [--log FILE]

For every image in <folder> (named exactly like the original in Immich -
finalize.py's output):
  1. the original asset is found by its original file name (a name that
     matches several live assets is skipped and reported);
  2. the restored file is uploaded (Immich files it by its storage template,
     from the capture date the file carries);
  3. it is added to every album the original is in and gets its favourite
     flag, rating and visibility;
  4. the original goes to Immich's TRASH (not deleted: restorable from the
     Trash page until Immich empties it, 30 days by default).
Every step is appended to the log (default <folder>/immich-replace.log.jsonl:
original id -> new id), so a run can be resumed and undone. --dry-run only
looks the originals up and prints the plan.

Settings: ~/.config/photo-restore/immich.env (outside the repo)
  IMMICH_URL=http://<server>:2283
  IMMICH_API_KEY=<key from Immich > Account Settings > API Keys; needs
                 asset.read, asset.upload, asset.update, asset.delete,
                 album.read, albumAsset.create>
"""
import argparse
import json
import mimetypes
import os
import sys
import re
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from pathlib import Path

CONFIG = Path.home() / ".config/photo-restore/immich.env"
EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}


def load_config():
    cfg = {}
    if CONFIG.exists():
        for line in CONFIG.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.strip().strip("'\"")
                if v:
                    cfg[k.strip()] = v
    url, key = os.environ.get("IMMICH_URL") or cfg.get("IMMICH_URL"), \
        os.environ.get("IMMICH_API_KEY") or cfg.get("IMMICH_API_KEY")
    if not url or not key:
        sys.exit(f"set IMMICH_URL and IMMICH_API_KEY in {CONFIG}")
    return url.rstrip("/"), key


class Immich:
    def __init__(self, url, key):
        self.url, self.key = url, key

    def call(self, method, path, body=None, raw=None, ctype=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.url + "/api" + path, data=data, method=method,
                                     headers={"x-api-key": self.key, "Accept": "application/json",
                                              "Content-Type": ctype or "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                txt = r.read().decode()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} {path} -> {e.code} {e.read().decode()[:300]}")
        return json.loads(txt) if txt else None

    def find(self, name):
        """Live assets whose library file is <name>. The library file can differ from
        Immich's originalFileName: a name uploaded twice is stored as "x (5)+1.jpg"
        with originalFileName "x (5).jpg", and accents may be composed differently."""
        stem, ext = os.path.splitext(name)
        queries = {name, re.sub(r"\+\d+$", "", stem) + ext}
        queries |= {unicodedata.normalize(f, q) for q in list(queries) for f in ("NFC", "NFD")}
        items = {}
        for q in queries:
            res = self.call("POST", "/search/metadata", {"originalFileName": q, "withDeleted": False,
                                                         "withExif": True, "size": 100})
            for a in res.get("assets", {}).get("items", []):
                if not a.get("isTrashed"):
                    items[a["id"]] = a
        nfc = lambda t: unicodedata.normalize("NFC", t)
        by_path = [a for a in items.values() if nfc(os.path.basename(a.get("originalPath", ""))) == nfc(name)]
        if by_path:
            return by_path
        return [a for a in items.values() if nfc(a.get("originalFileName", "")) == nfc(name)]

    def upload(self, path, like):
        b = uuid.uuid4().hex
        fields = {"deviceAssetId": f"photo-restore-{path.name}-{int(time.time())}",
                  "deviceId": "photo-restore",
                  "fileCreatedAt": like.get("fileCreatedAt"),
                  "fileModifiedAt": like.get("fileModifiedAt") or like.get("fileCreatedAt"),
                  "isFavorite": "true" if like.get("isFavorite") else "false"}
        parts = [f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
                 for k, v in fields.items() if v]
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"assetData\"; filename=\"{path.name}\"\r\n"
                     f"Content-Type: {mime}\r\n\r\n".encode() + path.read_bytes() + b"\r\n")
        parts.append(f"--{b}--\r\n".encode())
        return self.call("POST", "/assets", raw=b"".join(parts), ctype=f"multipart/form-data; boundary={b}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", action="append", help="file name to process (repeatable)")
    ap.add_argument("--log")
    a = ap.parse_args()
    folder = Path(os.path.expanduser(a.folder))
    log = Path(a.log) if a.log else folder / "immich-replace.log.jsonl"
    done = set()
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            e = json.loads(line)
            if e.get("step") == "trashed":
                done.add(e["file"])
    im = Immich(*load_config())
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in EXT)
    if a.only:
        files = [p for p in files if p.name in set(a.only)]
    ok = skipped = 0
    for i, f in enumerate(files, 1):
        tag = f"[{i}/{len(files)}] {f.name}"
        if f.name in done:
            print(f"{tag}: already replaced (log)")
            continue
        found = im.find(f.name)
        if len(found) != 1:
            print(f"{tag}: SKIP - {len(found)} live assets with this name")
            skipped += 1
            continue
        orig = found[0]
        albums = im.call("GET", f"/albums?assetId={orig['id']}") or []
        plan = f"{orig['originalPath']} -> trash; albums: {[x['albumName'] for x in albums]}"
        if a.dry_run:
            print(f"{tag}: {plan}")
            ok += 1
            continue
        res = im.upload(f, orig)
        new_id = res["id"]
        if res.get("status") == "duplicate":
            print(f"{tag}: SKIP - Immich says the restored file is already uploaded as {new_id}")
            skipped += 1
            continue
        with log.open("a", encoding="utf-8") as L:
            L.write(json.dumps({"file": f.name, "step": "uploaded", "original": orig["id"], "new": new_id,
                                "originalPath": orig["originalPath"]}, ensure_ascii=False) + "\n")
        for al in albums:
            im.call("PUT", f"/albums/{al['id']}/assets", {"ids": [new_id]})
        upd = {"ids": [new_id]}
        if orig.get("isFavorite"):
            upd["isFavorite"] = True
        if orig.get("visibility") and orig["visibility"] != "timeline":
            upd["visibility"] = orig["visibility"]
        rating = (orig.get("exifInfo") or {}).get("rating")
        if rating:
            upd["rating"] = rating
        if len(upd) > 1:
            im.call("PUT", "/assets", upd)
        im.call("DELETE", "/assets", {"ids": [orig["id"]], "force": False})
        with log.open("a", encoding="utf-8") as L:
            L.write(json.dumps({"file": f.name, "step": "trashed", "original": orig["id"], "new": new_id},
                               ensure_ascii=False) + "\n")
        print(f"{tag}: replaced ({plan}), new asset {new_id}", flush=True)
        ok += 1
    print(f"{'would replace' if a.dry_run else 'replaced'} {ok}, skipped {skipped}")


if __name__ == "__main__":
    main()
