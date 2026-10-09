#!/usr/bin/env bash
# One time-boxed round of the Mode-B folder pipeline, after the translations
# (tr/<stem>.json) are written:
#   ensure_upright -> assemble_translation -> clean_blocks (LaMa, resumable)
#   -> build_translated_psd -> verify -> preview -> cleanup of big PNGs
# Re-run until it prints "ALL DONE". Each stage is skipped when its output
# exists, and clean_blocks saves a pending mask when the budget runs out, so a
# page can span several shell calls.
#
#   OUT=<out dir with detect/ and tr/ - the one run_detect_round.sh used,
#   ~/Downloads/<images dir name> by default> [WORK=<OUT>/work] [BUDGET=150] \
#   [FORMAT=psd|xcf] [PAGES="stem1 stem2"] manga-translator-ptbr/scripts/run_build_round.sh
# FORMAT=xcf builds <OUT>/<stem>.xcf with native GIMP text layers via
# build_translated_xcf.py (headless flatpak GIMP; it verifies by reloading
# the file and renders the preview itself) instead of the PSD.
set -u
cd "$(dirname "$0")"   # manga-translator-ptbr/scripts
OUT=${OUT:?set OUT=<out dir with detect/ and tr/>}
WORK=${WORK:-$OUT/work}
BUDGET=${BUDGET:-150}
FORMAT=${FORMAT:-psd}
start=$(date +%s)
left() { echo $(( BUDGET - ($(date +%s) - start) )); }
mkdir -p "$WORK/final" "$WORK/up" "$OUT/preview" "$OUT/final"
stems=${PAGES:-$(ls "$OUT"/tr/*.json | xargs -n1 basename | sed 's/\.json$//' | sort -V)}
for stem in $stems; do
  [ -e "$OUT/final/${stem}.ok" ] && continue
  (( $(left) < 20 )) && { echo "BUDGET reached"; exit 2; }
  merged="$OUT/detect/${stem}_merged.json"
  blocks="$WORK/final/${stem}_blocks.json"
  det="$WORK/final/${stem}_detect.json"
  cleaned="$WORK/final/${stem}_cleaned.png"
  pending="$WORK/final/${stem}_pending_mask.png"
  if [ ! -e "$blocks" ]; then
    python3 ./assemble_translation.py "$merged" "$OUT/tr/${stem}.json" "$blocks" || { echo "FAIL assemble $stem"; continue; }
    cp "$blocks" "$det"
  fi
  src=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('source_for_psd') or d['source'])" "$blocks")
  python3 ./ensure_upright.py "$blocks" 2>&1 | grep -v Warn || true
  if [ ! -e "$cleaned" ] || [ -e "$pending" ]; then
    b=$(( $(left) - 25 )); (( b < 30 )) && { echo "BUDGET reached (before clean $stem)"; exit 2; }
    python3 ./clean_blocks.py "$det" --budget "$b" 2>&1 | grep -vE "Warn|setattr|return self"
    [ -e "$pending" ] && { echo "PARTIAL $stem -> rerun"; exit 2; }
    [ -e "$cleaned" ] || { echo "FAIL clean $stem"; continue; }
  fi
  (( $(left) < 40 )) && { echo "BUDGET reached (before build $stem)"; exit 2; }
  # pages that came out of split-scans: the page as scanned is the Original layer
  orig=(); [ -e "$OUT/split/originals/$stem.png" ] && orig=(--original "$OUT/split/originals/$stem.png")
  if [ "$FORMAT" = xcf ]; then
    python3 ./build_translated_xcf.py "$src" "$blocks" "$WORK/final/${stem}.xcf" --copy-image "$cleaned" --preview "$OUT/preview/${stem}.jpg" || { echo "FAIL build/verify $stem"; rm -f "$WORK/final/${stem}.xcf"; continue; }
  else
    node --max-old-space-size=3072 ./build_translated_psd.mjs "$src" "$blocks" "$WORK/final/${stem}.psd" --copy-image "$cleaned" "${orig[@]}" || { echo "FAIL build $stem"; continue; }
    node --max-old-space-size=3072 ./verify_translated_psd.mjs "$WORK/final/${stem}.psd" "$src" "$blocks" --copy-image "$cleaned" "${orig[@]}" || { echo "FAIL verify $stem"; rm -f "$WORK/final/${stem}.psd"; continue; }
    python3 ./preview_psd_text.py "$WORK/final/${stem}.psd" "$cleaned" "$OUT/preview/${stem}.jpg" --max 1400 >/dev/null 2>&1 || echo "WARN preview $stem"
  fi
  cp -f "$WORK/final/${stem}.$FORMAT" "$OUT/${stem}.$FORMAT" && rm -f "$WORK/final/${stem}.$FORMAT" || { echo "FAIL copy $stem"; continue; }
  cp -f "$blocks" "$OUT/final/${stem}_blocks.json"
  cp -f "$WORK/final/${stem}_clean_overlay.jpg" "$OUT/final/" 2>/dev/null
  touch "$OUT/final/${stem}.ok"
  rm -f "$cleaned" "$WORK/up/${stem}_upright.png" "$WORK/final/${stem}_clean_overlay.jpg"
  echo "DONE $stem ($(du -h "$OUT/${stem}.$FORMAT" | cut -f1))"
done
echo "ALL DONE"
