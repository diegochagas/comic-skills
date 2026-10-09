#!/usr/bin/env bash
# One time-boxed round of the placeholder (letter-ready) mode over a folder:
#   detect_text.py (page-scale detect + erase) -> build_translated_psd.mjs
#   --placeholder --copy-image  ->  <OUT>/<stem>.psd (Original + Copy + Lorem
#   ipsum "Text N" boxes) + <OUT>/preview/<stem>.jpg
# FORMAT=xcf writes <OUT>/<stem>.xcf with native GIMP text layers instead
# (build_translated_xcf.py, headless flatpak GIMP; the preview is rendered
# by GIMP itself).
# Re-run until it prints "ALL DONE" (each shell call may be capped by a tool
# timeout, so it stops starting new pages after $BUDGET seconds). Pages that
# already have a PSD are skipped; a page with zero blocks still gets a PSD.
#
#   SRC=<images dir> [OUT=~/Downloads/<images dir name>] [FORMAT=psd|xcf] [BUDGET=500] \
#   [PAGES="stem1 stem2"] [SPLIT=auto|1|0] [SPLIT_ARGS="--spread no"] manga-translator-ptbr/scripts/run_letter_round.sh
set -u
cd "$(dirname "$0")"   # manga-translator-ptbr/scripts
REPO="$(cd ../.. && pwd)"
PY="$REPO/venv/bin/python"; [ -x "$PY" ] || PY=python3
SRC=${SRC:?set SRC=<folder with the page images>}
OUT=${OUT:-${COMIC_OUTPUT_DIR:-$HOME/Downloads}/$(basename "$(cd "$SRC" && pwd)")}   # results never go into the source folder
BUDGET=${BUDGET:-500}
FORMAT=${FORMAT:-psd}
mkdir -p "$OUT/detect" "$OUT/preview"
# NNN-MMM scans (two facing pages in one file) are split into one flattened,
# gutter-fixed page each by the split-scans skill before anything else:
# SPLIT=auto (default) when SRC holds such names, SPLIT=1 always, SPLIT=0 never;
# SPLIT_ARGS passes flags ("--spread no"; "--rtl" only for scans numbered in reading order).
SPLIT=${SPLIT:-auto}
if [ "$SPLIT" != 0 ] && { [ "$SPLIT" = 1 ] || ls "$SRC"/[0-9]*-[0-9]*.* >/dev/null 2>&1; }; then
  if [ ! -e "$OUT/split/report.jsonl" ] || [ "${SPLIT_FORCE:-0}" = 1 ]; then
    echo "== split-scans: $SRC -> $OUT/split (${SPLIT_ARGS:-no flags})"
    "$PY" "$REPO/split-scans/scripts/split_scans.py" "$SRC" --output "$OUT/split" --also-images ${SPLIT_ARGS:-} || { echo "FAIL split"; exit 1; }
  fi
  SRC="$OUT/split/images"
  ORIGINALS="$OUT/split/originals"
fi
ORIGINALS=${ORIGINALS:-}
start=$(date +%s); n=0
imgs=()
if [ -n "${PAGES:-}" ]; then
  for s in $PAGES; do for e in jpg jpeg png; do [ -e "$SRC/$s.$e" ] && imgs+=("$SRC/$s.$e"); done; done
else
  for img in "$SRC"/*.jpg "$SRC"/*.jpeg "$SRC"/*.png; do [ -e "$img" ] && imgs+=("$img"); done
fi
for img in "${imgs[@]}"; do
  stem=$(basename "${img%.*}")
  [ -e "$OUT/$stem.$FORMAT" ] && continue
  now=$(date +%s); (( now - start > BUDGET )) && { echo "BUDGET reached after $n pages"; exit 2; }
  if [ ! -e "$OUT/detect/${stem}_cleaned.png" ]; then
    "$PY" ./detect_text.py "$OUT" "$img" 2>&1 | grep -vE "Warn|setattr|return self" || { echo "FAIL detect $stem"; continue; }
  fi
  [ -e "$OUT/detect/${stem}_detect.json" ] || { echo "FAIL detect $stem (no json)"; continue; }
  if [ "$FORMAT" = xcf ]; then
    python3 ./build_translated_xcf.py "$img" "$OUT/detect/${stem}_detect.json" "$OUT/$stem.xcf" \
      --copy-image "$OUT/detect/${stem}_cleaned.png" --placeholder --preview "$OUT/preview/$stem.jpg" || { echo "FAIL build $stem"; rm -f "$OUT/$stem.xcf"; continue; }
  else
    orig=(); [ -n "$ORIGINALS" ] && [ -e "$ORIGINALS/$stem.png" ] && orig=(--original "$ORIGINALS/$stem.png")
    node --max-old-space-size=3072 ./build_translated_psd.mjs "$img" "$OUT/detect/${stem}_detect.json" "$OUT/$stem.psd" \
      --copy-image "$OUT/detect/${stem}_cleaned.png" --placeholder "${orig[@]}" || { echo "FAIL build $stem"; rm -f "$OUT/$stem.psd"; continue; }
    "$PY" ./preview_psd_text.py "$OUT/$stem.psd" "$OUT/detect/${stem}_cleaned.png" "$OUT/preview/$stem.jpg" --max 1400 >/dev/null 2>&1 || echo "WARN preview $stem"
  fi
  echo "DONE $stem"
  n=$((n+1))
done
echo "ALL DONE"
