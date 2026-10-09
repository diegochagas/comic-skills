#!/usr/bin/env node
// Verifies a PSD written by build_translated_psd.mjs:
//   - readable, canvas-sized "Original" (+ "Copy") raster layers
//   - raster pixels identical to the source image (sampled, or full with --full)
//   - one "Text N" layer per block in blocks.json, texts matching, no empties
// Usage: node verify_translated_psd.mjs <psd> <source_image> <blocks.json> [--full]
//            [--copy-image <png>] [--original <png>]
// --original <png>: Original is expected to match this image (the page as
//   scanned, from split-scans) instead of the source
import * as fs from 'fs';
import { readPsd, initializeCanvas } from 'ag-psd';
import { createCanvas, loadImage } from 'canvas';

initializeCanvas(createCanvas);
const [psdPath, srcPath, blocksPath] = process.argv.slice(2);
const full = process.argv.includes('--full');
// --copy-image <png>: Copy is expected to match this image instead of the source
const ci = process.argv.indexOf('--copy-image');
const COPY_IMAGE = ci !== -1 ? process.argv[ci + 1] : null;
const oi = process.argv.indexOf('--original');
const ORIGINAL_IMAGE = oi !== -1 ? process.argv[oi + 1] : null;

const img = await loadImage(srcPath);
const c = createCanvas(img.width, img.height);
c.getContext('2d').drawImage(img, 0, 0);
const src = c.getContext('2d').getImageData(0, 0, img.width, img.height).data;
let copySrc = src, origSrc = src;
if (ORIGINAL_IMAGE) {
  const oi2 = await loadImage(ORIGINAL_IMAGE);
  const oc = createCanvas(oi2.width, oi2.height);
  oc.getContext('2d').drawImage(oi2, 0, 0);
  origSrc = oc.getContext('2d').getImageData(0, 0, oi2.width, oi2.height).data;
}
if (COPY_IMAGE) {
  const ci2 = await loadImage(COPY_IMAGE);
  const cc = createCanvas(ci2.width, ci2.height);
  cc.getContext('2d').drawImage(ci2, 0, 0);
  copySrc = cc.getContext('2d').getImageData(0, 0, ci2.width, ci2.height).data;
}

const psd = readPsd(fs.readFileSync(psdPath), { skipCompositeImageData: true, skipThumbnail: true });
const blocks = JSON.parse(fs.readFileSync(blocksPath, 'utf8'));
const problems = [];
if (psd.width !== img.width || psd.height !== img.height) problems.push(`canvas ${psd.width}x${psd.height} != source ${img.width}x${img.height}`);

for (const name of ['Original', 'Copy']) {
  const L = psd.children.find((l) => l.name === name);
  if (!L) { problems.push(`missing layer ${name}`); continue; }
  if (!L.canvas || L.canvas.width !== img.width || L.canvas.height !== img.height) { problems.push(`${name}: bad size`); continue; }
  const d = L.canvas.getContext('2d').getImageData(0, 0, img.width, img.height).data;
  const ref = name === 'Copy' ? copySrc : origSrc;
  const step = full ? 1 : 997;             // sample every 997th pixel unless --full
  let diff = 0, n = 0;
  for (let i = 0; i < d.length; i += 4 * step) {
    n++;
    if (d[i] !== ref[i] || d[i + 1] !== ref[i + 1] || d[i + 2] !== ref[i + 2]) diff++;
  }
  if (diff) problems.push(`${name}: ${diff}/${n} sampled pixels differ from source`);
}

const texts = psd.children.filter((l) => l.text);
if (texts.length !== blocks.text_blocks.length) problems.push(`text layers ${texts.length} != blocks ${blocks.text_blocks.length}`);
texts.forEach((l, i) => {
  const want = (blocks.texts || [])[i] ?? '';
  if (l.text.text !== want) problems.push(`Text ${i + 1}: content mismatch`);
  if (!l.text.text) problems.push(`Text ${i + 1}: empty`);
  if (!l.text.style || !l.text.style.fontSize) problems.push(`Text ${i + 1}: no font size`);
});

if (problems.length) { console.log(`FAIL ${psdPath}\n  ` + problems.join('\n  ')); process.exit(1); }
console.log(`OK ${psdPath}: ${psd.width}x${psd.height}, Original+Copy pixel-exact${COPY_IMAGE ? ' (Copy=cleaned)' : ''}${ORIGINAL_IMAGE ? ' (Original=as scanned)' : ''} (${full ? 'full' : 'sampled'}), ${texts.length} text layers`);
