#!/usr/bin/env node
// XCF -> PSD, last step. GIMP's own PSD export keeps pixels, groups, masks,
// blend modes and opacity, but writes every text layer as plain pixels and has
// no Layer Styles. This script takes that export plus the description
// gimp_convert_job.py wrote (fonts already resolved to PostScript names by
// convert.py) and turns the listed layers back into native Photoshop Type
// layers - paragraph box or point text, mixed style runs, rotation - with the
// GIMP "Text Styling" outline / shadow as live Layer Styles (Stroke, Drop
// Shadow, Color Overlay).
//
// Usage: node write_psd_text.mjs <gimp_export.psd> <info.json> <out.psd>
//
// Pixel data of every layer is passed through untouched (raw channel bytes),
// the rendered text included: it is what other viewers show. Photoshop is told
// to redraw the Type layers on open (invalidateTextLayers), so what Diego sees
// there is Photoshop's own rendering with his fonts.
// Prints one JSON line: {"text": n, "styled": n, "problems": [...]}; exit 2 on problems.

import * as fs from 'fs';
import * as path from 'path';
import { readPsd, writePsdBuffer, initializeCanvas } from 'ag-psd';

// ag-psd only needs somewhere to put decoded pixels: a plain buffer does it,
// so this skill has no native node-canvas dependency
initializeCanvas(
  () => { throw new Error('canvas is not available in this script'); },
  (width, height) => ({ width, height, data: new Uint8ClampedArray(width * height * 4) }),
);

const [inPath, infoPath, outPath] = process.argv.slice(2);
if (!inPath || !infoPath || !outPath) {
  console.error('Usage: node write_psd_text.mjs <gimp_export.psd> <info.json> <out.psd>');
  process.exit(1);
}

const rgb = (hex) => {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})/i.exec(hex || '');
  return m ? { r: parseInt(m[1], 16), g: parseInt(m[2], 16), b: parseInt(m[3], 16) } : { r: 0, g: 0, b: 0 };
};
// Photoshop's text language (Character panel; drives spell check and
// hyphenation). Every Type layer this skill writes is Portuguese: Brazilian
// (Adobe text engine code 11) - left unset, Photoshop
// shows English: USA.
const LANGUAGE_PT_BR = 11;
const pxv = (value) => ({ value, units: 'Pixels' });
const r2 = (v) => Math.round(v * 100) / 100;

function textRecord(t) {
  const runs = t.runs.filter((r) => r.text);
  const text = runs.map((r) => r.text).join('');
  const style = (r) => ({
    font: { name: r.font },
    fontSize: r2(r.size),
    fillColor: rgb(r.color),
    fauxBold: !!r.fauxBold,
    fauxItalic: !!r.fauxItalic,
    underline: !!r.underline,
    strikethrough: !!r.strike,
    tracking: Math.round(((r.letter_spacing || 0) / (r.size || 1)) * 1000),
    autoLeading: false,
    language: LANGUAGE_PT_BR,
    // GIMP: one line spacing per layer, a bigger run just makes its line taller
    leading: r2(t.leading * (r.size / (t.baseSize || r.size))),
  });
  const base = runs.reduce((a, b) => (b.text.length > a.text.length ? b : a), runs[0] || { ...t.runs[0] });
  const rad = ((t.angle || 0) * Math.PI) / 180;
  const cos = Math.cos(rad), sin = Math.sin(rad);
  const { w, h, cx, cy } = t.box;
  const just = t.justification || 'left';
  // local offset (from the box centre) of the point Photoshop's transform moves:
  // box text = the box's top-left; point text = the first baseline, at the
  // left end / middle / right end of the line depending on the justification
  const u = t.shape === 'box' ? -w / 2 : (just === 'center' ? 0 : just === 'right' ? w / 2 : -w / 2);
  // a layer squeezed horizontally (Photoshop's non-uniform Free Transform, kept
  // in the XCF parasite): the transform carries the squeeze, the text space doesn't
  const k = t.hscale && Math.abs(t.hscale - 1) >= 0.02 ? t.hscale : 1;
  const v = t.shape === 'box' ? -h / 2 : -h / 2 + (t.ascent || base.size * 0.8);
  const rec = {
    text,
    transform: [cos * k, sin * k, -sin, cos, r2(cx + cos * u - sin * v), r2(cy + sin * u + cos * v)].map((n) => Math.round(n * 1e6) / 1e6),
    antiAlias: 'smooth',
    orientation: 'horizontal',
    shapeType: t.shape === 'box' ? 'box' : 'point',
    style: style(base),
    paragraphStyle: { justification: just, firstLineIndent: t.indent || 0 },
  };
  if (t.shape === 'box') {
    Object.assign(rec, { left: 0, top: 0, right: r2(w / k), bottom: r2(h), boxBounds: [0, 0, r2(w / k), r2(h)] });
  } else {
    rec.pointBase = [0, 0];
  }
  if (runs.length > 1) rec.styleRuns = runs.map((r) => ({ length: r.text.length, style: style(r) }));
  return rec;
}

function effectsRecord(fx) {
  const out = { scale: 1 };
  if (fx.stroke) {
    out.stroke = [{ enabled: true, present: true, showInDialog: true, position: fx.stroke.position || 'outside',
      fillType: 'color', blendMode: 'normal', opacity: fx.stroke.opacity ?? 1, overprint: false,
      size: pxv(Math.max(1, Math.round(fx.stroke.size))), color: rgb(fx.stroke.color) }];
  }
  if (fx.shadow) {
    const { x, y, blur, grow } = fx.shadow;
    const dist = Math.hypot(x, y);
    out.dropShadow = [{ enabled: true, present: true, showInDialog: true, blendMode: 'normal',
      color: rgb(fx.shadow.color), opacity: fx.shadow.opacity ?? 0.75, useGlobalLight: false,
      angle: dist ? Math.round((Math.atan2(y, -x) * 180) / Math.PI) : 120,
      distance: pxv(Math.round(dist)), size: pxv(Math.round(blur)),
      choke: pxv(blur > 0 ? Math.min(100, Math.round((grow / blur) * 100)) : 0),
      noise: 0, antialiased: false, layerConceals: true,
      contour: { name: 'Linear', curve: [{ x: 0, y: 0 }, { x: 255, y: 255 }] } }];
  }
  if (fx.fill) {
    out.solidFill = [{ enabled: true, present: true, showInDialog: true, blendMode: fx.fill.blend || 'normal',
      color: rgb(fx.fill.color), opacity: 1 }];
  }
  return out;
}

const info = JSON.parse(fs.readFileSync(infoPath, 'utf8'));
const buf = fs.readFileSync(inPath);
const psd = readPsd(buf, { useRawData: true, skipCompositeImageData: true, skipThumbnail: true });
// the flattened image travels as decoded pixels (raw composite bytes cannot be
// written back). info.composite_from = GIMP's export made BEFORE the outline /
// shadow filters were hidden, so viewers that only show the flattened image
// still see them.
const flatFrom = info.composite_from && fs.existsSync(info.composite_from) ? fs.readFileSync(info.composite_from) : buf;
psd.imageData = readPsd(flatFrom, { skipLayerImageData: true, skipThumbnail: true, useImageData: true }).imageData;
const problems = [];
let nText = 0, nFx = 0;
for (const e of info.layers) {
  let children = psd.children || [], layer = null;
  for (const i of e.path) {
    layer = children[children.length - 1 - i];            // GIMP counts from the top, ag-psd from the bottom
    children = (layer && layer.children) || [];
  }
  if (!layer || (layer.name || '').trim() !== (e.name || '').trim()) {
    problems.push(`layer "${e.name}" not found at its place in GIMP's export (found "${layer ? layer.name : 'nothing'}")`);
    continue;
  }
  if (e.text) { layer.text = textRecord(e.text); nText++; }
  if (e.effects) {
    layer.effects = effectsRecord(e.effects);
    if (e.effects.fill_opacity != null) layer.fillOpacity = e.effects.fill_opacity;
    nFx++;
  }
}

fs.mkdirSync(path.dirname(outPath), { recursive: true });
fs.writeFileSync(outPath, writePsdBuffer(psd, { generateThumbnail: false, invalidateTextLayers: true, noBackground: true }));

// verify what was actually written
const back = readPsd(fs.readFileSync(outPath), { skipLayerImageData: true, skipCompositeImageData: true, skipThumbnail: true });
let gotText = 0, gotFx = 0;
const count = (ls) => ls.forEach((l) => { if (l.text) gotText++; if (l.effects) gotFx++; if (l.children) count(l.children); });
count(back.children || []);
if (gotText !== nText) problems.push(`${gotText} Type layer(s) in the file, expected ${nText}`);
if (gotFx !== nFx) problems.push(`${gotFx} layer(s) with Layer Styles in the file, expected ${nFx}`);
console.log(JSON.stringify({ text: nText, styled: nFx, problems }));
process.exit(problems.length ? 2 : 0);
