// Turns a raster layer into an embedded Photoshop Smart Object: the layer
// keeps its pixels as the preview and the image file rides inside the PSD
// (Layer > Smart Objects > Edit Contents opens it). Used for the "Original"
// layer, which is kept hidden and protected from accidental painting.
import * as crypto from 'crypto';
import { createCanvas, createImageData } from 'canvas';

/** PNG bytes of an ag-psd imageData ({data RGBA, width, height}).
 *  Example: pngOf(layer.imageData) -> Buffer */
export function pngOf(imageData) {
  const { data, width, height } = imageData;
  const canvas = createCanvas(width, height);
  const clamped = new Uint8ClampedArray(data.buffer, data.byteOffset, data.byteLength);
  canvas.getContext('2d').putImageData(createImageData(clamped, width, height), 0, 0);
  return canvas.toBuffer('image/png');
}

/** Makes `layer` (with imageData and left/top/right/bottom) a Smart Object
 *  whose contents are `png` (default: the layer's own pixels), registered in
 *  `psd.linkedFiles`. Returns the layer.
 *  Example: asSmartObject(psd, original, { hidden: true }) */
export function asSmartObject(psd, layer, { png, fileName, hidden = false } = {}) {
  if (!layer.imageData) throw new Error(`smart object "${layer.name}": layer has no pixels (expected a raster layer)`);
  const id = crypto.randomUUID();
  const { left, top, right, bottom } = layer;
  layer.placedLayer = {
    id, type: 'raster', width: right - left, height: bottom - top,
    transform: [left, top, right, top, right, bottom, left, bottom],
  };
  psd.linkedFiles = [...(psd.linkedFiles || []),
    { id, name: fileName || `${layer.name}.png`, type: 'png', data: new Uint8Array(png || pngOf(layer.imageData)) }];
  if (hidden) layer.hidden = true;
  return layer;
}
