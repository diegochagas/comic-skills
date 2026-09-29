#!/usr/bin/env node
// PSD -> XCF, step 1: everything GIMP's own PSD loader throws away. GIMP loads
// the pixels, groups, masks, blend modes and opacity of a PSD by itself, but it
// rasterizes every Type layer and drops every Layer Style. This script reads
// exactly those two things and writes them as JSON for gimp_convert_job.py,
// which turns them back into native GIMP text layers and "Text Styling"
// (gegl:styles) filters.
//
// Usage: node psd_text_info.mjs <in.psd> <out.json>
//
// out.json:
//   { "width", "height", "layers": [ {
//       "path":  [2, 0],            // index at each tree level, TOP layer = 0 (GIMP's order)
//       "name":  "Text 1",
//       "bbox":  [l, t, r, b],      // the layer's pixel bounds on the page
//       "text":  { ... } | null,    // see textInfo()
//       "effects": { "stroke": {...}, "shadow": {...}, "fill": {...} } | null
//   } ] }
// Only layers with a text and/or a convertible effect are listed. All sizes
// are page pixels: the Type layer's transform scale is already applied.

import * as fs from 'fs';
import { readPsd } from 'ag-psd';

const [psdPath, outPath] = process.argv.slice(2);
if (!psdPath || !outPath) {
  console.error('Usage: node psd_text_info.mjs <in.psd> <out.json>');
  process.exit(1);
}

const hex = (c) => {
  if (!c) return '#000000';
  // ag-psd colours: {r,g,b} 0-255 (also seen: {fr,fg,fb} floats, grayscale {k})
  if ('fr' in c) return hex({ r: c.fr * 255, g: c.fg * 255, b: c.fb * 255 });
  if ('k' in c && !('c' in c)) { const v = 255 - (c.k * 255) / 100; return hex({ r: v, g: v, b: v }); }
  const h = (v) => Math.max(0, Math.min(255, Math.round(v ?? 0))).toString(16).padStart(2, '0');
  return `#${h(c.r)}${h(c.g)}${h(c.b)}`;
};
const px = (v) => (v && typeof v === 'object' ? Number(v.value) : Number(v ?? 0));
const round = (v, n = 3) => Math.round(v * 10 ** n) / 10 ** n;

function textInfo(layer) {
  const t = layer.text;
  const [a, b, c, d, tx, ty] = t.transform || [1, 0, 0, 1, layer.left, layer.top];
  const sx = Math.hypot(a, b) || 1, sy = Math.hypot(c, d) || 1;
  const angle = (Math.atan2(b, a) * 180) / Math.PI;
  const base = t.style || {};
  // A Type layer squeezed or stretched with Free Transform (or Character >
  // horizontal/vertical scale) has different scales on the two axes. Neither
  // GIMP nor Pango can set a glyph's width, so the text is sized by the
  // VERTICAL scale and laid out in a box widened by 1/hscale - same line breaks
  // as Photoshop - and the finished layer is then squeezed to hscale of its
  // width (squeezing keeps the pixels sharp; stretching the height would not).
  const hs = base.horizontalScale ?? 1, vs = base.verticalScale ?? 1;
  let hscale = (sx * hs) / (sy * vs);
  if (Math.abs(hscale - 1) < 0.02) hscale = 1;
  const fs = sy * vs;                                     // font-size factor
  const pstyle = t.paragraphStyle || (t.paragraphStyleRuns && t.paragraphStyleRuns[0] && t.paragraphStyleRuns[0].style) || {};
  // Photoshop: \r = paragraph,  = soft line break
  const full = (t.text || '').replace(/\r\n?|/g, '\n');

  const runStyle = (s) => {
    const st = { ...base, ...(s || {}) };
    return {
      font: (st.font && st.font.name) || 'ArialMT',
      size: round((st.fontSize ?? 12) * fs),
      color: hex(st.fillColor),
      bold: !!st.fauxBold,
      italic: !!st.fauxItalic,
      underline: !!st.underline,
      strike: !!st.strikethrough,
      tracking: st.tracking ?? 0,                       // 1/1000 em
      caps: st.fontCaps ?? 0,                           // 1 small caps, 2 all caps
      leading: st.autoLeading === false && st.leading ? round(st.leading * fs) : null, // null = auto
    };
  };
  let runs = [];
  if (t.styleRuns && t.styleRuns.length) {
    let at = 0;
    for (const r of t.styleRuns) {
      const piece = full.slice(at, at + r.length);
      at += r.length;
      if (piece) runs.push({ text: piece, ...runStyle(r.style) });
    }
    if (at < full.length && runs.length) runs[runs.length - 1].text += full.slice(at);
  }
  if (!runs.length) runs = [{ text: full, ...runStyle(null) }];
  // Photoshop keeps a trailing paragraph mark; GIMP would render it as an empty line
  const last = runs[runs.length - 1];
  last.text = last.text.replace(/\n+$/, '');
  if (!last.text && runs.length > 1) runs.pop();

  const info = {
    shape: t.shapeType === 'box' ? 'box' : 'point',
    angle: round(angle),
    hscale: round(hscale, 4),                           // 1 = none; box.w and bbox are AFTER the squeeze
    justification: (pstyle.justification || 'left'),
    autoLeading: pstyle.autoLeading ?? 1.2,
    indent: round((pstyle.firstLineIndent ?? 0) * fs),
    orientation: t.orientation || 'horizontal',
    antiAlias: t.antiAlias !== 'none',
    runs,
  };
  if (info.shape === 'box') {
    const bb = t.boxBounds || [0, 0, layer.right - layer.left, layer.bottom - layer.top];
    const w = (bb[2] - bb[0]) * sx, h = (bb[3] - bb[1]) * sy;
    // page position of the box centre: the box is rotated about it in GIMP
    const mx = (bb[0] + bb[2]) / 2, my = (bb[1] + bb[3]) / 2;
    info.box = { w: round(w), h: round(h), cx: round(a * mx + c * my + tx), cy: round(b * mx + d * my + ty) };
  } else {
    info.origin = [round(tx), round(ty)];               // baseline start of the first line
  }
  return info;
}

function effectsInfo(layer, psd, text) {
  const e = layer.effects;
  if (!e || e.disabled) return null;
  const first = (v) => (Array.isArray(v) ? v : v ? [v] : []).find((x) => x && x.enabled !== false && x.present !== false);
  const out = {};
  const notes = [];
  const s = first(e.stroke);
  if (s) {
    if (s.fillType && s.fillType !== 'color') notes.push(`stroke fill "${s.fillType}" became a solid colour`);
    out.stroke = { size: px(s.size), color: hex(s.color), opacity: s.opacity ?? 1, position: s.position || 'outside' };
  }
  const glow = first(e.outerGlow);
  const sh = first(e.dropShadow) || null;
  if (sh) {
    const angle = ((sh.useGlobalLight && psd.imageResources && psd.imageResources.globalAngle != null
      ? psd.imageResources.globalAngle : sh.angle ?? 120) * Math.PI) / 180;
    const dist = px(sh.distance), size = px(sh.size);
    out.shadow = { x: round(-dist * Math.cos(angle)), y: round(dist * Math.sin(angle)), color: hex(sh.color),
      opacity: sh.opacity ?? 0.75, blur: size, grow: round((size * px(sh.choke)) / 100) };
    if (glow) notes.push('outer glow dropped (GIMP Text Styling has one shadow/glow slot, the drop shadow took it)');
  } else if (glow) {
    const size = px(glow.size);
    out.shadow = { x: 0, y: 0, color: hex(glow.color), opacity: glow.opacity ?? 0.75, blur: size,
      grow: round((size * px(glow.choke)) / 100), glow: true };
  }
  const fill = first(e.solidFill);
  if (fill) {
    const blend = fill.blendMode || 'normal';
    const color = hex(fill.color);
    // an opaque overlay in the colour the text already has changes nothing: no filter for it
    const same = blend === 'normal' && text && text.runs.every((r) => r.color === color);
    if (blend !== 'normal' && blend !== 'multiply') notes.push(`colour overlay with blend mode "${blend}" not converted`);
    else if (!same) {
      out.fill = { color, blend };
      if ((fill.opacity ?? 1) < 1) notes.push(`colour overlay opacity ${Math.round(fill.opacity * 100)}% applied at 100%`);
    }
  }
  for (const k of ['innerShadow', 'innerGlow', 'bevel', 'satin', 'gradientOverlay', 'patternOverlay']) {
    if (first(e[k])) notes.push(`layer style "${k}" not converted`);
  }
  if (!out.stroke && !out.shadow && !out.fill && !notes.length) return null;
  if (notes.length) out.notes = notes;
  return out;
}

// Photoshop saves an empty layer (a shape layer with no path) with bounds
// 0,0,-1,0; ag-psd refuses the whole file for it ("Invalid layer size"). Walk
// the layer records of the in-memory copy and collapse such a box to 0 x 0.
function fixEmptyLayerBounds(buf) {
  try {
    const big = buf.readUInt16BE(4) === 2;                // PSB: 8-byte lengths
    const len = (o) => (big ? Number(buf.readBigUInt64BE(o)) : buf.readUInt32BE(o));
    let o = 26;
    o += 4 + buf.readUInt32BE(o);                         // colour mode data
    o += 4 + buf.readUInt32BE(o);                         // image resources
    o += big ? 8 : 4;                                     // layer and mask section length
    const infoLen = len(o);
    o += big ? 8 : 4;
    if (!infoLen) return;
    const count = Math.abs(buf.readInt16BE(o));
    o += 2;
    for (let i = 0; i < count; i++) {
      const top = buf.readInt32BE(o), left = buf.readInt32BE(o + 4);
      if (buf.readInt32BE(o + 8) < top) buf.writeInt32BE(top, o + 8);
      if (buf.readInt32BE(o + 12) < left) buf.writeInt32BE(left, o + 12);
      o += 16;
      o += 2 + buf.readUInt16BE(o) * (big ? 10 : 6);      // channel id + data length
      o += 12;                                            // 8BIM, blend mode, opacity/clipping/flags/filler
      o += 4 + buf.readUInt32BE(o);                       // extra data
    }
  } catch {
    // not the layout expected here: leave the buffer alone, readPsd reports the file
  }
}

const data = fs.readFileSync(psdPath);
const readOptions = { skipLayerImageData: true, skipCompositeImageData: true, skipThumbnail: true };
let psd;
try {
  psd = readPsd(data, readOptions);
} catch (err) {
  if (!/Invalid layer size/.test(err.message)) throw err;
  fixEmptyLayerBounds(data);
  psd = readPsd(data, readOptions);
}
const layers = [];
const walk = (children, prefix) => {
  const n = children.length;
  children.forEach((l, i) => {
    const path = [...prefix, n - 1 - i];                // ag-psd lists bottom first, GIMP top first
    const text = l.text ? textInfo(l) : null;
    const effects = effectsInfo(l, psd, text);
    if (text || effects) {
      layers.push({ path, name: l.name || '', bbox: [l.left ?? 0, l.top ?? 0, l.right ?? 0, l.bottom ?? 0],
        group: !!l.children, text, effects });
    }
    if (l.children) walk(l.children, path);
  });
};
walk(psd.children || [], []);
fs.writeFileSync(outPath, JSON.stringify({ width: psd.width, height: psd.height, layers }, null, 1));
console.log(`${layers.filter((l) => l.text).length} text layer(s), ${layers.filter((l) => l.effects).length} styled layer(s)`);
