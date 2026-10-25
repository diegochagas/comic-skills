#!/usr/bin/env node
// Single read-modify-write pass: adds one text layer per block with the
// FINAL translated text already set (no separate placeholder-then-replace
// round trip). Written because some target folders are flaky network/VM
// shared-folder mounts (e.g. a WinBoat shared folder) where two separate
// node processes read-modify-writing the same file back-to-back
// (a box-adding pass then set_text_layers.mjs) can race and leave
// duplicate layers (one process's write landing after the other's read).
// Doing it in one process/one write avoids that race entirely.
//
// Usage: node add_and_fill_text_layers.mjs <psd> <page.json> [--output <path>] [--no-fit] [--append]
// The new boxes are grown to show their whole text and moved apart where
// their text would collide (fit_boxes.mjs) unless --no-fit is given.
// page.json: { "blocks": [[x,y,w,h], ...], "texts": ["line1", "line2", ...] }
// blocks[i] gets name "Text {i+1}" and text texts[i]. blocks and texts must
// be the same length, already in the desired final order.
// --append keeps the PSD's existing "Text N" layers and numbers the new ones
// after the highest N (for boxes the page was missing). page.json may then
// carry "styles": [{"color": "#ffffff", "size": 14, "rotate": 90,
// "align": "left"}, ...], one optional entry per block (rotate: 0|90|-90|180,
// default centred black text at a size derived from the box height).

import * as fs from 'fs';
import * as path from 'path';
import { readPsd, writePsdBuffer } from 'ag-psd';
import { fitTextLayers, describeChanges } from './fit_boxes.mjs';

const [psdPath, pageJsonPath] = process.argv.slice(2);
const outIdx = process.argv.indexOf('--output');
const outPath = outIdx !== -1 ? process.argv[outIdx + 1] : psdPath;
const APPEND = process.argv.includes('--append');

if (!psdPath || !pageJsonPath) {
  console.error('Usage: node add_and_fill_text_layers.mjs <psd> <page.json> [--output <path>]');
  process.exit(1);
}

const FONT = { name: 'CCWildWords-Regular' };
const MIN_FONT_SIZE = 10;
const MAX_FONT_SIZE = 32;
const LINES_PER_BOX = 7;
// Photoshop's text language (Character panel; drives spell check and
// hyphenation): Adobe text engine code 11 = "Portuguese: Brazilian" (10 =
// Portuguese, 0 = English: USA, which is what a layer gets when it is unset).
const LANGUAGE_PT_BR = 11;

/** "#rrggbb" -> {r,g,b}. */
function hexToRgb(hex) {
  const n = parseInt(hex.replace('#', ''), 16);
  return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
}

// Photoshop text transform [a b c d tx ty] for a box rotated around its corner
// (same convention as build_translated_psd.mjs rotMatrix).
function rotMatrix(rot, x, y, w, h) {
  switch (rot) {
    case 90: return [0, 1, -1, 0, x + w, y];
    case -90: return [0, -1, 1, 0, x, y + h];
    case 180: return [-1, 0, 0, -1, x + w, y + h];
    default: return [1, 0, 0, 1, x, y];
  }
}

function fontSizeFor(_w, h) {
  return Math.max(MIN_FONT_SIZE, Math.min(MAX_FONT_SIZE, Math.round(h / LINES_PER_BOX)));
}

const page = JSON.parse(fs.readFileSync(pageJsonPath, 'utf8'));
const { blocks, texts, styles = [] } = page;
if (!Array.isArray(blocks) || !Array.isArray(texts) || blocks.length !== texts.length) {
  console.error(`blocks (${blocks?.length}) and texts (${texts?.length}) must be same-length arrays`);
  process.exit(1);
}

const psd = readPsd(fs.readFileSync(psdPath), { useRawData: true });

// Guard against re-running on a PSD that already has "Text N" layers from a
// previous (possibly raced) attempt -- drop any pre-existing ones with the
// same names before adding the fresh, fully-filled set.
const textNumber = (c) => (c.text ? Number(/^Text (\d+)$/.exec(c.name || '')?.[1] || 0) : 0);
const firstNumber = APPEND ? Math.max(0, ...psd.children.map(textNumber)) + 1 : 1;
if (!APPEND) {
  const newNames = new Set(blocks.map((_, i) => `Text ${i + 1}`));
  psd.children = psd.children.filter((c) => !(c.text && newNames.has(c.name)));
}

for (const [i, [x, y, w, h]] of blocks.entries()) {
  const st = styles[i] || {};
  const rot = [0, 90, -90, 180].includes(Number(st.rotate)) ? Number(st.rotate) : 0;
  const [bw, bh] = Math.abs(rot) === 90 ? [h, w] : [w, h];
  psd.children.push({
    name: `Text ${firstNumber + i}`,
    top: y,
    left: x,
    bottom: y,      // zero-size pixel bounds, as Photoshop saves text layers
    right: x,       // (GIMP crashes on non-zero bounds with empty channels)
    text: {
      text: texts[i],
      transform: rotMatrix(rot, x, y, w, h),
      left: 0,
      top: 0,
      right: bw,
      bottom: bh,
      orientation: 'horizontal',
      antiAlias: 'smooth',
      shapeType: 'box',
      boxBounds: [0, 0, bw, bh],
      style: {
        font: FONT,
        fontSize: st.size ? Number(st.size) : fontSizeFor(bw, bh),
        fillColor: st.color ? hexToRgb(st.color) : { r: 0, g: 0, b: 0 },
        language: LANGUAGE_PT_BR,
      },
      paragraphStyle: { justification: st.align || 'center' },
    },
  });
}

if (!process.argv.includes('--no-fit')) {
  const fit = fitTextLayers(psd.children, psd.width, psd.height);
  if (fit.changes.length) console.log(`text boxes fitted (${fit.changes.length}):\n` + describeChanges(fit));
}
fs.mkdirSync(path.dirname(outPath), { recursive: true });
fs.writeFileSync(outPath, writePsdBuffer(psd, { generateThumbnail: false }));
console.log(`${path.basename(outPath)}: wrote ${blocks.length} text layer(s) with final text`);
