#!/usr/bin/env node
// Builds, in ONE pass (no GIMP, no PSD re-reads), a translation-ready PSD from
// a source image plus a blocks JSON:
//
//   layer "Original"  – the untouched source image (bottom)
//   layer "Copy"      – an identical copy (the letterer's working raster)
//   layer "Text N"    – one native Photoshop *paragraph* Type layer per text
//                       block, positioned/sized exactly over the block and
//                       pre-filled with that block's translation
//
// Usage:
//   node build_translated_psd.mjs <source_image> <blocks.json> <out.psd>
//                                 [--font CCWildWords-Regular] [--no-copy]
//                                 [--copy-image <png>] [--placeholder [text]]
//
// --placeholder: letter-ready mode (no translation) - every block without a
//   text gets "Lorem ipsum ..." (or the given text) in a modest font size
//   (box height / 7 lines, 10-32 px) so the letterer sees the lettering font
//   and just selects-and-types over it. blocks.json can be detect_text.py's
//   <stem>_detect.json directly (it has text_blocks and no texts).
//
// blocks.json (written by detect_blocks.py, then enriched with translations):
//   {
//     "text_blocks": [[x, y, w, h], ...],          // page px
//     "texts":       ["Tradução 1", ...],           // same order (optional)
//     "styles":      [{"rotate": 90, "color": "#ffffff", "size": 48,
//                      "align": "left", "font": "Arial"}, ...]  // optional, per block
//   }
// Any block without a text gets an empty box (still a real Type layer).
// Per-block style keys are all optional:
//   rotate  0 | 90 | -90 | 180   – rotates the text box around its centre
//                                   (90 = reads top-to-bottom, like a spine)
//   color   "#rrggbb"            – default: auto (black on light blocks,
//                                   white on dark blocks, sampled from the image)
//   size    px                   – default: auto-fit the text into the box
//   align   left|center|right    – default center
//   font    PostScript name      – default --font
//
// Memory: a 7000x10000 page is ~290 MB per raster layer; run with
// `node --max-old-space-size=4096` for scans that big.

import * as fs from 'fs';
import * as path from 'path';
import { writePsdBuffer } from 'ag-psd';
import { loadImage, createCanvas } from 'canvas';

const args = process.argv.slice(2);
const pos = args.filter((a) => !a.startsWith('--'));
const opt = (name, dflt) => { const i = args.indexOf(name); return i !== -1 ? args[i + 1] : dflt; };
const [srcPath, blocksPath, outPath] = pos;
if (!srcPath || !blocksPath || !outPath) {
  console.error('Usage: node build_translated_psd.mjs <source_image> <blocks.json> <out.psd> [--font NAME] [--no-copy] [--copy-image <png>] [--placeholder [text]]');
  process.exit(1);
}
const FONT_NAME = opt('--font', 'CCWildWords-Regular');
const WITH_COPY = !args.includes('--no-copy');
// --copy-image <png>: use this image (e.g. detect_text.py's *_cleaned.png)
// for the "Copy" layer instead of a pixel-identical duplicate of the source.
const COPY_IMAGE = opt('--copy-image', null);
// --placeholder [text]: fill blocks that have no text with placeholder text
const PH_IDX = args.indexOf('--placeholder');
const PLACEHOLDER = PH_IDX === -1 ? null
  : (args[PH_IDX + 1] && !args[PH_IDX + 1].startsWith('--') && !pos.includes(args[PH_IDX + 1])
      ? args[PH_IDX + 1] : 'Lorem ipsum dolor sit amet, consectetur adipiscing elit.');
const MIN_FONT = 8;
// Photoshop's text language (Character panel; drives spell check and
// hyphenation): Adobe text engine code 11 = "Portuguese: Brazilian" (10 =
// Portuguese, 0 = English: USA, which is what a layer gets when it is unset).
const LANGUAGE_PT_BR = 11;
// placeholder boxes: box height / 7 lines, clamped (same heuristic the old
// lettering pipeline used) - auto-fit would blow Lorem ipsum up to fill the box
const placeholderSize = (h) => Math.max(10, Math.min(32, Math.round(h / 7)));

function hexToRgb(hex) {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
  return m ? { r: parseInt(m[1], 16), g: parseInt(m[2], 16), b: parseInt(m[3], 16) } : { r: 0, g: 0, b: 0 };
}

// Mean luminance of the block area (sub-sampled) -> pick black or white text.
function autoColor(px, W, x, y, w, h) {
  let sum = 0, n = 0;
  const step = Math.max(1, Math.floor(Math.sqrt((w * h) / 4000)));
  for (let yy = y; yy < y + h; yy += step) {
    for (let xx = x; xx < x + w; xx += step) {
      const i = (yy * W + xx) * 4;
      sum += 0.299 * px[i] + 0.587 * px[i + 1] + 0.114 * px[i + 2];
      n++;
    }
  }
  const lum = n ? sum / n : 255;
  return lum < 110 ? { r: 255, g: 255, b: 255 } : { r: 0, g: 0, b: 0 };
}

// Largest font size (px) at which `text` word-wraps into a w x h box.
// Uses an average glyph advance of 0.55 em and 1.15 line height.
function fitFontSize(text, w, h) {
  const words = (text || '').split(/\s+/).filter(Boolean);
  if (!words.length) return Math.max(MIN_FONT, Math.min(72, Math.round(h * 0.6)));
  const paraLines = (text || '').split('\n');
  let size = Math.floor(Math.min(h * 0.85, w));
  for (; size >= MIN_FONT; size--) {
    const adv = size * 0.55, lineH = size * 1.15;
    let lines = 0;
    for (const p of paraLines) {
      let cur = 0; lines++;
      for (const wd of p.split(/\s+/).filter(Boolean)) {
        const ww = (wd.length + 1) * adv;
        if (wd.length * adv > w) { lines += Math.ceil((wd.length * adv) / w) - 1; cur = 0; continue; }
        if (cur + ww > w && cur > 0) { lines++; cur = ww; } else cur += ww;
      }
    }
    if (lines * lineH <= h) break;
  }
  return Math.max(MIN_FONT, size);
}

// Photoshop text transform [a b c d tx ty]: page = [a c; b d] * local + (tx, ty).
// The text box lives in local space as [0,0,bw,bh] (same convention as
// list_text_layers.mjs), so (tx,ty) is where the box's
// local origin lands on the page:
//   0    reads left-to-right           origin = block top-left
//   90   reads top-to-bottom (spines)  origin = block top-right
//   -90  reads bottom-to-top           origin = block bottom-left
//   180  upside down                   origin = block bottom-right
function rotMatrix(rot, x, y, w, h) {
  switch (rot) {
    case 90: return [0, 1, -1, 0, x + w, y];
    case -90: return [0, -1, 1, 0, x, y + h];
    case 180: return [-1, 0, 0, -1, x + w, y + h];
    default: return [1, 0, 0, 1, x, y];
  }
}

async function main() {
  const img = await loadImage(srcPath);
  const W = img.width, H = img.height;
  const canvas = createCanvas(W, H);
  const ctx = canvas.getContext('2d');
  ctx.drawImage(img, 0, 0);
  const { data } = ctx.getImageData(0, 0, W, H);
  const px = new Uint8Array(data.buffer, data.byteOffset, data.byteLength);

  const blk = JSON.parse(fs.readFileSync(blocksPath, 'utf8'));
  const boxes = blk.text_blocks || [];
  const texts = blk.texts || [];
  const styles = blk.styles || [];

  const raster = (name, pixels = px) => ({ name, top: 0, left: 0, bottom: H, right: W,
    imageData: { data: pixels, width: W, height: H } });
  const children = [raster('Original')];
  if (WITH_COPY) {
    let copyPx = px;
    if (COPY_IMAGE) {
      const cimg = await loadImage(COPY_IMAGE);
      if (cimg.width !== W || cimg.height !== H)
        throw new Error(`--copy-image ${cimg.width}x${cimg.height} != source ${W}x${H}`);
      const cc = createCanvas(W, H); const cx = cc.getContext('2d'); cx.drawImage(cimg, 0, 0);
      const cd = cx.getImageData(0, 0, W, H).data;
      copyPx = new Uint8Array(cd.buffer, cd.byteOffset, cd.byteLength);
    }
    children.push(raster('Copy', copyPx));
  }

  boxes.forEach(([x, y, w, h], i) => {
    const st = styles[i] || {};
    let text = texts[i] ?? '';
    const isPlaceholder = PLACEHOLDER !== null && !text.trim();
    if (isPlaceholder) text = PLACEHOLDER;
    const rot = [0, 90, -90, 180].includes(Number(st.rotate)) ? Number(st.rotate) : 0;
    // for a rotated box the text flows along the box's long side
    const [bw, bh] = Math.abs(rot) === 90 ? [h, w] : [w, h];
    const size = st.size ? Number(st.size) : (isPlaceholder ? placeholderSize(bh) : fitFontSize(text, bw, bh));
    const color = st.color ? hexToRgb(st.color) : autoColor(px, W, x, y, w, h);
    children.push({
      name: `Text ${i + 1}`,
      top: y, left: x, bottom: y + h, right: x + w,
      text: {
        text,
        transform: rotMatrix(rot, x, y, w, h),
        left: 0, top: 0, right: bw, bottom: bh,
        orientation: 'horizontal',
        antiAlias: 'smooth',
        shapeType: 'box',
        boxBounds: [0, 0, bw, bh],
        style: { font: { name: st.font || FONT_NAME }, fontSize: size, fillColor: color, language: LANGUAGE_PT_BR },
        paragraphStyle: { justification: st.align || 'center' },
      },
    });
  });

  fs.mkdirSync(path.dirname(outPath), { recursive: true });
  fs.writeFileSync(outPath, writePsdBuffer({ width: W, height: H, children }, { generateThumbnail: false }));
  console.log(`${path.basename(outPath)}: ${W}x${H}, ${children.length - boxes.length} raster + ${boxes.length} text layer(s)${PLACEHOLDER !== null ? ' (placeholder)' : ''}`);
}

main().catch((e) => { console.error(e); process.exit(1); });
