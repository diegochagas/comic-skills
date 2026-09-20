# GIMP 3 batch script: the GIMP half of psd-xcf-convert, both directions.
# Runs INSIDE GIMP's python-fu-eval interpreter; launched by convert.py, which
# writes the job JSON and reads the log:
#
#   flatpak run --env=CONVERT_JOB=/abs/job.json org.gimp.GIMP -id \
#     --batch-interpreter=python-fu-eval \
#     -b "exec(open('/abs/gimp_convert_job.py').read())" --quit
#
# NEVER start GIMP with -f here: without fonts every TextLayer.new returns NULL.
#
# Job JSON: {"tasks": [
#   {"mode": "psd2xcf", "src": "/abs/in.psd", "info": "/abs/in_info.json",   # from psd_text_info.mjs,
#    "out": "/abs/out.xcf", "preview": null | "/abs/out.jpg",                 # + "fonts" added by convert.py
#    "keep_raster": false},
#   {"mode": "xcf2psd", "src": "/abs/in.xcf", "out_psd": "/abs/tmp.psd",     # GIMP's own PSD export
#    "out_info": "/abs/tmp_info.json", "preview": null | "/abs/out.jpg"}     # read by write_psd_text.mjs
# ]}
#
# psd2xcf: GIMP loads the PSD itself (pixels, groups, masks, modes, opacity),
#   then every rasterized Type layer listed in the info JSON is REPLACED, at the
#   same place in the stack, by a native GIMP text layer (font, size, colour,
#   justification, tracking, leading, fixed box or dynamic, rotation, mixed
#   styles as markup), and every Layer Style stroke / drop shadow becomes a
#   "Text Styling" filter (gegl:styles, non-destructive, still editable).
# xcf2psd: every text layer and every gegl:styles / gegl:dropshadow filter is
#   described in out_info, those filters are hidden (Photoshop will draw them
#   live as Layer Styles) and GIMP exports the PSD; write_psd_text.mjs then
#   turns the rasterized text layers of that PSD back into Type layers.
#
# A layer parasite "psd-xcf-convert" carries what GIMP cannot store (rotation
# angle, the Photoshop name of a substituted font), so a file that goes
# PSD -> XCF -> PSD comes back with its own fonts and angles.
#
# Log: one "OK ..." / "FAIL ..." line per task, "NOTE <src>: ..." lines for
# anything approximated, "DONE" at the end.

import json
import math
import os
import re
import traceback
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

import gi
gi.require_version('Gimp', '3.0')
gi.require_version('Gegl', '0.4')
gi.require_version('Babl', '0.1')
from gi.repository import Gimp, Gegl, Gio, Babl

JOB_PATH = os.environ["CONVERT_JOB"]
LOG_PATH = JOB_PATH + ".log"
_log = open(LOG_PATH, "w")
PARASITE = "psd-xcf-convert"
STYLE_OPS = ("gegl:styles", "gegl:dropshadow")


def log(msg):
    _log.write(msg + "\n")
    _log.flush()


def color_hex(c):
    b = c.get_bytes(Babl.format("R'G'B'A u8")).get_data()
    return "#%02x%02x%02x" % (b[0], b[1], b[2])


def load(path):
    image = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE, Gio.File.new_for_path(path))
    # an XCF keeps the selection it was saved with, and with a selection active
    # every transform below would cut a floating layer instead of turning the layer
    Gimp.Selection.none(image)
    return image


def save(image, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, image, Gio.File.new_for_path(path), None)


def save_preview(image, path):
    prev = image.duplicate()
    try:
        prev.flatten()
        save(prev, path)
    finally:
        prev.delete()


def walk(layers, prefix=()):
    """(path, layer) for every layer, top first, groups before their children."""
    for i, l in enumerate(layers):
        path = prefix + (i,)
        yield path, l
        if l.is_group():
            yield from walk(l.get_children(), path)


def parasite_text(layer, name):
    """Parasite payload as str ('' if absent). get_data() hands the bytes back
    SIGNED, so any accented letter would break a plain bytes()."""
    try:
        if name not in layer.get_parasite_list():
            return ""
        return bytes(b & 0xFF for b in layer.get_parasite(name).get_data()).decode("utf8", "replace")
    except Exception:
        return ""


def get_parasite_json(layer):
    try:
        return json.loads(parasite_text(layer, PARASITE) or "{}")
    except ValueError:
        return {}


def set_parasite_json(layer, data):
    layer.attach_parasite(Gimp.Parasite.new(PARASITE, 1, list(json.dumps(data).encode("utf8"))))


def line_height(font, size):
    ok, _w, h1, asc, _d = Gimp.text_get_extents_font("Hg", size, font)
    ok, _w, h2, _a, _d = Gimp.text_get_extents_font("Hg\nHg", size, font)
    return h2 - h1, asc


JUSTIFY = {"left": Gimp.TextJustification.LEFT, "right": Gimp.TextJustification.RIGHT,
           "center": Gimp.TextJustification.CENTER}
JUSTIFY_BACK = {int(Gimp.TextJustification.LEFT): "left", int(Gimp.TextJustification.RIGHT): "right",
                int(Gimp.TextJustification.CENTER): "center", int(Gimp.TextJustification.FILL): "justify-left"}


# --------------------------------------------------------------- PSD -> XCF

def find_font(task, psname, notes):
    """GIMP font for a Photoshop PostScript name, through the candidates
    convert.py resolved with fontconfig. Returns (font, gimp_name)."""
    entry = (task.get("fonts") or {}).get(psname) or {}
    for cand in entry.get("gimp", []) + [psname]:
        try:
            f = Gimp.Font.get_by_name(cand)
        except Exception:
            f = None
        if f is not None:
            if entry.get("substitute"):
                notes.add(f'font "{psname}" is not installed: using "{cand}"')
            return f, cand
    f = Gimp.context_get_font()
    notes.add(f'font "{psname}" is not installed: using GIMP\'s current font "{f.get_name()}"')
    return f, f.get_name()


def run_text(run):
    return run["text"].upper() if run.get("caps") else run["text"]


def build_markup(runs, base, fonts, yres):
    parts = []
    for r in runs:
        s = escape(run_text(r))
        attrs = []
        if r["font"] != base["font"]:
            attrs.append('font="%s"' % escape(fonts[r["font"]], {'"': "&quot;"}))
        if abs(r["size"] - base["size"]) > 0.01:
            # pango span sizes are 1024ths of a POINT at the image resolution
            attrs.append('size="%d"' % round(r["size"] * 72.0 / yres * 1024))
        if r["color"] != base["color"]:
            attrs.append('foreground="%s"' % r["color"])
        if r.get("tracking") != base.get("tracking"):
            attrs.append('letter_spacing="%d"' % round(r.get("tracking", 0) / 1000.0 * r["size"] * 72.0 / yres * 1024))
        if attrs:
            s = "<span %s>%s</span>" % (" ".join(attrs), s)
        for flag, tag in (("bold", "b"), ("italic", "i"), ("underline", "u"), ("strike", "s")):
            if r.get(flag):
                s = "<%s>%s</%s>" % (tag, s, tag)
        parts.append(s)
    return "<markup>%s</markup>" % "".join(parts)


def ink_bounds(image, layer):
    """Bounds of the layer's opaque pixels, relative to the layer. Selections
    are clipped to the canvas, so the layer is parked at the origin first."""
    layer.set_offsets(0, 0)
    image.select_item(Gimp.ChannelOps.REPLACE, layer)
    try:
        r = Gimp.Selection.bounds(image)
        return (r.x1, r.y1, r.x2, r.y2) if r.non_empty else None
    finally:
        Gimp.Selection.none(image)


def rotate_layer(layer, angle):
    """Rotate about the layer's own centre; multiples of 90 are lossless.
    GIMP keeps the layer a text layer either way, flagged as modified."""
    a = round(angle) % 360
    if abs(angle - round(angle)) < 0.5 and a % 90 == 0:
        if a == 0:
            return layer
        kind = {90: Gimp.RotationType.DEGREES90, 180: Gimp.RotationType.DEGREES180,
                270: Gimp.RotationType.DEGREES270}[a]
        return layer.transform_rotate_simple(kind, True, 0, 0)
    return layer.transform_rotate(math.radians(angle), True, 0, 0)


def make_text_layer(image, task, entry, raster, notes):
    t = entry["text"]
    runs = [r for r in t["runs"] if r["text"]] or t["runs"][:1]
    base = max(runs, key=lambda r: len(r["text"]))
    yres = image.get_resolution().yresolution or 72.0
    fonts, gimp_fonts = {}, {}
    for r in runs:
        if r["font"] not in fonts:
            f, name = find_font(task, r["font"], notes)
            fonts[r["font"]], gimp_fonts[r["font"]] = name, f
    font = gimp_fonts[base["font"]]
    size = max(1.0, float(base["size"]))
    text = "".join(run_text(r) for r in runs)
    if t.get("orientation") == "vertical":
        notes.add(f'"{entry["name"]}": vertical text became horizontal (set the direction in GIMP\'s text tool)')

    layer = Gimp.TextLayer.new(image, text, font, size, Gimp.Unit.pixel())
    if layer is None:
        raise RuntimeError("TextLayer.new returned NULL (GIMP started without fonts?)")
    image.insert_layer(layer, raster.get_parent(), image.get_item_position(raster))
    layer.set_color(Gegl.Color.new(base["color"]))
    layer.set_antialias(True)
    just = t.get("justification", "left")
    layer.set_justification(Gimp.TextJustification.FILL if just.startswith("justify") else JUSTIFY.get(just, Gimp.TextJustification.LEFT))
    layer.set_letter_spacing(base.get("tracking", 0) / 1000.0 * size)
    natural, _asc = line_height(font, size)
    leading = base["leading"] if base.get("leading") else size * float(t.get("autoLeading") or 1.2)
    if t["shape"] == "box" or "\n" in text:
        layer.set_line_spacing(round(leading - natural, 2))
    if t.get("indent"):
        layer.set_indent(t["indent"])

    def style_key(r):
        return (r["font"], round(r["size"], 2), r["color"], r.get("bold"), r.get("italic"),
                r.get("underline"), r.get("strike"), r.get("tracking"))
    if len({style_key(r) for r in runs}) > 1 or any(base.get(k) for k in ("bold", "italic", "underline", "strike")):
        layer.set_markup(build_markup(runs, base, fonts, yres))

    angle = float(t.get("angle") or 0)
    if t["shape"] == "box":
        w, h = max(1, round(t["box"]["w"])), max(1, round(t["box"]["h"]))
        layer.resize(w, h)                                   # = fixed box: GIMP wraps the text inside it
        layer.set_offsets(round(t["box"]["cx"] - w / 2), round(t["box"]["cy"] - h / 2))
        layer = rotate_layer(layer, angle)
    else:
        layer = rotate_layer(layer, angle)
        # point text: GIMP and Photoshop measure lines differently, so put the
        # INK of the new layer where the ink of Photoshop's rendering is
        l, tp, r, b = entry["bbox"]
        ink = ink_bounds(image, layer)
        if ink and r > l and b > tp:
            layer.set_offsets(round((l + r) / 2 - (ink[0] + ink[2]) / 2),
                              round((tp + b) / 2 - (ink[1] + ink[3]) / 2))
        else:
            layer.set_offsets(l, tp)

    name = raster.get_name()
    raster.set_name(name + " (Photoshop render)")
    layer.set_name(name)
    layer.set_opacity(raster.get_opacity())
    layer.set_mode(raster.get_mode())
    layer.set_visible(raster.get_visible())
    layer.set_color_tag(raster.get_color_tag())
    if raster.get_mask() is not None:
        notes.add(f'"{entry["name"]}": the layer mask of this text layer was not carried over')
    set_parasite_json(layer, {"angle": angle, "shape": t["shape"], "justification": just,
                              "fonts": {fonts[k]: k for k in fonts}})
    if task.get("keep_raster"):
        raster.set_visible(False)
    else:
        image.remove_layer(raster)
    return layer


def add_styles_filter(layer, fx, notes, name):
    f = Gimp.DrawableFilter.new(layer, "gegl:styles", "Text Styling")
    cfg = f.get_config()
    s = fx.get("stroke")
    if s:
        grow = s["size"] if s["position"] == "outside" else s["size"] / 2.0
        if s["position"] != "outside":
            notes.add(f'"{name}": stroke position "{s["position"]}" drawn as an outside outline of {grow:g}px')
        cfg.set_property("enableoutline", True)
        cfg.set_property("outline", float(grow))
        cfg.set_property("outline-color", Gegl.Color.new(s["color"]))
        cfg.set_property("outline-opacity", float(s.get("opacity", 1)))
    sh = fx.get("shadow")
    if sh:
        cfg.set_property("shadow-opacity", float(sh["opacity"]))
        cfg.set_property("shadow-x", float(sh["x"]))
        cfg.set_property("shadow-y", float(sh["y"]))
        cfg.set_property("shadow-color", Gegl.Color.new(sh["color"]))
        cfg.set_property("shadow-radius", float(sh["blur"]) / 2.0)       # Photoshop size ~ 2 x gaussian std-dev
        cfg.set_property("shadow-grow-radius", min(100.0, float(sh.get("grow", 0))))
    fill = fx.get("fill")
    if fill:
        cfg.set_property("color-fill", Gegl.Color.new(fill["color"]))
        cfg.set_property("color-policy", "multiply" if fill["blend"] == "multiply" else "solidcolor")
    f.update()
    layer.append_filter(f)


def psd2xcf(task):
    src = task["src"]
    info = json.load(open(task["info"]))
    notes = set()
    image = load(src)
    try:
        by_path = dict(walk(image.get_layers()))
        by_name = {}
        for _p, l in by_path.items():
            by_name.setdefault(l.get_name().strip(), []).append(l)
        targets = []
        for e in info["layers"]:
            l = by_path.get(tuple(e["path"]))
            want = e["name"].strip()
            if l is None or (l.get_name().strip() != want and not l.get_name().startswith(want)):
                cands = by_name.get(want, [])
                l = cands[0] if len(cands) == 1 else None
            if l is None:
                notes.add(f'"{e["name"]}": layer not found in GIMP\'s copy of the PSD, left as loaded')
                continue
            targets.append((e, l))
        n_text = n_fx = 0
        for e, l in targets:
            try:
                if e.get("text") and not l.is_group():
                    l = make_text_layer(image, task, e, l, notes)
                    n_text += 1
                fx = e.get("effects") or {}
                for n in fx.get("notes", []):
                    notes.add(f'"{e["name"]}": {n}')
                if fx.get("stroke") or fx.get("shadow") or fx.get("fill"):
                    add_styles_filter(l, fx, notes, e["name"])
                    n_fx += 1
            except Exception:
                notes.add(f'"{e["name"]}": conversion failed, layer left as GIMP loaded it ({traceback.format_exc().splitlines()[-1]})')
        save(image, task["out"])
        if task.get("preview"):
            save_preview(image, task["preview"])
    finally:
        image.delete()

    # verify on a fresh load
    image = load(task["out"])
    try:
        layers = [l for _p, l in walk(image.get_layers())]
        have_text = sum(1 for l in layers if isinstance(l, Gimp.TextLayer))
        have_fx = sum(1 for l in layers if any(f.get_operation_name() == "gegl:styles" for f in l.get_filters()))
    finally:
        image.delete()
    for n in sorted(notes):
        log(f"NOTE {src}: {n}")
    if have_text != n_text or have_fx != n_fx:
        log(f"FAIL {src} -> {task['out']}: wrote {n_text} text / {n_fx} styled layer(s), file has {have_text} / {have_fx}")
    else:
        want_text = sum(1 for e in info["layers"] if e.get("text"))
        log(f"OK {src} -> {task['out']}: {n_text}/{want_text} editable text layer(s), {n_fx} Text Styling filter(s), {len(layers)} layer(s)")


# --------------------------------------------------------------- XCF -> PSD

def text_parasite(layer):
    raw = parasite_text(layer, "gimp-text-layer")
    out = {}
    for key in ("box-mode", "box-width", "box-height", "psname", "fullname", "family", "style"):
        m = re.search(r'\(%s\s+"?([^")]*)"?\)' % re.escape(key), raw)
        if m:
            out[key] = m.group(1)
    return out


def parse_markup(markup, base):
    """GIMP/Pango markup -> flat runs; every run carries the full style."""
    runs = []

    def emit(text, st):
        if text:
            runs.append({**st, "text": text})

    def rec(node, st):
        st = dict(st)
        tag = node.tag
        if tag == "b":
            st["bold"] = True
        elif tag == "i":
            st["italic"] = True
        elif tag == "u":
            st["underline"] = True
        elif tag == "s":
            st["strike"] = True
        elif tag == "span":
            a = node.attrib
            if a.get("font") or a.get("font_desc") or a.get("face") or a.get("font_family"):
                st["gimp_font"] = a.get("font") or a.get("font_desc") or a.get("face") or a.get("font_family")
                st["psname"] = None
            if a.get("size") and a["size"].lstrip("-").isdigit():
                st["size"] = int(a["size"]) / 1024.0 * base["yres"] / 72.0
            for k in ("foreground", "color", "fgcolor"):
                if a.get(k, "").startswith("#") and len(a[k]) >= 7:
                    st["color"] = a[k][:7].lower()
            if a.get("letter_spacing", "").lstrip("-").isdigit():
                st["letter_spacing"] = int(a["letter_spacing"]) / 1024.0 * base["yres"] / 72.0
            if a.get("weight") in ("bold", "ultrabold", "heavy") or a.get("font_weight") == "bold":
                st["bold"] = True
            if a.get("style") in ("italic", "oblique") or a.get("font_style") in ("italic", "oblique"):
                st["italic"] = True
        emit(node.text, st)
        for child in node:
            rec(child, st)
            emit(child.tail, st)

    style = {k: v for k, v in base.items() if k != "yres"}
    try:
        rec(ET.fromstring(markup), style)
    except ET.ParseError:
        return None
    return runs


def overlap_area(image, a, b):
    """Pixels where both layers are opaque (mean of the intersected selection x area)."""
    image.select_item(Gimp.ChannelOps.REPLACE, a)
    image.select_item(Gimp.ChannelOps.INTERSECT, b)
    try:
        sel = image.get_selection()
        h = sel.histogram(Gimp.HistogramChannel.VALUE, 0.0, 1.0)
        return h.mean * h.pixels
    finally:
        Gimp.Selection.none(image)


def guess_rotation(image, layer, font, size, nat_w, nat_h):
    """A text layer rotated in GIMP does not remember its angle. Sizes tell a
    quarter turn from an untouched layer; which quarter is decided by
    rendering both and keeping the one that overlaps the real pixels."""
    w, h = layer.get_width(), layer.get_height()
    tol = max(4, 0.04 * max(nat_w, nat_h))
    if abs(w - nat_w) <= tol and abs(h - nat_h) <= tol:
        return 0
    if not (abs(w - nat_h) <= tol and abs(h - nat_w) <= tol):
        return None                                           # scaled / sheared / free rotation
    best = (-1, None)
    for angle, kind in ((90, Gimp.RotationType.DEGREES90), (-90, Gimp.RotationType.DEGREES270)):
        # render the same text again, turn THAT, lay it over the real layer
        fresh = Gimp.TextLayer.new(image, layer.get_text() or "x", font, size, Gimp.Unit.pixel())
        image.insert_layer(fresh, None, 0)
        try:
            if layer.get_markup():
                fresh.set_markup(layer.get_markup())
            fresh.set_justification(layer.get_justification())
            fresh.set_line_spacing(layer.get_line_spacing())
            fresh.set_letter_spacing(layer.get_letter_spacing())
            if abs(fresh.get_width() - h) > tol or abs(fresh.get_height() - w) > tol:
                fresh.resize(h, w)                            # fixed box: same box before the turn
            fresh = fresh.transform_rotate_simple(kind, True, 0, 0)
            fresh.set_offsets(*layer.get_offsets()[1:])
            score = overlap_area(image, layer, fresh)
        finally:
            image.remove_layer(fresh)
        if score > best[0]:
            best = (score, angle)
    return best[1]


def describe_text(image, layer, notes):
    name = layer.get_name()
    par = text_parasite(layer)
    ours = get_parasite_json(layer)
    yres = image.get_resolution().yresolution or 72.0
    font = layer.get_font()
    size, unit = layer.get_font_size()
    if unit.get_id() != Gimp.Unit.pixel().get_id() and unit.get_factor() > 0:
        size = size * yres / unit.get_factor()
    markup = layer.get_markup()
    text = layer.get_text()
    base = {"gimp_font": font.get_name(), "psname": par.get("psname") or None, "size": size,
            "color": color_hex(layer.get_color()), "bold": False, "italic": False, "underline": False,
            "strike": False, "letter_spacing": layer.get_letter_spacing(), "yres": yres}
    runs = parse_markup(markup, base) if markup else None
    if runs is None:
        if markup:
            text = re.sub(r"<[^>]+>", "", markup)
            notes.add(f'"{name}": markup could not be parsed, mixed styles dropped')
        b = {k: v for k, v in base.items() if k != "yres"}
        runs = [{**b, "text": text or ""}]
    plain = "".join(r["text"] for r in runs)

    ok, nat_w, nat_h, ascent, _d = Gimp.text_get_extents_font(plain or "x", size, font)
    natural, _a = line_height(font, size)
    fixed = par.get("box-mode") == "fixed"
    if fixed:
        nat_w, nat_h = float(par.get("box-width", layer.get_width())), float(par.get("box-height", layer.get_height()))
    else:
        # extents ignore markup and spacing: measure a real dynamic twin instead
        twin = Gimp.TextLayer.new(image, plain or "x", font, size, Gimp.Unit.pixel())
        image.insert_layer(twin, None, 0)
        if markup:
            twin.set_markup(markup)
        twin.set_line_spacing(layer.get_line_spacing())
        twin.set_letter_spacing(layer.get_letter_spacing())
        nat_w, nat_h = twin.get_width(), twin.get_height()
        image.remove_layer(twin)

    if "angle" in ours:
        angle = float(ours["angle"])
    else:
        angle = guess_rotation(image, layer, font, size, nat_w, nat_h)
        if angle is None:
            notes.add(f'"{name}": text layer was transformed in GIMP (scaled or freely rotated) - kept as pixels, not editable text')
            return None
    ox, oy = layer.get_offsets()[1:]
    w, h = layer.get_width(), layer.get_height()
    cx, cy = ox + w / 2.0, oy + h / 2.0
    quarter = abs(round(angle)) % 180 == 90 and abs(angle - round(angle)) < 0.5
    if fixed or "angle" not in ours:
        bw, bh = (h, w) if quarter else (w, h)
        if "angle" in ours and not quarter and abs(angle) > 0.5:
            bw, bh = nat_w, nat_h                                # free rotation: layer bounds grew, box did not
    else:
        bw, bh = nat_w, nat_h

    info = {
        "shape": "box" if fixed else "point",
        "angle": angle,
        "box": {"w": bw, "h": bh, "cx": cx, "cy": cy},           # unrotated size + page centre, both shapes
        "ascent": ascent * (max(r["size"] for r in runs) / size if size else 1),
        "justification": ours.get("justification") if ours.get("justification", "").startswith("justify")
        else JUSTIFY_BACK.get(int(layer.get_justification()), "left"),
        "baseSize": size,
        "leading": natural + layer.get_line_spacing(),
        "indent": layer.get_indent(),
        "runs": runs,
        "font_back": ours.get("fonts") or {},                    # GIMP name -> original Photoshop name
    }
    outline = layer.get_outline()
    if outline != Gimp.TextOutline.NONE:
        ow = layer.get_outline_width()                       # (width, unit) in GIMP 3.2, a float before
        if isinstance(ow, tuple):
            ow = ow[0] * (yres / ow[1].get_factor() if ow[1].get_factor() > 0 else 1)
        nick = getattr(layer.get_outline_direction(), "value_nick", "")
        info["outline"] = {"width": ow, "color": color_hex(layer.get_outline_color()),
                           "position": {"outer": "outside", "inner": "inside"}.get(nick, "center"),
                           "only": outline == Gimp.TextOutline.STROKE_ONLY}
    return info


def describe_filters(layer, notes):
    """gegl:styles / gegl:dropshadow -> Layer Style numbers. Returns (effects, filters to hide)."""
    fx, hide = {}, []
    name = layer.get_name()
    for f in layer.get_filters():
        op = f.get_operation_name()
        if not f.get_visible():
            continue
        if op not in STYLE_OPS:
            notes.add(f'"{name}": filter {op} has no Layer Style equivalent, it was merged into the pixels')
            continue
        c = f.get_config()
        g = c.get_property
        used = False
        if op == "gegl:styles":
            if g("enableoutline") and "stroke" not in fx:
                fx["stroke"] = {"size": g("outline"), "color": color_hex(g("outline-color")),
                                "opacity": g("outline-opacity") * f.get_opacity(), "position": "outside"}
                used = True
                if abs(g("outline-x")) > 0.5 or abs(g("outline-y")) > 0.5:
                    notes.add(f'"{name}": outline offset ({g("outline-x"):g}, {g("outline-y"):g}) has no Layer Style equivalent, stroke is centred')
            if g("shadow-opacity") > 0 and "shadow" not in fx:
                fx["shadow"] = {"x": g("shadow-x"), "y": g("shadow-y"), "color": color_hex(g("shadow-color")),
                                "opacity": g("shadow-opacity") * f.get_opacity(), "blur": g("shadow-radius") * 2.0,
                                "grow": g("shadow-grow-radius")}
                used = True
            fill = color_hex(g("color-fill"))
            if g("color-policy") == "solidcolor" or (g("color-policy") == "multiply" and fill != "#ffffff"):
                fx["fill"] = {"color": fill, "blend": "multiply" if g("color-policy") == "multiply" else "normal"}
                used = True
            for flag, label in (("enablebevel", "bevel"), ("enableinnerglow", "inner glow"), ("enableimage", "image overlay")):
                if g(flag):
                    notes.add(f'"{name}": Text Styling {label} not converted')
        elif "shadow" not in fx:
            fx["shadow"] = {"x": g("x"), "y": g("y"), "color": color_hex(g("color")),
                            "opacity": min(1.0, g("opacity")) * f.get_opacity(), "blur": g("radius") * 2.0,
                            "grow": g("grow-radius")}
            used = True
        if used:
            hide.append(f)
    return fx, hide


def xcf2psd(task):
    src = task["src"]
    notes = set()
    image = load(src)
    try:
        if task.get("preview"):
            save_preview(image, task["preview"])
        entries, to_hide = [], []
        for path, l in list(walk(image.get_layers())):
            text = describe_text(image, l, notes) if isinstance(l, Gimp.TextLayer) else None
            fx, hide = describe_filters(l, notes)
            if text and text.get("outline"):
                o = text.pop("outline")
                if "stroke" in fx:
                    notes.add(f'"{l.get_name()}": has both a text outline and a Text Styling outline, the filter one was kept')
                else:
                    # GIMP draws its own text outline into the pixels; Photoshop redraws it as a Stroke
                    fx["stroke"] = {"size": o["width"], "color": o["color"], "opacity": 1.0, "position": o["position"]}
                    if o["only"]:
                        fx["fill_opacity"] = 0.0
            if text or fx:
                entries.append({"path": list(path), "name": l.get_name(), "text": text, "effects": fx or None})
                to_hide += hide
        # Photoshop draws the Layer Styles itself: export the layer pixels without
        # them - but the flattened image inside the PSD (what viewers, thumbnails
        # and psd_to_jpg.py show) must still have the outlines, so that one comes
        # from a first export made with everything visible
        composite = None
        if to_hide:
            composite = task["out_psd"][:-4] + "_full.psd"
            save(image, composite)
            for f in to_hide:
                f.set_visible(False)
        save(image, task["out_psd"])
        res = image.get_resolution()
        json.dump({"width": image.get_width(), "height": image.get_height(), "yres": res.yresolution,
                   "composite_from": composite, "layers": entries}, open(task["out_info"], "w"), indent=1)
    finally:
        image.delete()
    for n in sorted(notes):
        log(f"NOTE {src}: {n}")
    log(f"OK {src} -> {task['out_psd']}: {sum(1 for e in entries if e['text'])} text layer(s), "
        f"{sum(1 for e in entries if e['effects'])} styled layer(s) described")


def main():
    job = json.load(open(JOB_PATH))
    Gimp.context_set_interpolation(Gimp.InterpolationType.CUBIC)
    Gimp.context_set_transform_resize(Gimp.TransformResize.ADJUST)
    for task in job["tasks"]:
        try:
            (psd2xcf if task["mode"] == "psd2xcf" else xcf2psd)(task)
        except Exception:
            log(f"FAIL {task.get('src')}\n" + traceback.format_exc())


try:
    main()
    log("DONE")
except Exception:
    log("FATAL\n" + traceback.format_exc())
_log.close()
