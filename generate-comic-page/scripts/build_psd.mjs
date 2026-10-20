#!/usr/bin/env node
// Writes the layered PSD of a page from the job build_psd.py prepared, then
// reads it back and checks every layer is there and every Type layer still
// holds its exact text. ag-psd only (no GIMP, no canvas): raster layers come
// in as PNG files (pngjs), text boxes are native Photoshop paragraph Type
// layers in the same form manga-translator-ptbr's build_translated_psd.mjs
// writes (Diego opens those in Photoshop), the outline is a live Layer Style
// stroke.
//
//   node build_psd.mjs <psd_job.json>
//
// Job (absolute paths, boxes in px [x, y, w, h], bottom of the stack first):
//   {"out", "size": [W, H], "paper": "#ffffff", "composite": "preview.png",
//    "art": "art.png" | null,                                  hidden reference layer
//    "panels": [{"name": "Panel 01", "box", "layers": [{"name": "Background", "file"}, ...]}],
//    "images": [{"name", "file", "box"}],
//    "texts":  [{"name", "text", "box", "font": "CCWildWords-Regular", "size", "color",
//                "align", "line_spacing", "letter_spacing", "outline": {"color", "width"} | null}]}
import * as fs from 'fs';
import * as path from 'path';
import { PNG } from 'pngjs';
import { readPsd, writePsdBuffer } from 'ag-psd';

// Adobe text engine language code 11 = "Portuguese: Brazilian" (what Diego's own
// Photoshop layers carry; 0 would be English: USA)
const LANGUAGE_PT_BR = 11;
const LEADING = 1.2;

function hexToRgb(hex) {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex || '');
  return m ? { r: parseInt(m[1], 16), g: parseInt(m[2], 16), b: parseInt(m[3], 16) } : { r: 0, g: 0, b: 0 };
}

function readPng(file) {
  const png = PNG.sync.read(fs.readFileSync(file));
  return { data: new Uint8ClampedArray(png.data.buffer, png.data.byteOffset, png.data.byteLength), width: png.width, height: png.height };
}

function rasterLayer(name, file, left, top, extra = {}) {
  const img = readPng(file);
  return { name, left, top, right: left + img.width, bottom: top + img.height, imageData: img, ...extra };
}

function paperLayer(W, H, color) {
  const { r, g, b } = hexToRgb(color);
  const data = new Uint8ClampedArray(W * H * 4);
  for (let i = 0; i < data.length; i += 4) { data[i] = r; data[i + 1] = g; data[i + 2] = b; data[i + 3] = 255; }
  return { name: 'Paper', left: 0, top: 0, right: W, bottom: H, imageData: { data, width: W, height: H } };
}

function strokeEffect(outline) {
  return { scale: 1, stroke: [{ enabled: true, present: true, showInDialog: true, position: 'outside',
    fillType: 'color', blendMode: 'normal', opacity: 1, overprint: false,
    size: { units: 'Pixels', value: Math.max(1, Math.round(outline.width || 4)) }, color: hexToRgb(outline.color) }] };
}

function textLayer(t) {
  const [x, y, w, h] = t.box;
  const style = { font: { name: t.font }, fontSize: t.size, fillColor: hexToRgb(t.color), language: LANGUAGE_PT_BR };
  if (t.line_spacing) { style.autoLeading = false; style.leading = Math.round(LEADING * t.size + t.line_spacing); }
  if (t.letter_spacing) style.tracking = Math.round((t.letter_spacing / t.size) * 1000);
  const layer = {
    name: t.name,
    // zero-size pixel bounds, as Photoshop saves text layers (GIMP's loader
    // crashes on non-zero bounds with empty channels); the box is text.boxBounds
    top: y, left: x, bottom: y, right: x,
    text: { text: t.text, transform: [1, 0, 0, 1, x, y], left: 0, top: 0, right: w, bottom: h,
      orientation: 'horizontal', antiAlias: 'smooth', shapeType: 'box', boxBounds: [0, 0, w, h],
      style, paragraphStyle: { justification: t.align || 'center' } },
  };
  if (t.outline) layer.effects = strokeEffect(t.outline);
  return layer;
}

function buildChildren(job) {
  const [W, H] = job.size;
  const children = [];
  if (job.art) children.push(rasterLayer('Art (flat)', job.art, 0, 0, { hidden: true }));
  children.push(paperLayer(W, H, job.paper));
  for (const p of job.panels) {
    children.push({ name: p.name, opened: true, children: p.layers.map((l) => rasterLayer(l.name, l.file, p.box[0], p.box[1])) });
  }
  for (const im of job.images) children.push(rasterLayer(im.name, im.file, im.box[0], im.box[1]));
  children.push({ name: 'Text', opened: true, children: job.texts.map(textLayer) });
  return children;
}

function expectedLayers(job) {
  const names = ['Paper', 'Text', ...job.images.map((i) => i.name)];
  if (job.art) names.push('Art (flat)');
  for (const p of job.panels) names.push(p.name, ...p.layers.map((l) => l.name));
  return names;
}

function verify(file, job) {
  const psd = readPsd(fs.readFileSync(file), { skipLayerImageData: true, skipCompositeImageData: true, skipThumbnail: true });
  const found = new Map();
  const walk = (ls) => ls.forEach((l) => { found.set(l.name, l); if (l.children) walk(l.children); });
  walk(psd.children || []);
  const problems = [];
  for (const name of expectedLayers(job)) if (!found.has(name)) problems.push(`layer '${name}' missing`);
  for (const t of job.texts) {
    const l = found.get(t.name);
    if (!l) problems.push(`text layer '${t.name}' missing`);
    else if (!l.text) problems.push(`'${t.name}' is not an editable text layer`);
    else if (l.text.text !== t.text) problems.push(`'${t.name}' text differs from the layout`);
  }
  return problems;
}

function main() {
  const job = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const [W, H] = job.size;
  const children = buildChildren(job);
  const composite = readPng(job.composite);
  fs.mkdirSync(path.dirname(job.out), { recursive: true });
  fs.writeFileSync(job.out, writePsdBuffer({ width: W, height: H, children, imageData: composite }, { generateThumbnail: false }));
  const problems = verify(job.out, job);
  const rasters = children.reduce((n, l) => n + (l.children ? l.children.filter((c) => !c.text).length : l.text ? 0 : 1), 0);
  if (problems.length) { console.log('FAIL ' + problems.join('; ')); process.exit(1); }
  console.log(`OK ${job.out}: ${W}x${H}, ${job.panels.length} panel group(s), ${rasters} raster + ${job.texts.length} text layer(s)`);
}

main();
