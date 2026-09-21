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
// Usage: node add_and_fill_text_layers.mjs <psd> <page.json> [--output <path>]
// page.json: { "blocks": [[x,y,w,h], ...], "texts": ["line1", "line2", ...] }
// blocks[i] gets name "Text {i+1}" and text texts[i]. blocks and texts must
// be the same length, already in the desired final order.

import * as fs from 'fs';
import * as path from 'path';
import { readPsd, writePsdBuffer } from 'ag-psd';

const [psdPath, pageJsonPath] = process.argv.slice(2);
const outIdx = process.argv.indexOf('--output');
const outPath = outIdx !== -1 ? process.argv[outIdx + 1] : psdPath;

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

function fontSizeFor(_w, h) {
  return Math.max(MIN_FONT_SIZE, Math.min(MAX_FONT_SIZE, Math.round(h / LINES_PER_BOX)));
}

const page = JSON.parse(fs.readFileSync(pageJsonPath, 'utf8'));
const { blocks, texts } = page;
if (!Array.isArray(blocks) || !Array.isArray(texts) || blocks.length !== texts.length) {
  console.error(`blocks (${blocks?.length}) and texts (${texts?.length}) must be same-length arrays`);
  process.exit(1);
}

const psd = readPsd(fs.readFileSync(psdPath), { useRawData: true });

// Guard against re-running on a PSD that already has "Text N" layers from a
// previous (possibly raced) attempt -- drop any pre-existing ones with the
// same names before adding the fresh, fully-filled set.
const newNames = new Set(blocks.map((_, i) => `Text ${i + 1}`));
psd.children = psd.children.filter((c) => !(c.text && newNames.has(c.name)));

for (const [i, [x, y, w, h]] of blocks.entries()) {
  psd.children.push({
    name: `Text ${i + 1}`,
    top: y,
    left: x,
    bottom: y + h,
    right: x + w,
    text: {
      text: texts[i],
      transform: [1, 0, 0, 1, x, y],
      left: 0,
      top: 0,
      right: w,
      bottom: h,
      orientation: 'horizontal',
      antiAlias: 'smooth',
      shapeType: 'box',
      boxBounds: [0, 0, w, h],
      style: {
        font: FONT,
        fontSize: fontSizeFor(w, h),
        fillColor: { r: 0, g: 0, b: 0 },
        language: LANGUAGE_PT_BR,
      },
      paragraphStyle: { justification: 'center' },
    },
  });
}

fs.mkdirSync(path.dirname(outPath), { recursive: true });
fs.writeFileSync(outPath, writePsdBuffer(psd, { generateThumbnail: false }));
console.log(`${path.basename(outPath)}: wrote ${blocks.length} text layer(s) with final text`);
