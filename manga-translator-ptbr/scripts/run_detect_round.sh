#!/usr/bin/env bash
# One time-boxed round of Mode-B detection + column merge over a folder.
# Re-run until it prints "ALL DONE" (each shell call is capped by the tool
# timeout, so this stops starting new pages after $BUDGET seconds).
#
#   SRC=<images dir> [OUT=~/Downloads/<images dir name>] [UP=<OUT>/work/up] [BUDGET=500] \
#   [COLW=300] [MINTILE=2048] [SPLIT=auto|1|0] [SPLIT_ARGS="--spread no"] manga-translator-ptbr/scripts/run_detect_round.sh
set -u
cd "$(dirname "$0")"   # manga-translator-ptbr/scripts
REPO="$(cd ../.. && pwd)"
PY="$REPO/venv/bin/python"; [ -x "$PY" ] || PY=python3
SRC=${SRC:?set SRC=<folder with the page images>}
OUT=${OUT:-${COMIC_OUTPUT_DIR:-$HOME/Downloads}/$(basename "$(cd "$SRC" && pwd)")}
UP=${UP:-$OUT/work/up}
BUDGET=${BUDGET:-500}; COLW=${COLW:-300}; MINTILE=${MINTILE:-2048}
mkdir -p "$OUT"
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
for img in "$SRC"/*.jpg "$SRC"/*.jpeg "$SRC"/*.png; do
  [ -e "$img" ] || continue
  stem=$(basename "${img%.*}")
  [ -e "$OUT/detect/${stem}_merged.json" ] && continue
  now=$(date +%s); (( now - start > BUDGET )) && { echo "BUDGET reached after $n pages"; exit 2; }
  python3 ./detect_blocks.py "$OUT" "$img" --apply-exif --upright-dir "$UP" --min-tile "$MINTILE" 2>&1 | grep -vE "Warn|setattr|return self" || echo "FAIL detect $stem"
  [ -e "$OUT/detect/${stem}_detect.json" ] && python3 ./merge_columns.py "$OUT/detect/${stem}_detect.json" --col-width "$COLW" 2>&1 | tail -1
  # the upright PNG is ~55 MB per 71 MP page: drop it now, ensure_upright.py recreates it at clean/build time
  [ "${KEEP_UP:-0}" = 1 ] || rm -f "$UP/${stem}_upright.png"
  n=$((n+1))
done
echo "ALL DONE"
