"""Side-by-side preview images for the pages psd-sync would keep.

One JPG per divergent page: folder1's flattened page on the left, folder2's on
the right, each labelled with its pixel size, so a human can decide by eye.
"""
from pathlib import Path

from PIL import Image, ImageDraw
from psd_tools import PSDImage

Image.MAX_IMAGE_PIXELS = None  # spreads are 14000+ px wide
PREVIEW_HEIGHT = 1600
DIVERGENT = {"cut", "different"}
GAP_PX = 20
LABEL_BAR_PX = 34
LEFT_TAG = "NEW (folder1)"
RIGHT_TAG = "CURRENT (folder2)"


def flatten(path):
    """Flattened RGB page and its (width, height); uses the PSD's own preview
    image when it has one, which is far faster than compositing layers."""
    psd = PSDImage.open(path)
    img = psd.topil() or psd.composite()
    return img.convert("RGB"), psd.size


def labelled_thumb(img, size, text):
    """`img` scaled to PREVIEW_HEIGHT with a black label bar on top."""
    ratio = PREVIEW_HEIGHT / img.height
    thumb = img.resize((max(1, round(img.width * ratio)), PREVIEW_HEIGHT), Image.LANCZOS)
    draw = ImageDraw.Draw(thumb)
    draw.rectangle([0, 0, thumb.width, LABEL_BAR_PX], fill=(0, 0, 0))
    draw.text((8, 8), f"{text} {size[0]}x{size[1]}", fill=(255, 255, 0))
    return thumb


def side_by_side(left, right):
    sheet = Image.new("RGB", (left.width + right.width + GAP_PX, PREVIEW_HEIGHT), (255, 0, 255))
    sheet.paste(left, (0, 0))
    sheet.paste(right, (left.width + GAP_PX, 0))
    return sheet


def make_previews(src_dir, dst_dir, rows, out_dir):
    """Write `<out_dir>/<page>.jpg` for every row whose status is cut/different;
    returns the list of written paths (empty when nothing diverged).

    Example: make_previews(Path("new"), Path("old"), rows, Path("~/Downloads/new comparison"))
    """
    todo = [r["file"] for r in rows if r["status"] in DIVERGENT]
    if not todo:
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name in todo:
        new_img, new_size = flatten(src_dir / name)
        old_img, old_size = flatten(dst_dir / name)
        sheet = side_by_side(labelled_thumb(new_img, new_size, f"{LEFT_TAG} {name}"),
                             labelled_thumb(old_img, old_size, f"{RIGHT_TAG} {name}"))
        target = out_dir / f"{Path(name).stem}.jpg"
        sheet.save(target, quality=88)
        written.append(target)
    return written
