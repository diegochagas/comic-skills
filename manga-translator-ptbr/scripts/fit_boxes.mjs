// Sizes Photoshop paragraph text boxes to their text and pulls overlapping
// boxes apart. Pure geometry on ag-psd layer objects; used by
// build_translated_psd.mjs, set_text_layers.mjs, add_and_fill_text_layers.mjs
// (every time a translation goes into a box) and by fit_text_boxes.mjs on
// finished PSDs.
//
//   import { fitTextLayers } from './fit_boxes.mjs';
//   const changes = fitTextLayers(psd.children, psd.width, psd.height, { gap: 0 });
//
// Per text layer: the box keeps its centre and grows (never shrinks) until
// the text at the layer's font size fits - wider when a word is wider than
// the box, taller when the wrapped lines need more height (real font
// metrics, see text_metrics.mjs). Rotated boxes (spines, 90°) grow along
// their own axes. Then boxes whose TEXT (the lines actually written, which
// sit at the top of a paragraph box - the empty rest of a tall box covers
// nothing) would collide are moved apart along the axis of least overlap,
// both by half, round after round, and kept inside the canvas. Returns one
// record per layer that changed.
import { needsBox } from './text_metrics.mjs';

function boxOf(layer) {
  const t = layer.text;
  const [a, b, c, d, tx, ty] = t.transform || [1, 0, 0, 1, layer.left || 0, layer.top || 0];
  const bb = t.boxBounds || [0, 0, (layer.right || 0) - (layer.left || 0), (layer.bottom || 0) - (layer.top || 0)];
  return { a, b, c, d, tx, ty, bw: bb[2] - bb[0], bh: bb[3] - bb[1], x0: bb[0], y0: bb[1] };
}

function pageRect(box) {
  const { a, b, c, d, tx, ty, bw, bh, x0, y0 } = box;
  const pts = [[x0, y0], [x0 + bw, y0], [x0, y0 + bh], [x0 + bw, y0 + bh]].map(([x, y]) => [a * x + c * y + tx, b * x + d * y + ty]);
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  return { left: Math.min(...xs), top: Math.min(...ys), right: Math.max(...xs), bottom: Math.max(...ys) };
}

// page rectangle of the written text inside a box: top-aligned, as wide as
// its longest line, placed by the paragraph justification
function inkRect(box, ink) {
  const { a, b, c, d, tx, ty, bw, x0, y0 } = box;
  const w = Math.min(bw, ink.width), h = Math.min(box.bh, ink.height);
  const lx = ink.align === 'left' ? x0 : ink.align === 'right' ? x0 + bw - w : x0 + (bw - w) / 2;
  const pts = [[lx, y0], [lx + w, y0], [lx, y0 + h], [lx + w, y0 + h]].map(([x, y]) => [a * x + c * y + tx, b * x + d * y + ty]);
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  return { left: Math.min(...xs), top: Math.min(...ys), right: Math.max(...xs), bottom: Math.max(...ys) };
}

function applyBox(layer, box) {
  const t = layer.text;
  t.transform = [box.a, box.b, box.c, box.d, box.tx, box.ty];
  t.boxBounds = [box.x0, box.y0, box.x0 + box.bw, box.y0 + box.bh];
  t.left = box.x0; t.top = box.y0; t.right = box.x0 + box.bw; t.bottom = box.y0 + box.bh;
  const r = pageRect(box);
  // the cached render no longer matches: drop it and give the layer the
  // zero-size pixel bounds Photoshop itself saves for text layers (non-zero
  // bounds with empty channels crash GIMP's PSD loader); Photoshop re-renders
  // the layer on open
  normalizeTextLayer(layer, Math.round(r.left), Math.round(r.top));
}

export function normalizeTextLayer(layer, left, top) {
  layer.left = left ?? layer.left ?? 0; layer.top = top ?? layer.top ?? 0;
  layer.right = layer.left; layer.bottom = layer.top;
  delete layer.imageData; delete layer.imageDataRaw; delete layer.rawData; delete layer.canvas;
}

function layerFont(layer, fallbackFont) {
  const st = layer.text.style || {};
  const run = (layer.text.styleRuns || [])[0]?.style || {};
  return { name: (st.font && st.font.name) || (run.font && run.font.name) || fallbackFont, size: st.fontSize || run.fontSize || 12 };
}

export function fitTextLayers(children, W, H, opts = {}) {
  const gap = opts.gap ?? 0;
  const fallbackFont = opts.font || 'CCWildWords-Regular';
  const items = [];
  for (const layer of children || []) {
    if (!layer.text || !String(layer.text.text || '').trim()) continue;
    const box = boxOf(layer);
    const { name, size } = layerFont(layer, fallbackFont);
    const need = needsBox(layer.text.text, name, size, box.bw);
    const nbw = Math.max(box.bw, Math.ceil(need.longestWord + 2));
    const need2 = nbw > box.bw ? needsBox(layer.text.text, name, size, nbw) : need;
    const nbh = Math.max(box.bh, Math.ceil(need2.height));
    const align = (layer.text.paragraphStyle && layer.text.paragraphStyle.justification) || 'center';
    const rec = { layer, box, name: layer.name, grew: nbw > box.bw || nbh > box.bh, from: [box.bw, box.bh], to: [nbw, nbh], moved: [0, 0], lines: need2.lines, font: name, size, exact: need.exact,
      ink: { width: need2.width, height: need2.height, align } };
    if (rec.grew) {
      // keep the centre: new local centre must land where the old one did
      const cx = box.x0 + box.bw / 2, cy = box.y0 + box.bh / 2;
      const px = box.a * cx + box.c * cy + box.tx, py = box.b * cx + box.d * cy + box.ty;
      const ncx = box.x0 + nbw / 2, ncy = box.y0 + nbh / 2;
      box.bw = nbw; box.bh = nbh;
      box.tx = px - (box.a * ncx + box.c * ncy); box.ty = py - (box.b * ncx + box.d * ncy);
    }
    items.push(rec);
  }
  // keep the WRITTEN text on the canvas (a box may overhang the page edge on purpose)
  const clamp = (it) => {
    const r = inkRect(it.box, it.ink);
    let dx = 0, dy = 0;
    if (r.right - r.left <= W) { if (r.left < 0) dx = -r.left; else if (r.right > W) dx = W - r.right; }
    if (r.bottom - r.top <= H) { if (r.top < 0) dy = -r.top; else if (r.bottom > H) dy = H - r.bottom; }
    it.box.tx += dx; it.box.ty += dy; it.moved[0] += dx; it.moved[1] += dy;
  };
  items.forEach(clamp);
  // pull overlapping boxes apart
  for (let round = 0; round < 300; round++) {
    let moved = false;
    for (let i = 0; i < items.length; i++) {
      for (let j = i + 1; j < items.length; j++) {
        const A = inkRect(items[i].box, items[i].ink), B = inkRect(items[j].box, items[j].ink);
        const ox = Math.min(A.right, B.right) - Math.max(A.left, B.left) + gap;
        const oy = Math.min(A.bottom, B.bottom) - Math.max(A.top, B.top) + gap;
        // the text rectangle is an estimate (+-0.3 em; one em of overlap is tolerated): a title and its subtitle set
        // flush against each other touch, they do not cover each other
        const tol = 1.0 * Math.min(items[i].size, items[j].size);
        if (ox <= tol || oy <= tol) continue;
        const acx = (A.left + A.right) / 2, bcx = (B.left + B.right) / 2;
        const acy = (A.top + A.bottom) / 2, bcy = (B.top + B.bottom) / 2;
        let dx = 0, dy = 0;
        if (ox < oy) dx = (ox / 2 + 0.5) * (acx <= bcx ? -1 : 1);
        else dy = (oy / 2 + 0.5) * (acy <= bcy ? -1 : 1);
        items[i].box.tx += dx; items[i].box.ty += dy; items[i].moved[0] += dx; items[i].moved[1] += dy;
        items[j].box.tx -= dx; items[j].box.ty -= dy; items[j].moved[0] -= dx; items[j].moved[1] -= dy;
        clamp(items[i]); clamp(items[j]);
        moved = true;
      }
    }
    if (!moved) break;
  }
  const changes = [];
  for (const it of items) {
    const m = Math.hypot(it.moved[0], it.moved[1]);
    if (it.grew || m > 1.5) {
      applyBox(it.layer, it.box);
      changes.push({ name: it.name, from: it.from, to: it.to, moved: [Math.round(it.moved[0]), Math.round(it.moved[1])], lines: it.lines, font: it.font, size: it.size, fontFound: it.exact });
    }
  }
  // what still overlaps (boxes bigger than the space available)
  const left = [];
  for (let i = 0; i < items.length; i++) for (let j = i + 1; j < items.length; j++) {
    const A = inkRect(items[i].box, items[i].ink), B = inkRect(items[j].box, items[j].ink);
    const tol = 1.0 * Math.min(items[i].size, items[j].size);
    if (Math.min(A.right, B.right) - Math.max(A.left, B.left) > tol && Math.min(A.bottom, B.bottom) - Math.max(A.top, B.top) > tol) left.push([items[i].name, items[j].name]);
  }
  return { changes, overlapping: left };
}

export function describeChanges(result) {
  const out = [];
  for (const c of result.changes) {
    const parts = [];
    if (c.to[0] !== c.from[0] || c.to[1] !== c.from[1]) parts.push(`${c.from[0]}x${c.from[1]} -> ${c.to[0]}x${c.to[1]} (${c.lines} line${c.lines === 1 ? '' : 's'} at ${c.size}px)`);
    if (c.moved[0] || c.moved[1]) parts.push(`moved ${c.moved[0]},${c.moved[1]}`);
    if (!c.fontFound) parts.push(`font ${c.font} not installed, measured with DejaVu`);
    out.push(`  ${c.name}: ${parts.join('; ')}`);
  }
  for (const [a, b] of result.overlapping) out.push(`  STILL OVERLAP: ${a} / ${b}`);
  return out.join('\n');
}
