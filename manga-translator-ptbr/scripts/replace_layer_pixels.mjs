#!/usr/bin/env node
// Replaces the pixels of ONE raster layer of a PSD with a PNG of the same
// size (e.g. the Cleaned layer after erasing a sign with inpaint_lama.py);
// every other layer, text layers included, is written back as it was read.
//
// Usage: node replace_layer_pixels.mjs <psd> <layer index> <png> [--output <path>]
// <layer index> is the index from list_layers.mjs. Defaults to overwriting
// the PSD in place.

import * as fs from 'fs';
import * as path from 'path';
import { readPsd, writePsdBuffer } from 'ag-psd';
import { createCanvas, loadImage } from 'canvas';

const [psdPath, indexArg, pngPath] = process.argv.slice(2);
const outIdx = process.argv.indexOf('--output');
const outPath = outIdx !== -1 ? process.argv[outIdx + 1] : psdPath;

if (!psdPath || indexArg === undefined || !pngPath) {
  console.error('Usage: node replace_layer_pixels.mjs <psd> <layer index> <png> [--output <path>]');
  process.exit(1);
}

/** Decodes a PNG into raw RGBA bytes, returned with its size. */
async function readRgba(file) {
  const img = await loadImage(file);
  const ctx = createCanvas(img.width, img.height).getContext('2d');
  ctx.drawImage(img, 0, 0);
  const { data } = ctx.getImageData(0, 0, img.width, img.height);
  return { width: img.width, height: img.height, data: new Uint8Array(data.buffer, data.byteOffset, data.byteLength) };
}

const psd = readPsd(fs.readFileSync(psdPath), { useRawData: true });
const layer = psd.children[Number(indexArg)];
if (!layer || layer.text || !layer.rawData) {
  console.error(`layer ${indexArg} is not a raster layer (children: ${psd.children.length})`);
  process.exit(1);
}
const png = await readRgba(pngPath);
// raw mode keeps a raster layer as its compressed bytes (rawData); the layer
// rectangle says how big the pixels must be
const width = layer.right - layer.left;
const height = layer.bottom - layer.top;
if (png.width !== width || png.height !== height) {
  console.error(`${pngPath} is ${png.width}x${png.height}, layer "${layer.name}" is ${width}x${height}`);
  process.exit(1);
}
delete layer.rawData;
layer.imageData = { width, height, data: png.data };
fs.mkdirSync(path.dirname(outPath), { recursive: true });
fs.writeFileSync(outPath, writePsdBuffer(psd, { generateThumbnail: false }));
console.log(`${path.basename(outPath)}: layer ${indexArg} "${layer.name}" replaced (${width}x${height})`);
