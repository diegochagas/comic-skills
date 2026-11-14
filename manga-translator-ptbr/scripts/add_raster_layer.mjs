#!/usr/bin/env node
// Inserts a PNG as a new raster layer of a PSD, right above a named layer.
// Everything else in the file (text layers, adjustment layers, the other
// rasters) is written back as read.
//
// Usage: node add_raster_layer.mjs <in.psd> <layer.png> <out.psd> --name "Qwen clean"
//             --above "Copy" [--left 0] [--top 0] [--smart-original NAME ...]
// --smart-original NAME: also turn that layer into a hidden Smart Object
//   (the scan it holds stays in the file, out of the way).
import * as fs from 'fs';
import * as path from 'path';
import { readPsd, writePsdBuffer, initializeCanvas } from 'ag-psd';
import { loadImage, createCanvas, createImageData } from 'canvas';
import { asSmartObject } from './smart_object.mjs';

initializeCanvas(createCanvas, createImageData);
const args = process.argv.slice(2);
const pos = [];
const opts = { smart: [] };
for (let i = 0; i < args.length; i++) {
  if (args[i] === '--smart-original') opts.smart.push(args[++i]);
  else if (args[i].startsWith('--')) opts[args[i].slice(2)] = args[++i];
  else pos.push(args[i]);
}
const [inPath, pngPath, outPath] = pos;
if (!outPath || !opts.above) {
  console.error('Usage: node add_raster_layer.mjs <in.psd> <layer.png> <out.psd> --name N --above LAYER [--left X] [--top Y] [--smart-original NAME ...]');
  process.exit(1);
}

const psd = readPsd(fs.readFileSync(inPath), { useImageData: true, skipCompositeImageData: true, skipThumbnail: true });
const at = psd.children.findIndex((l) => l.name === opts.above);
if (at === -1) {
  console.error(`no top-level layer named "${opts.above}" in ${inPath} (has: ${psd.children.map((l) => l.name).join(', ')})`);
  process.exit(1);
}
const img = await loadImage(pngPath);
const canvas = createCanvas(img.width, img.height);
canvas.getContext('2d').drawImage(img, 0, 0);
const d = canvas.getContext('2d').getImageData(0, 0, img.width, img.height);
const left = Number(opts.left || 0);
const top = Number(opts.top || 0);
psd.children.splice(at + 1, 0, {
  name: opts.name || 'Qwen clean', left, top, right: left + img.width, bottom: top + img.height,
  imageData: { data: new Uint8Array(d.data.buffer, d.data.byteOffset, d.data.byteLength), width: img.width, height: img.height },
});
for (const name of opts.smart) {
  const layer = psd.children.find((l) => l.name === name);
  if (!layer) { console.error(`--smart-original: no layer named "${name}"`); process.exit(1); }
  asSmartObject(psd, layer, { hidden: true });
}
fs.mkdirSync(path.dirname(outPath), { recursive: true });
fs.writeFileSync(outPath, writePsdBuffer(psd, { generateThumbnail: false }));
console.log(`${path.basename(outPath)}: "${opts.name || 'Qwen clean'}" ${img.width}x${img.height} at ${left},${top} above "${opts.above}"`);
