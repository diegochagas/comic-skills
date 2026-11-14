#!/usr/bin/env python3
"""Erase the text inside blocks found by detect_blocks.py (Mode B) and write a
cleaned page for the PSD "Copy" layer.

Usage: python clean_blocks.py <detect_json> [<detect_json> ...] [--pad 6]

For every <stem>_detect.json (from detect_blocks.py) writes, next to it:
  <stem>_cleaned.png   page with the strokes inside every text block erased
  <stem>_clean_overlay.jpg   red = solid fill, green = inpainted

Per block the comic-text-detector is run on a crop of that block (letterboxed
to 1024 px), so its stroke segmentation sees the text at block scale — big
display titles that the page-scale pass blurs away are segmented cleanly.
Each stroke component is then erased exactly like detect_text.py does: a
solid fill with the sampled surrounding color when the ring around it is
uniform, AI-inpainted with LaMa (see inpaint_lama.py; cv2.inpaint fallback)
otherwise. Pixels outside the blocks are never touched.
"""
import argparse
import json
import os
import sys
import time

import cv2
import numpy as np
import onnxruntime as ort

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import detect_text as dt  # noqa: E402  (constants + MODEL path)
import inpaint_lama  # noqa: E402  (LaMa "generative fill" for busy surroundings)

CROP_PAD = 24      # context around the block given to the model
LOW_COVERAGE = 0.01    # stroke-mask share of a box below which the colour-cluster fallback runs
HIGH_COVERAGE = 0.4    # ...and above which the segmentation is treated as noise and replaced by it
INK_TOL = 28       # a pixel is "ink" if its gray differs from the local background by more than this
INK_GROW = 3       # px of anti-aliased edge kept around ink pixels
STROKE_NEAR = 20   # plain-bg blocks: art running through the box edge is erased only within this many px of a detected stroke
MIN_CROP = 256     # tiny blocks are padded up to this so the model sees context


def seg_mask_for_crop(sess, crop):
    """Stroke mask (uint8 0/255) for a BGR crop, model run at crop scale."""
    h, w = crop.shape[:2]
    canvas, scale, nh, nw = dt.letterbox(crop)
    inp = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
    seg = sess.run(["seg"], {"images": np.ascontiguousarray(inp)})[0][0, 0]
    if seg.min() < -0.01 or seg.max() > 1.01:
        seg = 1.0 / (1.0 + np.exp(-seg))

    def to_full(th):
        m = (seg[:nh, :nw] > th).astype(np.uint8) * 255
        m = cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR)
        return (m > 127).astype(np.uint8) * 255

    high, low = to_full(dt.SEG_THRESH), to_full(dt.SEG_LOW)
    near = cv2.dilate(high, np.ones((2 * dt.SEG_NEAR + 1,) * 2, np.uint8))
    mask = low & near
    if dt.DILATE_PX:
        mask = cv2.dilate(mask, np.ones((2 * dt.DILATE_PX + 1,) * 2, np.uint8))
    return mask


def colour_cluster_text(reg, border_med, k=4):
    """Text mask (uint8) for a block the detector missed: k-means on the
    block's colours; clusters far from the border colour that cover a
    plausible share of the box are text.  None when nothing qualifies."""
    h, w = reg.shape[:2]
    step = max(1, int(np.sqrt(h * w / 20000)))
    sample = reg[::step, ::step].reshape(-1, 3).astype(np.float32)
    if len(sample) < 50:
        return None
    _, labels, centers = cv2.kmeans(sample, k, None,
                                    (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0),
                                    2, cv2.KMEANS_PP_CENTERS)
    share = np.bincount(labels.flatten(), minlength=k) / len(sample)
    dist = np.linalg.norm(centers - border_med, axis=1)
    text = [i for i in range(k) if dist[i] > 2.5 * dt.COLOR_TOL and 0.01 <= share[i] <= 0.5]
    if not text:
        return None
    # assign every pixel to its nearest centre (chunked to stay in memory)
    out = np.zeros((h, w), np.uint8)
    flat = reg.reshape(-1, 3).astype(np.float32)
    lab = np.empty(len(flat), np.int32)
    for i in range(0, len(flat), 500000):
        d = ((flat[i:i + 500000, None, :] - centers[None, :, :]) ** 2).sum(2)
        lab[i:i + 500000] = d.argmin(1)
    sel = np.isin(lab, text).reshape(h, w)
    out[sel] = 255
    out = cv2.morphologyEx(out, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    grow = max(2, min(h, w) // 150)          # cover anti-aliased edges (~6 px at 600 dpi)
    out = cv2.dilate(out, np.ones((2 * grow + 1,) * 2, np.uint8))
    return out if out.any() else None


def clean_page(sess, det_path, pad, deadline=None):
    d = json.load(open(det_path))
    src = d.get("source_for_psd") or d["source"]
    out_dir = os.path.dirname(det_path)
    stem = os.path.basename(det_path)[:-len("_detect.json")]
    cleaned_path = os.path.join(out_dir, f"{stem}_cleaned.png")
    pending_path = os.path.join(out_dir, f"{stem}_pending_mask.png")
    if os.path.exists(pending_path):
        # resume: only the LaMa regions left over from a previous time-boxed run
        cleaned = cv2.imread(cleaned_path)
        pend = cv2.imread(pending_path, cv2.IMREAD_GRAYSCALE)
        left = np.zeros_like(pend)
        cleaned = inpaint_lama.inpaint(cleaned, pend, inplace=True, deadline=deadline, pending=left)
        cv2.imwrite(cleaned_path, cleaned)
        if left.any():
            cv2.imwrite(pending_path, left)
            print(f"{stem}: PARTIAL (resumed), {int((left > 0).sum())} mask px still pending -> rerun")
        else:
            os.remove(pending_path)
            print(f"{stem}: {len(d['text_blocks'])} blocks, resumed inpainting finished -> {cleaned_path}")
        return
    img = cv2.imread(src)
    if img is None:
        raise RuntimeError(f"cannot read {src}")
    H, W = img.shape[:2]
    blocks = d["text_blocks"]

    # 1. stroke mask restricted to the (padded) blocks: union of the page-scale
    #    segmentation (best for small print) and a per-block crop-scale pass
    #    (best for large display text)
    page_mask = seg_mask_for_crop(sess, img)
    mask = np.zeros((H, W), np.uint8)
    plain = []   # (block, bg color) for blocks on a uniform background
    for bx, by, bw, bh in blocks:
        # crop with context; grow small blocks so the model has something to see
        ex = max(CROP_PAD, (MIN_CROP - bw) // 2)
        ey = max(CROP_PAD, (MIN_CROP - bh) // 2)
        cx0, cy0 = max(0, bx - ex), max(0, by - ey)
        cx1, cy1 = min(W, bx + bw + ex), min(H, by + bh + ey)
        if max(bw, bh) > 300 and (min(bw, bh) > 100 or len(blocks) <= 40):   # crop pass (skipped for thin columns on very dense pages)
            m = seg_mask_for_crop(sess, img[cy0:cy1, cx0:cx1])
        else:
            m = np.zeros((cy1 - cy0, cx1 - cx0), np.uint8)
        # keep only strokes inside the block (+pad)
        keep = np.zeros_like(m)
        kx0, ky0 = max(0, bx - pad - cx0), max(0, by - pad - cy0)
        kx1, ky1 = min(cx1, bx + bw + pad) - cx0, min(cy1, by + bh + pad) - cy0
        keep[ky0:ky1, kx0:kx1] = 255
        mask[cy0:cy1, cx0:cx1] |= (m | page_mask[cy0:cy1, cx0:cx1]) & keep

        # plain-background test: band just outside and just inside the block
        # edge, ignoring detected strokes.  Uniform -> fill every non-bg pixel.
        ox0, oy0 = max(0, bx - pad - 8), max(0, by - pad - 8)
        ox1, oy1 = min(W, bx + bw + pad + 8), min(H, by + bh + pad + 8)
        band = np.zeros((oy1 - oy0, ox1 - ox0), np.uint8)
        band[:] = 255
        ix0, iy0 = bx + 4 - ox0, by + 4 - oy0
        ix1, iy1 = bx + bw - 4 - ox0, by + bh - 4 - oy0
        if ix1 > ix0 and iy1 > iy0:
            band[iy0:iy1, ix0:ix1] = 0
        band &= ~mask[oy0:oy1, ox0:ox1]
        bg = img[oy0:oy1, ox0:ox1][band > 0].astype(np.float32)
        is_plain = False
        if bg.size:
            med = np.median(bg, axis=0)
            if dt.is_plain(bg, med):
                plain.append(((bx, by, bw, bh), med))
                is_plain = True
        # Fallback for display text the detector cannot segment (big white
        # titles / coloured typography over gradients and paintings): if the
        # model found almost no strokes inside a NON-plain box, cluster the
        # box's colours and take the cluster(s) far from the border colour
        # as the text.  Everything picked here goes through the normal
        # ring test -> LaMa, so art nearby is reconstructed, not flat-filled.
        inbox = mask[by:by + bh, bx:bx + bw]
        cov = inbox.mean() / 255
        if os.environ.get("CLEAN_DEBUG"):
            print(f"  block {bx,by,bw,bh}: cov={cov:.3f} plain={is_plain}")
        if not is_plain and bg.size and cov < LOW_COVERAGE:
            # the model found almost no strokes inside this non-plain box:
            # the detector missed genuine display text.  The reference
            # colour is the raw outer ring of the box (the mask may be
            # garbage here, so it must not be used to pick the samples).
            outer = np.ones((oy1 - oy0, ox1 - ox0), bool)
            outer[by - oy0:by + bh - oy0, bx - ox0:bx + bw - ox0] = False
            ref = np.median(img[oy0:oy1, ox0:ox1][outer].astype(np.float32), axis=0) if outer.any() else med
            extra = colour_cluster_text(img[by:by + bh, bx:bx + bw], ref)
            if os.environ.get("CLEAN_DEBUG"):
                print(f"  block {bx,by,bw,bh}: cov={cov:.3f} plain={is_plain} ref={ref.round()} cluster={'none' if extra is None else int((extra > 0).sum())}")
            if extra is not None:
                mask[by:by + bh, bx:bx + bw] |= extra

    # Refine the (blobby, upsampled) segmentation to INK pixels only: a
    # glyph's strokes plus a few px of anti-aliasing, not the whole glyph
    # bounding area.  Over art this keeps the paper between the strokes as
    # anchors so the inpainter continues hatching/screentone instead of
    # filling one big hole with flat white.  On plain backgrounds it changes
    # nothing visible (the blob was all background there anyway).
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    k_ink = np.ones((2 * INK_GROW + 1,) * 2, np.uint8)
    for bx, by, bw, bh in blocks:
        x0, y0 = max(0, bx - pad - 8), max(0, by - pad - 8)
        x1, y1 = min(W, bx + bw + pad + 8), min(H, by + bh + pad + 8)
        g = gray[y0:y1, x0:x1]
        m = mask[y0:y1, x0:x1]
        if not m.any():
            continue
        bgl = np.median(g[m == 0]) if (m == 0).any() else np.median(g)
        # either polarity: dark text on light paper, or light/coloured text
        # (white titles) on a dark or mid-tone background
        ink = np.abs(g.astype(np.int16) - int(bgl)) > INK_TOL
        ink = cv2.dilate(ink.astype(np.uint8) * 255, k_ink)
        mask[y0:y1, x0:x1] = m & ink

    for (bx, by, bw, bh), med in plain:
        x0, y0 = max(0, bx - pad), max(0, by - pad)
        x1, y1 = min(W, bx + bw + pad), min(H, by + bh + pad)
        reg = img[y0:y1, x0:x1].astype(np.float32)
        far = (np.linalg.norm(reg - med, axis=2) > dt.COLOR_TOL).astype(np.uint8) * 255
        far = cv2.dilate(far, np.ones((3, 3), np.uint8))
        # Only take non-background pixels that are plausibly text.  A
        # component fully inside the box is text the model under-segmented
        # (stylised titles, big handwriting) -> erase it whole.  A component
        # that runs THROUGH the box edge is art (hatching, speed lines, a
        # balloon outline, a panel border, or a glyph welded to such art) ->
        # erase only its pixels within STROKE_NEAR px of a detected stroke,
        # never the rest of the drawing.
        fn, fl, fst, _ = cv2.connectedComponentsWithStats(far, connectivity=8)
        near = cv2.dilate(mask[y0:y1, x0:x1], np.ones((2 * STROKE_NEAR + 1,) * 2, np.uint8))
        keep = np.zeros_like(far)
        fh, fw = far.shape
        for j in range(1, fn):
            jx, jy, jw, jh, ja = fst[j]
            comp = (fl[jy:jy + jh, jx:jx + jw] == j).astype(np.uint8) * 255
            touches = jx == 0 or jy == 0 or jx + jw >= fw or jy + jh >= fh
            if touches:
                comp &= near[jy:jy + jh, jx:jx + jw]
            keep[jy:jy + jh, jx:jx + jw] |= comp
        mask[y0:y1, x0:x1] |= keep

    if os.environ.get("CLEAN_DEBUG"):
        cv2.imwrite(det_path.replace("_detect.json", "_mask.png"), mask)

    # 2. erase components (same fill/inpaint rule as detect_text.py)
    if os.environ.get("CLEAN_DEBUG"):
        cv2.imwrite(os.path.join(os.path.dirname(det_path), "debug_mask.png"), mask)
        print(f"  mask px total {int((mask > 0).sum())}")
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    cleaned = img.copy()
    inpaint_mask = np.zeros((H, W), np.uint8)
    fill_tint = np.zeros((H, W), np.uint8)
    k_in = np.ones((2 * dt.RING_IN + 1,) * 2, np.uint8)
    k_out = np.ones((2 * dt.RING_OUT + 1,) * 2, np.uint8)
    nfill = ninp = 0
    del page_mask
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < dt.MIN_AREA:
            continue
        p = dt.RING_OUT + 2
        x0, y0 = max(0, x - p), max(0, y - p)
        x1, y1 = min(W, x + bw + p), min(H, y + bh + p)
        # everything below works on the component's bbox slice only: full-page
        # `labels == i` per component is O(components x megapixels) and OOMs
        # on 71 MP scans
        comp = (labels[y0:y1, x0:x1] == i).astype(np.uint8)
        ring = cv2.dilate(comp, k_out) & ~cv2.dilate(comp, k_in) & ~(mask[y0:y1, x0:x1] > 0)
        bg = img[y0:y1, x0:x1][ring > 0].astype(np.float32)
        median = np.median(bg, axis=0) if bg.size else None
        uniform = dt.is_plain(bg, median)
        sel = comp > 0
        if uniform:
            cleaned[y0:y1, x0:x1][sel] = median.round().clip(0, 255).astype(np.uint8)
            fill_tint[y0:y1, x0:x1][sel] = 255
            nfill += 1
        else:
            inpaint_mask[y0:y1, x0:x1][sel] = 255
            ninp += 1
    del labels
    left = np.zeros_like(inpaint_mask)
    if inpaint_mask.any():
        # busy surroundings (art, screentone, gradients): LaMa inpainting when
        # <skill>/models/lama_fp32.onnx exists, cv2.inpaint otherwise.  The solid
        # fills above are never touched by this step.  With --budget the
        # LaMa pass may stop early; the unprocessed regions are saved as
        # <stem>_pending_mask.png and finished on the next run.
        cleaned = inpaint_lama.inpaint(cleaned, inpaint_mask, inplace=True, deadline=deadline, pending=left)

    cv2.imwrite(cleaned_path, cleaned)
    if left.any():
        cv2.imwrite(pending_path, left)
    overlay = img.copy()
    for m, tint in ((fill_tint, (0, 0, 255)), (inpaint_mask, (0, 200, 0))):
        if m.any():
            overlay[m > 0] = 0.25 * overlay[m > 0] + 0.75 * np.array(tint)
    cv2.imwrite(os.path.join(out_dir, f"{stem}_clean_overlay.jpg"), overlay,
                [cv2.IMWRITE_JPEG_QUALITY, 85])
    d["cleaned"] = cleaned_path
    d["inpaint_method"] = inpaint_lama.method_name()
    with open(det_path, "w") as f:
        json.dump(d, f, indent=1)
    print(f"{stem}: {len(blocks)} blocks, {nfill} fill + {ninp} inpaint ({inpaint_lama.method_name()}) components -> {cleaned_path}"
          + (f"  PARTIAL: {int((left > 0).sum())} mask px pending -> rerun" if left.any() else ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("detect_json", nargs="+")
    ap.add_argument("--pad", type=int, default=6, help="px around each block still erased")
    ap.add_argument("--budget", type=float, default=None,
                    help="seconds; stop the AI inpainting early once this much wall time has passed since "
                         "start (the rest is saved as <stem>_pending_mask.png and finished by a rerun). "
                         "Lets a 71 MP page be cleaned across several time-capped shell calls.")
    a = ap.parse_args()
    so = ort.SessionOptions()
    so.add_session_config_entry("session.set_denormal_as_zero", "1")
    sess = ort.InferenceSession(dt.MODEL, so, providers=["CPUExecutionProvider"])
    deadline = time.time() + a.budget if a.budget else None
    for p in a.detect_json:
        if deadline and time.time() > deadline:
            print(f"BUDGET: not started {p}")
            continue
        try:
            clean_page(sess, p, a.pad, deadline)
        except Exception as e:
            print(f"SKIP {p}: {e}")


if __name__ == "__main__":
    main()
