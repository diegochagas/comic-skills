#!/usr/bin/env python3
"""Detect ALL text strokes in manga pages with comic-text-detector (ONNX) and
erase them, filling each with the predominant color around it.

Usage: <repo>/venv/bin/python detect_text.py <out_dir> <image> [<image>...]

Only actual text is touched: the model's text-BLOCK detection head gates its
stroke segmentation, so stroke-like false positives on art (ornamental
borders, screentone, hatching) that fall outside every detected text block
are left untouched. Each erased region is filled by surroundings:
  - one plain color (white bubble, black box, grey caption, red banner, ...)
                    -> filled with that exact sampled color ("fill")
  - busy (art, screentone, gradients)
                    -> strokes inpainted from the surrounding pixels ("inpaint")

Nothing is left for manual review; restoration of anything worth keeping is
done by hand from the "Original" layer of the PSD.

For each page writes into <out_dir>/detect/:
  <stem>_cleaned.png   the page with every detected text stroke erased —
                       this becomes the PSD's "Cleaned" layer
  <stem>_mask.png      union stroke mask of everything that was touched
  <stem>_overlay.jpg   original tinted where strokes were detected
                       (red = solid color fill, green = inpainted,
                        yellow = detected strokes outside any text block,
                        left untouched)
  <stem>_detect.json   text block boxes + erased component bboxes + method +
                       sampled bg_color (hex, for "fill" components), in page
                       pixel coords
"""
import json
import os
import sys

import cv2
import numpy as np
import onnxruntime as ort

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inpaint_lama  # noqa: E402  (LaMa "generative fill" for busy surroundings)

MODEL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models", "comictextdetector.pt.onnx")  # <skill>/models
SIZE = 1024
SEG_THRESH = 0.3
SEG_LOW = 0.1          # hysteresis: faint strokes kept if near a confident one
SEG_NEAR = 10          # ...but only within this many px of a confident stroke
DILATE_PX = 3          # cover anti-aliased stroke edges
MIN_AREA = 24          # drop specks
RING_IN, RING_OUT = 3, 10   # background sampling ring around a component
COLOR_TOL = 22         # max per-pixel color distance from the ring's median color
PLAIN_FRAC = 0.90      # fraction of ring pixels within COLOR_TOL for a solid fill
INPAINT_RADIUS = 4     # cv2.inpaint radius (fallback when LaMa model is missing)
BLK_CONF = 0.4         # min confidence for a text block box
BLK_IOU = 0.4          # NMS threshold for text block boxes
BLK_PAD = 8            # px padding around each text block box
DET_THRESH = 0.4       # min prob in the DBNet-style text-line map ("det" head)
DET_DILATE = 6         # px dilation of the text-line map
BLK_OVERLAP = 0.3      # min fraction of a stroke component inside text regions
BLK_SPILL = 0.8        # a component overlapping a block box with less than this
                       # fraction inside it is text merged with art strokes
                       # (hair, hatching): only its part inside the boxes is erased


def letterbox(img):
    h, w = img.shape[:2]
    scale = SIZE / max(h, w)
    nh, nw = round(h * scale), round(w * scale)
    resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((SIZE, SIZE, 3), dtype=np.uint8)
    canvas[:nh, :nw] = resized
    return canvas, scale, nh, nw


def text_blocks(blk, scale, w, h):
    """Decode the model's YOLO-style block head into [x, y, w, h] page-pixel
    boxes of detected text blocks (already sigmoid'ed/grid-decoded on export)."""
    score = blk[:, 4] * blk[:, 5:].max(axis=1)
    cand = blk[score >= BLK_CONF]
    if not len(cand):
        return []
    boxes = np.stack([cand[:, 0] - cand[:, 2] / 2, cand[:, 1] - cand[:, 3] / 2,
                      cand[:, 2], cand[:, 3]], axis=1) / scale
    keep = cv2.dnn.NMSBoxes(boxes.tolist(), score[score >= BLK_CONF].tolist(),
                            BLK_CONF, BLK_IOU)
    out = []
    for i in np.array(keep).flatten():
        x, y, bw, bh = boxes[i]
        x0, y0 = max(0, int(x - BLK_PAD)), max(0, int(y - BLK_PAD))
        x1, y1 = min(w, int(x + bw + BLK_PAD)), min(h, int(y + bh + BLK_PAD))
        if x1 > x0 and y1 > y0:
            out.append([x0, y0, x1 - x0, y1 - y0])
    return out


def detect_page(sess, path, out_dir):
    stem = os.path.splitext(os.path.basename(path))[0]
    img = cv2.imread(path)
    if img is None:
        raise RuntimeError(f"cannot read {path}")
    h, w = img.shape[:2]
    canvas, scale, nh, nw = letterbox(img)
    inp = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
    blk, seg, det = sess.run(["blk", "seg", "det"],
                             {"images": np.ascontiguousarray(inp)})
    blocks = text_blocks(blk[0], scale, w, h)
    seg = seg[0, 0]
    det = det[0, 0]
    if det.min() < -0.01 or det.max() > 1.01:      # logits -> sigmoid
        det = 1.0 / (1.0 + np.exp(-det))
    if seg.min() < -0.01 or seg.max() > 1.01:      # logits -> sigmoid
        seg = 1.0 / (1.0 + np.exp(-seg))
    def to_full(th):
        m = (seg[:nh, :nw] > th).astype(np.uint8) * 255
        m = cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR)
        return (m > 127).astype(np.uint8) * 255

    # hysteresis: faint strokes are kept only within SEG_NEAR px of a confident
    # detection — extends glyphs to their faint edges without letting long
    # low-confidence runs (e.g. along decorative borders) merge in whole areas
    high, low = to_full(SEG_THRESH), to_full(SEG_LOW)
    near = cv2.dilate(high, np.ones((2 * SEG_NEAR + 1,) * 2, np.uint8))
    mask = low & near
    if DILATE_PX:
        mask = cv2.dilate(mask, np.ones((2 * DILATE_PX + 1,) * 2, np.uint8))

    os.makedirs(out_dir, exist_ok=True)

    # text regions = union of block boxes and the dilated text-line map — the
    # blk head misses some blocks (vertical captions, light-on-dark sidebars)
    # that the det head catches, and vice versa
    allow = np.zeros((h, w), np.uint8)
    for bx, by, bbw, bbh in blocks:
        allow[by:by + bbh, bx:bx + bbw] = 255
    blk_boxes = allow.copy()
    det_mask = (det[:nh, :nw] > DET_THRESH).astype(np.uint8) * 255
    det_mask = cv2.resize(det_mask, (w, h), interpolation=cv2.INTER_LINEAR)
    det_mask = (det_mask > 127).astype(np.uint8) * 255
    if DET_DILATE:
        det_mask = cv2.dilate(det_mask, np.ones((2 * DET_DILATE + 1,) * 2, np.uint8))
    allow |= det_mask

    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    cleaned = img.copy()
    inpaint_mask = np.zeros((h, w), np.uint8)
    fill_tint = np.zeros((h, w), np.uint8)
    skip_tint = np.zeros((h, w), np.uint8)
    comps = []
    skipped = 0
    k_in = np.ones((2 * RING_IN + 1,) * 2, np.uint8)
    k_out = np.ones((2 * RING_OUT + 1,) * 2, np.uint8)
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < MIN_AREA:
            continue
        # only erase strokes that sit (mostly) inside a detected text block —
        # stroke-like false positives on art stay untouched
        inside = allow[y:y + bh, x:x + bw][labels[y:y + bh, x:x + bw] == i]
        if (inside > 0).mean() < BLK_OVERLAP:
            skip_tint[y:y + bh, x:x + bw][labels[y:y + bh, x:x + bw] == i] = 255
            skipped += 1
            continue
        # text merged with art strokes: keep only the part inside the block boxes
        lab = labels[y:y + bh, x:x + bw]
        in_blk = blk_boxes[y:y + bh, x:x + bw][lab == i] > 0
        if in_blk.any() and in_blk.mean() < BLK_SPILL:
            spill = (lab == i) & (blk_boxes[y:y + bh, x:x + bw] == 0)
            skip_tint[y:y + bh, x:x + bw][spill] = 255
            lab[spill] = 0
            area = int((lab == i).sum())
        # background ring around this component, computed on a padded crop,
        # excluding every detected stroke (not just this component's)
        pad = RING_OUT + 2
        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1, y1 = min(w, x + bw + pad), min(h, y + bh + pad)
        comp = (labels[y0:y1, x0:x1] == i).astype(np.uint8)
        ring = cv2.dilate(comp, k_out) & ~cv2.dilate(comp, k_in) & ~(mask[y0:y1, x0:x1] > 0)
        bg = img[y0:y1, x0:x1][ring > 0].astype(np.float32)   # BGR ring pixels
        median = np.median(bg, axis=0) if bg.size else None
        uniform = (bg.size > 0 and
                   (np.linalg.norm(bg - median, axis=1) <= COLOR_TOL).mean() >= PLAIN_FRAC)
        comp_out = {"bbox": [int(x), int(y), int(bw), int(bh)], "area": int(area)}
        sel = comp > 0     # bbox-slice ops only: full-page `labels == i` OOMs on huge scans
        if uniform:
            comp_out["method"] = "fill"
            cleaned[y0:y1, x0:x1][sel] = median.round().clip(0, 255).astype(np.uint8)
            fill_tint[y0:y1, x0:x1][sel] = 255
            b, g, r = median.round().astype(int).clip(0, 255)
            comp_out["bg_color"] = f"#{r:02x}{g:02x}{b:02x}"
        else:
            comp_out["method"] = "inpaint"
            inpaint_mask[y0:y1, x0:x1][sel] = 255
        comps.append(comp_out)

    del labels
    if inpaint_mask.any():
        # busy surroundings: LaMa inpainting (<skill>/models/lama_fp32.onnx) when
        # available, cv2.inpaint (Telea) otherwise. Solid fills above untouched.
        cleaned = inpaint_lama.inpaint(cleaned, inpaint_mask, inplace=True)

    cv2.imwrite(os.path.join(out_dir, f"{stem}_mask.png"), fill_tint | inpaint_mask)
    cleaned_path = os.path.join(out_dir, f"{stem}_cleaned.png")
    cv2.imwrite(cleaned_path, cleaned)

    overlay = img.copy()
    for m, tint in ((fill_tint, (0, 0, 255)), (inpaint_mask, (0, 200, 0)),
                    (skip_tint, (0, 255, 255))):
        if m.any():
            overlay[m > 0] = 0.25 * overlay[m > 0] + 0.75 * np.array(tint)
    cv2.imwrite(os.path.join(out_dir, f"{stem}_overlay.jpg"), overlay,
                [cv2.IMWRITE_JPEG_QUALITY, 88])

    out = {"source": os.path.abspath(path), "size": [w, h],
           "text_mask": os.path.join(out_dir, f"{stem}_mask.png"),
           "cleaned": cleaned_path, "text_blocks": blocks,
           "components": comps, "skipped_non_text": skipped,
           "inpaint_method": inpaint_lama.method_name()}
    with open(os.path.join(out_dir, f"{stem}_detect.json"), "w") as f:
        json.dump(out, f, indent=1)
    print(f"{stem}: {len(blocks)} text blocks, {len(comps)} components erased "
          f"({sum(c['method'] == 'fill' for c in comps)} fill, "
          f"{sum(c['method'] == 'inpaint' for c in comps)} inpaint), "
          f"{skipped} non-text skipped")


def make_session():
    # denormal-as-zero: this model's weights produce many denormal floats and
    # onnxruntime's default (DAZ off) makes one inference ~60x slower (90 s vs
    # 1.4 s measured 2026-09-03); output is unaffected
    so = ort.SessionOptions()
    so.add_session_config_entry("session.set_denormal_as_zero", "1")
    return ort.InferenceSession(MODEL, so, providers=["CPUExecutionProvider"])


def main():
    out_dir = os.path.join(sys.argv[1], "detect")
    sess = make_session()
    failed = []
    for path in sys.argv[2:]:
        try:
            detect_page(sess, path, out_dir)
        except Exception as e:
            print(f"SKIP {path}: {e}")
            failed.append(path)
    if failed:
        print(f"Skipped {len(failed)} image(s): "
              + ", ".join(os.path.basename(p) for p in failed))


if __name__ == "__main__":
    main()
