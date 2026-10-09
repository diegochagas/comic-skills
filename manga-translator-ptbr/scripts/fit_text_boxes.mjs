#!/usr/bin/env node
// Makes every text box of a PSD (or of every PSD in a folder) big enough to
// show its whole text at the box's own font size, and moves boxes that
// cover each other apart (see fit_boxes.mjs). Rasters and everything else
// stay byte-exact; only the geometry of the changed text layers is written.
// The merged preview image is rebuilt from the top raster layer (ag-psd
// cannot carry the original one over and would write a black preview).
//
// Usage: node fit_text_boxes.mjs <psd | folder> [--from STEM] [--only STEM ...]
//            [--output DIR | --in-place] [--gap 0] [--dry-run]
//   --from 150   only stems that sort at or after "150" (page numbers)
//   --output     default ~/Downloads/<folder name> textfit/ (COMIC_OUTPUT_DIR
//                replaces ~/Downloads); --in-place rewrites the input PSDs
//   --dry-run    print what would change, write nothing
//   --rewrite-all  write every file, even when no box changes (every text layer
//                gets zero-size pixel bounds, so GIMP can open the file too)
import * as fs from 'fs';
import * as path from 'path';
import { readPsd, writePsdBuffer, initializeCanvas } from 'ag-psd';
import { createCanvas, createImageData } from 'canvas';
import { fitTextLayers, describeChanges, normalizeTextLayer } from './fit_boxes.mjs';

initializeCanvas(createCanvas, createImageData);

const args = process.argv.slice(2);
const src = args.find((a) => !a.startsWith('--') && !(args[args.indexOf(a) - 1] || '').startsWith('--') || args.indexOf(a) === 0);
const opt = (n, d) => { const i = args.indexOf(n); return i !== -1 ? args[i + 1] : d; };
const flag = (n) => args.includes(n);
if (!src) { console.error('usage: fit_text_boxes.mjs <psd|folder> [--from STEM] [--only STEM ...] [--output DIR | --in-place] [--gap N] [--dry-run]'); process.exit(1); }

const from = opt('--from', null);
const onlyI = args.indexOf('--only');
const only = onlyI === -1 ? null : new Set(args.slice(onlyI + 1).filter((a) => !a.startsWith('--')).reduce((acc, a) => { if (acc.stop || fs.existsSync(a) && a === src) acc.stop = true; else acc.push(a); return acc; }, Object.assign([], { stop: false })));
const gap = Number(opt('--gap', 0));
const inPlace = flag('--in-place');
const dry = flag('--dry-run');
const rewriteAll = flag('--rewrite-all');
const stat = fs.statSync(src);
const files = stat.isDirectory()
  ? fs.readdirSync(src).filter((n) => /\.psd$/i.test(n)).sort().map((n) => path.join(src, n))
  : [src];
const root = process.env.COMIC_OUTPUT_DIR || path.join(process.env.HOME || '', 'Downloads');
const outDir = inPlace ? null : (opt('--output', null) || path.join(root, `${path.basename(stat.isDirectory() ? src : path.dirname(src))} textfit`));
const key = (f) => path.basename(f, path.extname(f));
const selected = files.filter((f) => (!from || key(f).localeCompare(from, undefined, { numeric: true }) >= 0) && (!only || only.has(key(f))));
console.log(`${selected.length} PSD(s)${dry ? ' (dry run)' : inPlace ? ' (in place)' : ` -> ${outDir}`}`);
let changedFiles = 0;
for (const f of selected) {
  let psd;
  let buf;
  try { buf = fs.readFileSync(f); psd = readPsd(buf, { useRawData: true, skipCompositeImageData: true, skipThumbnail: true }); }
  catch (e) { console.log(`${path.basename(f)}: FAIL ${e.message}`); continue; }
  const res = fitTextLayers(psd.children, psd.width, psd.height, { gap });
  const n = (psd.children || []).filter((l) => l.text).length;
  if (!res.changes.length && !rewriteAll) { console.log(`${path.basename(f)}: ${n} text layer(s), nothing to change`); continue; }
  console.log(`${path.basename(f)}: ${res.changes.length}/${n} text layer(s) changed`);
  if (res.changes.length) console.log(describeChanges(res));
  changedFiles++;
  if (dry) continue;
  // every text layer gets Photoshop's zero-size pixel bounds (GIMP crashes on
  // non-zero bounds with empty channels); its render is rebuilt on open anyway
  for (const l of psd.children || []) if (l.text) normalizeTextLayer(l);
  const out = inPlace ? f : path.join(outDir, path.basename(f));
  psd.imageData = compositeFromTopRaster(buf, psd.width, psd.height);
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.writeFileSync(out, writePsdBuffer(psd, { generateThumbnail: false }));
}
console.log(`done: ${changedFiles} file(s) ${dry ? 'would change' : 'written'}`);

// The merged preview: the topmost full raster (the lettered / cleaned art)
// on white. A second, decoded read of the file - the raw read used for the
// byte-exact rewrite has no pixels to compose from.
function compositeFromTopRaster(buf, W, H) {
  try {
    const q = readPsd(buf, { useImageData: true, skipCompositeImageData: true, skipThumbnail: true });
    const t = (q.children || []).filter((l) => !l.text && l.imageData && l.imageData.width > 0).pop();
    if (!t) return undefined;
    const c = createCanvas(W, H); const ctx = c.getContext('2d');
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, W, H);
    ctx.putImageData(createImageData(new Uint8ClampedArray(t.imageData.data.buffer, t.imageData.data.byteOffset, t.imageData.data.byteLength), t.imageData.width, t.imageData.height), t.left || 0, t.top || 0);
    const d = ctx.getImageData(0, 0, W, H);
    return { data: new Uint8Array(d.data.buffer, d.data.byteOffset, d.data.byteLength), width: W, height: H };
  } catch (e) { return undefined; }
}
