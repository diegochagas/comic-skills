# GIMP 3 batch script: build translation-ready / letter-ready XCF files with
# NATIVE GIMP text layers - the GIMP-format twin of build_translated_psd.mjs.
# Runs INSIDE GIMP's python-fu-eval interpreter; normally launched by
# build_translated_xcf.py (which writes the job JSON and reads the log):
#
#   flatpak run --env=XCF_JOB=/abs/job.json org.gimp.GIMP -id \
#     --batch-interpreter=python-fu-eval \
#     -b "exec(open('/abs/gimp_xcf_job.py').read())" --quit
#
# Job JSON: {"pages": [ {
#   "source":      "/abs/page.png",           # bottom layer "Original"
#   "blocks":      "/abs/page_blocks.json",   # {"text_blocks": [[x,y,w,h]...], "texts": [...], "styles": [...]}
#   "out":         "/abs/page.xcf",
#   "copy_image":  "/abs/page_cleaned.png",   # optional: pixels of layer "Copy" (else duplicate of Original)
#   "with_copy":   true,                      # false = no Copy layer
#   "placeholder": null | "Lorem ipsum ...",  # fill blocks without text with this
#   "font":        "CCWildWords-Regular",
#   "preview":     "/abs/preview.jpg"         # optional: flattened JPG rendered by GIMP
# }, ...]}
#
# Per page: layer "Original", layer "Copy", one GIMP text layer "Text N" per
# block, in FIXED BOX mode (paragraph box the size of the block; GIMP wraps
# the text inside it), with the block's font / size / colour (auto black or
# white from the block luminance) / justification. rotate 90 / -90 / 180 is
# applied with a lossless 90-degree transform: GIMP keeps the layer a text
# layer, but it flags it "modified" - editing the text later re-renders it
# unrotated, so rotate it again after editing. NOTE: never start GIMP with
# -f (no fonts) - without fonts every TextLayer.new returns NULL. Fonts the
# flatpak can't see (CCWildWords installed only for Photoshop) fall back to
# the context font; the log says so. After saving, the XCF is
# reloaded and checked (layer names, text count, every text present) and the
# log gets OK/FAIL per page and DONE at the end.

import json
import os
import traceback

import gi
gi.require_version('Gimp', '3.0')
gi.require_version('Gegl', '0.4')
from gi.repository import Gimp, Gegl, Gio

JOB_PATH = os.environ["XCF_JOB"]
LOG_PATH = JOB_PATH + ".log"
_log = open(LOG_PATH, "w")

MIN_FONT = 8


def log(msg):
    _log.write(msg + "\n")
    _log.flush()


def fit_font_size(text, w, h):
    """Largest font size (px) at which `text` word-wraps into a w x h box -
    same heuristic as build_translated_psd.mjs (0.55 em advance, 1.15 line)."""
    words = [t for t in (text or "").split() if t]
    if not words:
        return max(MIN_FONT, min(72, round(h * 0.6)))
    paras = (text or "").split("\n")
    size = int(min(h * 0.85, w))
    while size >= MIN_FONT:
        adv, line_h = size * 0.55, size * 1.15
        lines = 0
        for p in paras:
            cur = 0
            lines += 1
            for wd in [t for t in p.split() if t]:
                ww = (len(wd) + 1) * adv
                if len(wd) * adv > w:
                    lines += -(-int(len(wd) * adv) // int(w)) - 1 if w >= 1 else 0
                    cur = 0
                    continue
                if cur + ww > w and cur > 0:
                    lines += 1
                    cur = ww
                else:
                    cur += ww
        if lines * line_h <= h:
            break
        size -= 1
    return max(MIN_FONT, size)


def placeholder_size(h):
    return max(10, min(32, round(h / 7)))


def hex_color(spec):
    return Gegl.Color.new(spec)


def auto_color(image, drawable, x, y, w, h):
    """Black text on light blocks, white on dark ones (mean value of the block)."""
    image.select_rectangle(Gimp.ChannelOps.REPLACE, x, y, w, h)
    try:
        mean = drawable.histogram(Gimp.HistogramChannel.VALUE, 0.0, 1.0).mean
    finally:
        Gimp.Selection.none(image)
    return hex_color("#ffffff" if mean < 110 / 255 else "#000000")


def get_font(name):
    try:
        f = Gimp.Font.get_by_name(name)
        if f is not None:
            return f, True
    except Exception:
        pass
    return Gimp.context_get_font(), False


JUSTIFY = {"left": Gimp.TextJustification.LEFT, "right": Gimp.TextJustification.RIGHT,
           "center": Gimp.TextJustification.CENTER, "fill": Gimp.TextJustification.FILL}
ROTATE = {90: Gimp.RotationType.DEGREES90, -90: Gimp.RotationType.DEGREES270,
          180: Gimp.RotationType.DEGREES180}


def add_text_layer(image, original, i, box, text, style, font_name, placeholder):
    x, y, w, h = [int(round(v)) for v in box]
    is_placeholder = placeholder is not None and not (text or "").strip()
    if is_placeholder:
        text = placeholder
    rot = int(style.get("rotate") or 0)
    rot = rot if rot in (90, -90, 180) else 0
    bw, bh = (h, w) if abs(rot) == 90 else (w, h)       # text flows along the box's long side
    if style.get("size"):
        size = float(style["size"])
    elif is_placeholder:
        size = placeholder_size(bh)
    else:
        size = fit_font_size(text, bw, bh)
    font, found = get_font(style.get("font") or font_name)
    layer = Gimp.TextLayer.new(image, text or "", font, size, Gimp.Unit.pixel())
    image.insert_layer(layer, None, 0)
    layer.set_name(f"Text {i + 1}")
    layer.resize(bw, bh)                                    # fixed paragraph box
    layer.set_justification(JUSTIFY.get(style.get("align") or "center", Gimp.TextJustification.CENTER))
    layer.set_color(hex_color(style["color"]) if style.get("color") else auto_color(image, original, x, y, w, h))
    layer.set_antialias(True)
    layer.set_language("pt-br")                             # Photoshop: Portuguese: Brazilian
    # centre the unrotated bw x bh box on the block, then rotate about that centre
    ox, oy = x + (w - bw) / 2, y + (h - bh) / 2
    layer.set_offsets(int(round(ox)), int(round(oy)))
    if rot:
        layer.transform_rotate_simple(ROTATE[rot], True, 0, 0)
    return found


def verify(page, n_blocks, texts):
    image = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE, Gio.File.new_for_path(page["out"]))
    try:
        layers = image.get_layers()
        names = [l.get_name() for l in layers]
        problems = []
        if "Original" not in names:
            problems.append("no Original layer")
        if page.get("with_copy", True) and "Copy" not in names:
            problems.append("no Copy layer")
        text_layers = [l for l in layers if isinstance(l, Gimp.TextLayer)]
        if len(text_layers) != n_blocks:
            problems.append(f"{len(text_layers)} text layers, expected {n_blocks}")
        for l in text_layers:
            if not (l.get_text() or "").strip():
                problems.append(f"{l.get_name()} is empty")
        want = [t for t in texts if (t or "").strip()]
        have = {(l.get_text() or "") for l in text_layers}
        missing = [t for t in want if t not in have]
        if missing:
            problems.append(f"{len(missing)} translation(s) not found in the file")
        return problems
    finally:
        image.delete()


def process_page(page):
    src, out = page["source"], page["out"]
    blk = json.load(open(page["blocks"]))
    boxes = blk.get("text_blocks", [])
    texts = blk.get("texts", [])
    styles = blk.get("styles", [])
    placeholder = page.get("placeholder")
    if placeholder is not None:
        texts = [(texts[i] if i < len(texts) and (texts[i] or "").strip() else placeholder) for i in range(len(boxes))]

    image = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE, Gio.File.new_for_path(src))
    try:
        original = image.get_layers()[0]
        original.set_name("Original")
        if page.get("with_copy", True):
            if page.get("copy_image"):
                copy = Gimp.file_load_layer(Gimp.RunMode.NONINTERACTIVE, image,
                                            Gio.File.new_for_path(page["copy_image"]))
                if (copy.get_width(), copy.get_height()) != (image.get_width(), image.get_height()):
                    raise ValueError(f"copy_image {copy.get_width()}x{copy.get_height()} != source "
                                     f"{image.get_width()}x{image.get_height()}")
            else:
                copy = original.copy()
            image.insert_layer(copy, None, 0)
            copy.set_name("Copy")
        missing_font = 0
        for i, box in enumerate(boxes):
            st = styles[i] if i < len(styles) and styles[i] else {}
            found = add_text_layer(image, original, i, box, texts[i] if i < len(texts) else "",
                                   st, page.get("font") or "CCWildWords-Regular", placeholder)
            missing_font += 0 if found else 1
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, image, Gio.File.new_for_path(out), None)
        if page.get("preview"):
            os.makedirs(os.path.dirname(os.path.abspath(page["preview"])), exist_ok=True)
            prev = image.duplicate()
            for l in prev.get_layers():
                if l.get_name() == "Original" and page.get("with_copy", True):
                    l.set_visible(False)
            prev.flatten()
            Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, prev, Gio.File.new_for_path(page["preview"]), None)
            prev.delete()
    finally:
        image.delete()

    problems = verify(page, len(boxes), texts)
    if problems:
        log(f"FAIL {src} -> {out}: " + "; ".join(problems))
    else:
        note = f" (font not installed in GIMP, {missing_font} box(es) use the context font)" if missing_font else ""
        log(f"OK {src} -> {out}: 2 raster + {len(boxes)} text layer(s){note}")


def main():
    job = json.load(open(JOB_PATH))
    for page in job["pages"]:
        try:
            process_page(page)
        except Exception:
            log(f"FAIL {page.get('source')} -> {page.get('out')}\n" + traceback.format_exc())


try:
    main()
    log("DONE")
except Exception:
    log("FATAL\n" + traceback.format_exc())
_log.close()
