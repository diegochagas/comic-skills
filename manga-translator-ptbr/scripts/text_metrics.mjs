// Real font metrics for Photoshop paragraph text boxes (node-canvas + the
// font files fontconfig knows), so a box can be sized to show ALL of its
// text instead of clipping the lines that do not fit.
//
//   import { needsBox } from './text_metrics.mjs';
//   needsBox(text, fontPostScriptName, fontSizePx, boxWidthPx)
//     -> { lines, width, height, longestWord, font }   (px)
//
// Photoshop lays a paragraph box out as: first baseline at the font's
// ascent, then one line every `leading` (auto leading = 120 % of the size),
// and hides any line whose bottom does not fit. The estimate here is greedy
// word wrap at the box width minus a small safety margin (Photoshop's
// every-line composer and metrics kerning differ slightly), height =
// ascent + (lines - 1) * leading + 1 px (calibrated on boxes Diego sized by
// hand in Photoshop). A word wider
// than the box is reported in `longestWord` so the caller can widen the box.
// Fonts: the PostScript name is resolved with `fc-match`; when it is not
// installed, DejaVu Sans Bold stands in with a wider margin.
import { execFileSync } from 'child_process';
import { registerFont, createCanvas } from 'canvas';

const LEADING = 1.2;          // Photoshop auto leading
const SAFETY_W = 0.96;        // usable fraction of the box width
const FALLBACK = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf';
const registered = new Map();   // ps name -> { family, exact }
let ctx = null;

function fontFile(psName) {
  // exact PostScript name first; then the family (a layer that only says
  // "CCWildWords" means the Regular face)
  const family = psName.split('-')[0];
  const tries = [[`:postscriptname=${psName}`, (got) => got.toLowerCase() === psName.toLowerCase()],
                 [`:family=${family}:style=Regular`, (got, fam) => fam.replace(/\s+/g, '').toLowerCase() === family.toLowerCase()]];
  for (const [pattern, ok] of tries) {
    try {
      const out = execFileSync('fc-match', ['-f', '%{file}|%{postscriptname}|%{family}', pattern], { encoding: 'utf8' }).trim();
      const [file, got, fam] = out.split('|');
      if (ok(got || '', fam || '')) return { file, exact: true };
    } catch (e) { /* fontconfig missing */ }
  }
  return { file: FALLBACK, exact: false };
}

export function fontFamily(psName) {
  if (!registered.has(psName)) {
    const { file, exact } = fontFile(psName);
    const family = `psd-${registered.size}-${psName.replace(/[^A-Za-z0-9]/g, '')}`;
    try { registerFont(file, { family }); } catch (e) { registerFont(FALLBACK, { family }); }
    registered.set(psName, { family, exact });
  }
  return registered.get(psName);
}

function context() {
  if (!ctx) ctx = createCanvas(16, 16).getContext('2d');
  return ctx;
}

export function measure(text, psName, size) {
  const { family } = fontFamily(psName);
  const c = context();
  c.font = `${size}px "${family}"`;
  return c.measureText(text);
}

export function wrapLines(text, psName, size, width) {
  const { family, exact } = fontFamily(psName);
  const c = context();
  c.font = `${size}px "${family}"`;
  const usable = width * (exact ? SAFETY_W : SAFETY_W - 0.06);
  const lines = [];
  let longest = 0;
  for (const para of String(text).split(/\r?\n/)) {
    const words = para.split(/\s+/).filter(Boolean);
    if (!words.length) { lines.push(''); continue; }
    let cur = '';
    for (const w of words) {
      longest = Math.max(longest, c.measureText(w).width);
      const cand = cur ? cur + ' ' + w : w;
      if (c.measureText(cand).width <= usable || !cur) cur = cand;
      else { lines.push(cur); cur = w; }
    }
    lines.push(cur);
  }
  return { lines, longest };
}

export function needsBox(text, psName, size, width) {
  const { lines, longest } = wrapLines(text, psName, size, width);
  const { family, exact } = fontFamily(psName);
  const c = context();
  c.font = `${size}px "${family}"`;
  const m = c.measureText('Hgjp');
  const ascent = m.emHeightAscent || size * 0.95, descent = m.emHeightDescent || size * 0.25;
  // Photoshop shows a line once its baseline is inside the box (the last
  // line's descenders hang out); measured on boxes Diego sized by hand:
  // 2 lines of 41.7 px ObelixPro (ascent 1.08 em) live in 95.8 px
  const height = ascent + (lines.length - 1) * size * LEADING + 1;
  const lineW = Math.max(0, ...lines.map((l) => c.measureText(l).width));
  const margin = exact ? SAFETY_W : SAFETY_W - 0.06;
  return { lines: lines.length, width: lineW / margin, height, longestWord: longest / margin, font: family, exact, ascent, descent };
}
