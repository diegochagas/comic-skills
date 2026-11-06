#!/usr/bin/env node
// Copies one layer of a PSD (typically an adjustment layer such as "Levels 1")
// to the top of every PSD in a folder, leaving all other layers byte-exact
// (raw channel data, same as set_text_layers.mjs).
//
// Usage: node copy_layer_to_psds.mjs <source.psd> <layer name> <folder> [--from STEM] [--output DIR | --in-place]
//   --from 006     only PSDs whose stem sorts at or after 006
//   --output DIR   default ~/Downloads/<folder name> textlayer ; --in-place rewrites the inputs
// Example: node manga-translator-ptbr/scripts/copy_layer_to_psds.mjs 005.psd "Levels 1" ./pages --from 006 --in-place
// A PSD that already has a layer with that name is skipped (rerun-safe).
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { readPsd, writePsdBuffer } from 'ag-psd';

const READ = { useRawData: true };

/** Reads the named layer from the source PSD, dropping its id so ag-psd assigns a fresh one. */
function loadSourceLayer(sourcePath, layerName) {
  const layer = readPsd(fs.readFileSync(sourcePath), READ).children.find((l) => l.name === layerName);
  if (!layer) throw new Error(`layer "${layerName}" not found in ${sourcePath}`);
  const copy = structuredClone(layer);
  delete copy.id;
  return copy;
}

/** Returns the output path for one input file, or null when the layer is already there. */
function addLayerTo(file, layer, outPath) {
  const psd = readPsd(fs.readFileSync(file), READ);
  if (psd.children.some((l) => l.name === layer.name)) return false;
  psd.children.push(structuredClone(layer));
  fs.writeFileSync(outPath, writePsdBuffer(psd, { generateThumbnail: false }));
  return true;
}

function parseArgs(argv) {
  const opts = { from: '', output: null, inPlace: false };
  const rest = [];
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--from') opts.from = argv[++i];
    else if (argv[i] === '--output') opts.output = argv[++i];
    else if (argv[i] === '--in-place') opts.inPlace = true;
    else rest.push(argv[i]);
  }
  if (rest.length !== 3) throw new Error('usage: copy_layer_to_psds.mjs <source.psd> <layer name> <folder> [--from STEM] [--output DIR | --in-place]');
  return { source: rest[0], layerName: rest[1], folder: rest[2], ...opts };
}

function main() {
  const a = parseArgs(process.argv.slice(2));
  const layer = loadSourceLayer(a.source, a.layerName);
  const outDir = a.inPlace ? a.folder : (a.output ?? path.join(os.homedir(), 'Downloads', `${path.basename(a.folder)} ${a.layerName}`));
  fs.mkdirSync(outDir, { recursive: true });
  const stems = fs.readdirSync(a.folder).filter((f) => /\.psd$/i.test(f) && path.parse(f).name >= a.from).sort();
  for (const f of stems) {
    const done = addLayerTo(path.join(a.folder, f), layer, path.join(outDir, f));
    console.log(`${f}: ${done ? 'added' : 'already has it, skipped'}`);
  }
}

main();
