#!/usr/bin/env node
// PSD layer dump / rebuild for split_scans.py (ag-psd + canvas).
//
//   node psd_io.mjs dump <psd> <dir> [--first-only]
//     writes <dir>/layers.json {width, height, layers: [{index, name, kind:
//     raster|text|other, left, top, right, bottom, opacity, hidden, blendMode,
//     png (raster: the layer's own pixels as RGBA PNG), text (text layers:
//     ag-psd's text object, reusable as-is)}]}. --first-only dumps only the
//     first raster layer's pixels (the analysis image).
//   node psd_io.mjs build <spec.json>
//     spec {out, width, height, layers: [{kind: raster, name, png, left, top,
//     opacity, hidden, blendMode} | {kind: text, name, left, top, right,
//     bottom, text, ...}]} -> writes the PSD (raster from the PNGs, text
//     layers native/editable, no cached render: Photoshop re-renders on open).
import * as fs from 'fs';
import * as path from 'path';
import { readPsd, writePsdBuffer, initializeCanvas } from 'ag-psd';
import { createCanvas, loadImage, createImageData } from 'canvas';

initializeCanvas(createCanvas, createImageData);

const [cmd, ...rest] = process.argv.slice(2);

function flatten(children, out = []) {
  for (const l of children || []) {
    if (l.children) flatten(l.children, out); else out.push(l);
  }
  return out;
}

if (cmd === 'dump') {
  const [psdPath, dir] = rest;
  const firstOnly = rest.includes('--first-only');
  const psd = readPsd(fs.readFileSync(psdPath), { skipCompositeImageData: true, skipThumbnail: true, useImageData: true });
  const layers = [];
  let rasterDone = 0;
  flatten(psd.children).forEach((l, index) => {
    const base = { index, name: l.name, left: l.left ?? 0, top: l.top ?? 0, right: l.right ?? 0, bottom: l.bottom ?? 0,
      opacity: l.opacity ?? 1, hidden: !!l.hidden, blendMode: l.blendMode || 'normal' };
    if (l.text) {
      layers.push({ ...base, kind: 'text', text: l.text });
    } else if (l.imageData && l.imageData.width > 0 && l.imageData.height > 0) {
      const rec = { ...base, kind: 'raster' };
      if (!firstOnly || rasterDone === 0) {
        const c = createCanvas(l.imageData.width, l.imageData.height);
        c.getContext('2d').putImageData(createImageData(new Uint8ClampedArray(l.imageData.data.buffer, l.imageData.data.byteOffset, l.imageData.data.byteLength), l.imageData.width, l.imageData.height), 0, 0);
        rec.png = path.join(dir, `layer${index}.png`);
        fs.writeFileSync(rec.png, c.toBuffer('image/png'));
      }
      rasterDone++;
      layers.push(rec);
    } else {
      layers.push({ ...base, kind: 'other' });
    }
  });
  fs.writeFileSync(path.join(dir, 'layers.json'), JSON.stringify({ width: psd.width, height: psd.height, layers }));
  console.log(`dump: ${psd.width}x${psd.height}, ${layers.length} layer(s)`);
} else if (cmd === 'build') {
  const spec = JSON.parse(fs.readFileSync(rest[0], 'utf8'));
  const children = [];
  for (const L of spec.layers) {
    if (L.kind === 'raster') {
      const img = await loadImage(L.png);
      const c = createCanvas(img.width, img.height); c.getContext('2d').drawImage(img, 0, 0);
      const d = c.getContext('2d').getImageData(0, 0, img.width, img.height);
      children.push({ name: L.name, left: L.left, top: L.top, right: L.left + img.width, bottom: L.top + img.height,
        opacity: L.opacity ?? 1, hidden: !!L.hidden, blendMode: L.blendMode || 'normal',
        imageData: { data: new Uint8Array(d.data.buffer, d.data.byteOffset, d.data.byteLength), width: img.width, height: img.height } });
    } else if (L.kind === 'text') {
      // a text layer carries no pixels: zero-size bounds, like Photoshop saves
      // them (non-zero bounds with empty channels crash GIMP's PSD loader)
      children.push({ name: L.name, left: L.left, top: L.top, right: L.left, bottom: L.top,
        opacity: L.opacity ?? 1, hidden: !!L.hidden, blendMode: L.blendMode || 'normal', text: L.text });
    }
  }
  // merged preview image = the top raster (the fixed page), so file browsers
  // and tools that only read the composite show the page, not black
  const top = [...children].reverse().find((l) => l.imageData && l.imageData.width === spec.width && l.imageData.height === spec.height);
  fs.mkdirSync(path.dirname(spec.out), { recursive: true });
  fs.writeFileSync(spec.out, writePsdBuffer({ width: spec.width, height: spec.height, children, imageData: top ? top.imageData : undefined }, { generateThumbnail: false }));
  console.log(`${path.basename(spec.out)}: ${spec.width}x${spec.height}, ${children.length} layer(s)`);
} else {
  console.error('usage: psd_io.mjs dump <psd> <dir> [--first-only] | build <spec.json>');
  process.exit(1);
}
