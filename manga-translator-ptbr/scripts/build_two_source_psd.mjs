#!/usr/bin/env node
// Batch-build 2-layer PSDs (Japanese bottom, Portuguese top) plus one native
// Photoshop paragraph text box per detected Japanese text block, in a single
// process (no GIMP dependency -- pure ag-psd, mirrors what
// build_translated_psd.mjs does for the box layers).
//
// Usage: node build_two_source_psd.mjs <job.json>
// job.json: [{ "jp_png": "...", "pt_png": "...", "detect_json": "...",
//              "out_psd": "...", "stem": "005" }, ...]
// jp_png and pt_png must already be the SAME pixel dimensions (canvas size).

import * as fs from 'fs';
import * as path from 'path';
import { PNG } from 'pngjs';
import { writePsdBuffer } from 'ag-psd';

const FONT = { name: 'CCWildWords-Regular' };
const PLACEHOLDER_TEXT = 'Lorem ipsum dolor sit amet, consectetur adipiscing elit.';
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

function decodePng(pngPath) {
  const png = PNG.sync.read(fs.readFileSync(pngPath));
  return {
    data: new Uint8Array(png.data.buffer, png.data.byteOffset, png.data.byteLength),
    width: png.width,
    height: png.height,
  };
}

function buildOne(job) {
  const { jp_png, pt_png, detect_json, out_psd, stem } = job;

  const jpImg = decodePng(jp_png);
  const ptImg = decodePng(pt_png);
  if (jpImg.width !== ptImg.width || jpImg.height !== ptImg.height) {
    throw new Error(`size mismatch: jp ${jpImg.width}x${jpImg.height} vs pt ${ptImg.width}x${ptImg.height}`);
  }
  const width = jpImg.width, height = jpImg.height;

  const psd = {
    width,
    height,
    children: [
      { name: 'Japanese', top: 0, left: 0, bottom: height, right: width, imageData: jpImg },
      { name: 'Portuguese', top: 0, left: 0, bottom: height, right: width, imageData: ptImg },
    ],
  };

  let nBlocks = 0;
  if (detect_json && fs.existsSync(detect_json)) {
    const detect = JSON.parse(fs.readFileSync(detect_json, 'utf8'));
    const blocks = detect.text_blocks || [];
    nBlocks = blocks.length;
    for (const [i, [x, y, w, h]] of blocks.entries()) {
      psd.children.push({
        name: `Text ${i + 1}`,
        top: y,
        left: x,
        bottom: y + h,
        right: x + w,
        text: {
          text: PLACEHOLDER_TEXT,
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
  }

  fs.mkdirSync(path.dirname(out_psd), { recursive: true });
  fs.writeFileSync(out_psd, writePsdBuffer(psd, { generateThumbnail: false }));
  return nBlocks;
}

function main() {
  const jobPath = process.argv[2];
  if (!jobPath) {
    console.error('Usage: node build_two_source_psd.mjs <job.json>');
    process.exit(1);
  }
  const jobs = JSON.parse(fs.readFileSync(jobPath, 'utf8'));
  let ok = 0, fail = 0;
  for (const job of jobs) {
    try {
      const n = buildOne(job);
      console.log(`OK ${job.stem}: ${n} text block(s) -> ${job.out_psd}`);
      ok++;
    } catch (e) {
      console.log(`FAIL ${job.stem}: ${e.message}`);
      fail++;
    }
  }
  console.log(`DONE ${ok} ok, ${fail} failed`);
}

main();
