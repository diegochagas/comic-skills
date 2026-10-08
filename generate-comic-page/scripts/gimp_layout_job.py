# GIMP 3 batch script: build a cover / editorial XCF from a layout - the art
# (or a flat paper colour) at the bottom and one NATIVE, EDITABLE GIMP text
# layer per text item on top, plus optional pasted images (logo, fan art).
# Runs INSIDE GIMP's python-fu-eval interpreter; launched by build_xcf.py,
# which resolves every path/box to absolute pixels and reads the log:
#
#   flatpak run --env=LAYOUT_JOB=/abs/job.json io.github.diegochagas.GIMPhoto -id \
#     --batch-interpreter=python-fu-eval \
#     -b "exec(open('/abs/gimp_layout_job.py').read())" --quit
#
# Job JSON (written by build_xcf.py):
#   {"list_fonts": "filter"}                       -> log the installed fonts
#   {"uniform_size": bool, "max_size": px | null,   (see plan_sizes)
#    "art": "/abs/art.png" | null,
#    "background": {"color": "#efe6cf", "size": [w, h]} | null,
#    "out": "/abs/page.xcf", "preview": "/abs/page_preview.jpg" | null,
#    "layers": [ {"name", "box": [x, y, w, h] px,
#                 "text", "font", "size" | null, "color", "align", "valign",
#                 "line_spacing", "letter_spacing", "outline": {"color", "width"}}
#              | {"name", "box", "image": "/abs/file.png"} ]}
#
# NOTE: never start GIMP with -f (no fonts) - every TextLayer.new returns NULL.
# Text layers are left untransformed (no rotation) so they stay editable.

import json
import math
import os
import traceback

import gi
gi.require_version('Gimp', '3.0')
gi.require_version('Gegl', '0.4')
from gi.repository import Gimp, Gegl, Gio

JOB_PATH = os.environ["LAYOUT_JOB"]
_log = open(JOB_PATH + ".log", "w")

MIN_FONT = 8
JUSTIFY = {"left": Gimp.TextJustification.LEFT, "right": Gimp.TextJustification.RIGHT,
           "center": Gimp.TextJustification.CENTER, "fill": Gimp.TextJustification.FILL}


def log(msg):
    _log.write(msg + "\n")
    _log.flush()


def get_font(name):
    try:
        f = Gimp.Font.get_by_name(name)
        if f is not None:
            return f, True
    except Exception:
        pass
    return Gimp.context_get_font(), False


def extents(text, size, font):
    ok, w, h, _asc, _desc = Gimp.text_get_extents_font(text or " ", size, font)
    return w, h


def measure(text, size, font, box_w, line_spacing):
    """(lines, needed_height, fits_width) of `text` word-wrapped into box_w at
    `size`: every paragraph is measured on one line and split by width."""
    line_h = extents("Ág", size, font)[1] + line_spacing
    lines, fits = 0, True
    for para in (text or "").split("\n"):
        if not para.strip():
            lines += 1
            continue
        w = extents(para, size, font)[0]
        # greedy wrapping wastes the tail of each line: keep ~8% slack
        lines += max(1, math.ceil(w / (box_w * 0.92)))
        if max(extents(wd, size, font)[0] for wd in para.split()) > box_w:
            fits = False
    return lines, lines * line_h, fits


def fit_size(text, font, box_w, box_h, line_spacing):
    lo, hi, best = MIN_FONT, max(MIN_FONT, int(box_h)), MIN_FONT
    while lo <= hi:
        mid = (lo + hi) // 2
        _n, need_h, fits_w = measure(text, mid, font, box_w, line_spacing)
        if fits_w and need_h <= box_h:
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
    return best


def add_text(image, item):
    x, y, w, h = item["box"]
    font, found = get_font(item["font"])
    spacing = float(item.get("line_spacing") or 0)
    size = float(item["size"])                              # resolved by plan_sizes()
    layer = Gimp.TextLayer.new(image, item["text"], font, size, Gimp.Unit.pixel())
    image.insert_layer(layer, None, 0)
    layer.set_name(item["name"])
    layer.set_justification(JUSTIFY.get(item.get("align") or "center", Gimp.TextJustification.CENTER))
    layer.set_color(Gegl.Color.new(item.get("color") or "#000000"))
    layer.set_antialias(True)
    layer.set_language("pt-br")                             # Photoshop: Portuguese: Brazilian
    if spacing:
        layer.set_line_spacing(spacing)
    if item.get("letter_spacing"):
        layer.set_letter_spacing(float(item["letter_spacing"]))
    outline = item.get("outline")
    if outline:
        layer.set_outline(Gimp.TextOutline.STROKE_FILL)
        layer.set_outline_color(Gegl.Color.new(outline.get("color") or "#000000"))
        layer.set_outline_width(float(outline.get("width") or 4), Gimp.Unit.pixel())
        layer.set_outline_direction(Gimp.TextOutlineDirection.OUTER)
    # fixed paragraph box as wide as the layout box; its height hugs the text so
    # valign can place it (GIMP always starts the text at the top of the box)
    _n, need_h, _f = measure(item["text"], size, font, w, spacing)
    box_h = int(min(h, math.ceil(need_h * 1.08) + 4)) if item.get("valign", "middle") != "top" else h
    layer.resize(w, max(box_h, 1))
    off = {"top": 0, "middle": (h - box_h) // 2, "bottom": h - box_h}[item.get("valign", "middle")]
    layer.set_offsets(x, y + off)
    overflow = need_h > h
    return size, found, overflow


def plan_sizes(job):
    """Give every text item its final "size". Auto sizes are the largest that
    fits the box, capped by max_size; with uniform_size they all drop to one
    common size (25th percentile of the fits) so the lettering looks even."""
    auto = []
    for item in job["layers"]:
        if "text" in item and not item.get("size"):
            font, _found = get_font(item["font"])
            item["size"] = fit_size(item["text"], font, item["box"][2], item["box"][3],
                                    float(item.get("line_spacing") or 0))
            if job.get("max_size"):
                item["size"] = min(item["size"], float(job["max_size"]))
            auto.append(item)
    if job.get("uniform_size") and auto:
        fits = sorted(i["size"] for i in auto)
        common = fits[len(fits) // 4]
        for item in auto:
            item["size"] = min(item["size"], common)


def add_image(image, item):
    x, y, w, h = item["box"]
    layer = Gimp.file_load_layer(Gimp.RunMode.NONINTERACTIVE, image, Gio.File.new_for_path(item["image"]))
    image.insert_layer(layer, None, 0)
    layer.set_name(item["name"])
    scale = min(w / layer.get_width(), h / layer.get_height())
    nw, nh = max(1, round(layer.get_width() * scale)), max(1, round(layer.get_height() * scale))
    layer.scale(nw, nh, False)
    layer.set_offsets(x + (w - nw) // 2, y + (h - nh) // 2)


def verify(job):
    image = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE, Gio.File.new_for_path(job["out"]))
    try:
        by_name = {l.get_name(): l for l in image.get_layers()}
        problems = []
        for item in job["layers"]:
            layer = by_name.get(item["name"])
            if layer is None:
                problems.append(f"layer '{item['name']}' missing")
            elif "text" in item:
                if not isinstance(layer, Gimp.TextLayer):
                    problems.append(f"'{item['name']}' is not an editable text layer")
                elif (layer.get_text() or "") != item["text"]:
                    problems.append(f"'{item['name']}' text differs from the layout")
        return problems
    finally:
        image.delete()


def build(job):
    if job.get("art"):
        image = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE, Gio.File.new_for_path(job["art"]))
        image.get_layers()[0].set_name("Art")
    else:
        bw, bh = job["background"]["size"]
        image = Gimp.Image.new(bw, bh, Gimp.ImageBaseType.RGB)
        paper = Gimp.Layer.new(image, "Paper", bw, bh, Gimp.ImageType.RGB_IMAGE, 100.0, Gimp.LayerMode.NORMAL)
        image.insert_layer(paper, None, 0)
        Gimp.context_set_foreground(Gegl.Color.new(job["background"]["color"]))
        paper.fill(Gimp.FillType.FOREGROUND)
    try:
        notes = []
        plan_sizes(job)
        for item in job["layers"]:
            if "image" in item:
                add_image(image, item)
                continue
            size, found, overflow = add_text(image, item)
            notes.append(f"  text '{item['name']}': {size:g}px"
                         + ("" if found else f"  [font '{item['font']}' NOT installed -> context font]")
                         + ("  [may OVERFLOW its box: shrink size or grow the box]" if overflow else ""))
        os.makedirs(os.path.dirname(job["out"]), exist_ok=True)
        if job.get("preview"):
            os.makedirs(os.path.dirname(job["preview"]), exist_ok=True)
        Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, image, Gio.File.new_for_path(job["out"]), None)
        if job.get("preview"):
            prev = image.duplicate()
            prev.flatten()
            Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, prev, Gio.File.new_for_path(job["preview"]), None)
            prev.delete()
    finally:
        image.delete()
    problems = verify(job)
    log(("FAIL " + "; ".join(problems)) if problems else f"OK {job['out']}")
    for n in notes:
        log(n)


try:
    job = json.load(open(JOB_PATH))
    if "list_fonts" in job:
        needle = (job["list_fonts"] or "").lower()
        for name in sorted({f.get_name() for f in Gimp.fonts_get_list("")}):
            if needle in name.lower():
                log("FONT " + name)
    else:
        build(job)
    log("DONE")
except Exception:
    log("FATAL\n" + traceback.format_exc())
_log.close()
