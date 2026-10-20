#!/usr/bin/env python3
"""Local Anima backend for gen_panel.py: ONE panel drawn by Anima Turbo in ComfyUI.

Free and offline (~18 s per ~1 MP panel on a 6 GB GPU), but TEXT ONLY: Anima
cannot take reference images, so the model sheets and scenario images that
Higgsfield gets are not used; only their written descriptions reach the
prompt. Characters drift off-model more easily. Use it for panels where the
look of a specific character matters least (backgrounds, establishing shots,
crowds, characters without a model sheet), or to rough out a page for free.

Anima also cannot draw empty balloons reliably (tested 2026-10-20: none, or
odd blobs), so the art is generated WITHOUT balloons and this module draws
one empty white oval per dialogue line on top: in the top band, in
reading order, sized by word count. make_layout.py then finds them like any
other empty balloon. `balloons` (fractions of the panel, x,y,w,h) overrides
the automatic placement, one box per line in order.

Needs (installed by local-ai-setup with COMFYUI_MODEL_SETS including
"anima"): diffusion_models/anima-turbo-v1.1.safetensors,
text_encoders/qwen_3_06b_base.safetensors, vae/qwen_image_vae.safetensors.
Server access (COMFYUI_URL / COMFYUI_SERVICE) is comfyui.py's.

Example:
    png = generate(panel, cfg, notes=["Ryo: spiky brown hair, goggles"], seed=7)
"""
import math
import re
import sys

import cv2
import numpy as np

import comfyui

MODEL = {"unet": "anima-turbo-v1.1.safetensors", "clip": "qwen_3_06b_base.safetensors",
         "vae": "qwen_image_vae.safetensors"}
MODEL_MP = 1.0                      # Anima works between 512² and 1536²
# no "comic": on Danbooru that tag means a multi-panel page, and Anima then splits the panel
DEFAULT_STYLE = "monochrome, greyscale, manga, screentone, ink, lineart"
QUALITY = "masterpiece, best quality, score_7, safe"
NEGATIVE = ("worst quality, low quality, score_1, score_2, score_3, blurry, jpeg artifacts, text, "
            "letters, speech bubble, signature, watermark, artist name, panel border, multiple panels")
# the phrase plan_panels.py puts in place of each quoted line, with the "," / "and" that joins it
BALLOON_RE = re.compile(r"(?:\s*,|\s+and)?\s*\(an EMPTY balloon sized for about \d+ words?\)")


def missing_models() -> list[str]:
    """Model files ComfyUI does not have (empty = ready). Starts COMFYUI_SERVICE
    once when the server is down; exits with the reason when it stays down."""
    comfyui.ensure_server()
    have: set[str] = set()
    for kind in ("diffusion_models", "text_encoders", "vae"):
        have |= comfyui.model_files(kind)
    return [f for f in MODEL.values() if f not in have]


def model_size(box_w: float, box_h: float) -> tuple[int, int]:
    """~MODEL_MP pixels at the panel box's own aspect, sides multiple of 16, so
    assemble_page.py crops (almost) nothing — balloons drawn near the edges survive."""
    scale = math.sqrt(MODEL_MP * 1e6 / (box_w * box_h))
    return max(256, round(box_w * scale / 16) * 16), max(256, round(box_h * scale / 16) * 16)


def build_prompt(panel: dict, cfg: dict, notes: list[str], fix: str | None) -> str:
    """Quality tags + style tags + the panel's prose with its balloon phrases
    removed (balloons are drawn afterwards) + the character design notes."""
    scene = BALLOON_RE.sub("", panel["prompt"]).strip()
    parts = [f"{QUALITY}, {cfg.get('anima_style', DEFAULT_STYLE)}. {scene}"]
    parts += [f"Character design: {n}" for n in notes]
    if fix:
        parts.append(fix)
    return "\n".join(parts)


def workflow(prompt: str, w: int, h: int, seed: int) -> dict:
    node = comfyui.node
    return {
        "1": node("UNETLoader", unet_name=MODEL["unet"], weight_dtype="default"),
        "2": node("CLIPLoader", clip_name=MODEL["clip"], type="stable_diffusion", device="default"),
        "3": node("VAELoader", vae_name=MODEL["vae"]),
        "4": node("CLIPTextEncode", text=prompt, clip=["2", 0]),
        "5": node("CLIPTextEncode", text=NEGATIVE, clip=["2", 0]),
        "6": node("EmptyLatentImage", width=w, height=h, batch_size=1),
        # Turbo: CFG 1, 8-12 steps (model card); the negative is then ignored by the sampler
        "7": node("KSampler", model=["1", 0], positive=["4", 0], negative=["5", 0], latent_image=["6", 0],
                  seed=seed, steps=10, cfg=1.0, sampler_name="euler", scheduler="simple", denoise=1.0),
        "8": node("VAEDecode", samples=["7", 0], vae=["3", 0]),
        "9": node("PreviewImage", images=["8", 0]),
    }


def auto_balloons(word_counts: list[int], rtl: bool = False) -> list[tuple[float, float, float, float]]:
    """One box (x, y, w, h as fractions of the panel) per line: ovals ~1.6:1
    whose area grows with the word count, laid left to right (right to left
    with rtl) in rows across the top, 5% away from the edges."""
    boxes, x, y, row_h = [], 0.05, 0.05, 0.0
    for words in word_counts:
        area = min(0.12, 0.025 + 0.006 * words)
        bw, bh = min(0.9, math.sqrt(area * 1.6)), math.sqrt(area / 1.6)
        if x + bw > 0.95 and x > 0.05:
            x, y, row_h = 0.05, y + row_h + 0.03, 0.0
        boxes.append((1 - x - bw if rtl else x, y, bw, bh))
        x, row_h = x + bw + 0.03, max(row_h, bh)
    return boxes


def draw_balloons(img: np.ndarray, boxes: list[tuple[float, float, float, float]]) -> np.ndarray:
    """White oval + small tail toward the panel centre, closed black outline,
    empty inside (make_layout.py detects it as an empty balloon)."""
    H, W = img.shape[:2]
    out, line = img.copy(), max(2, round(W * 0.004))
    for fx, fy, fw, fh in boxes:
        cx, cy, ax, ay = int((fx + fw / 2) * W), int((fy + fh / 2) * H), int(fw * W / 2), int(fh * H / 2)
        tx = cx + int(np.sign(W / 2 - cx) * ax * 0.3)
        tail = np.array([[cx - ax // 6, cy + ay * 3 // 4], [cx + ax // 6, cy + ay * 3 // 4],
                         [tx, min(H - 3, cy + ay + int(0.06 * H))]], np.int32)
        cv2.fillPoly(out, [tail], (0, 0, 0))
        cv2.ellipse(out, (cx, cy), (ax + line, ay + line), 0, 0, 360, (0, 0, 0), -1)
        cv2.fillPoly(out, [_shrink(tail, line)], (255, 255, 255))
        cv2.ellipse(out, (cx, cy), (ax, ay), 0, 0, 360, (255, 255, 255), -1)
    return out


def _shrink(tri: np.ndarray, px: int) -> np.ndarray:
    centre = tri.mean(axis=0)
    return (centre + (tri - centre) * (1 - 2.5 * px / max(1.0, np.ptp(tri[:, 1])))).astype(np.int32)


def generate(panel: dict, cfg: dict, notes: list[str], seed: int, fix: str | None = None,
             balloons: list[tuple[float, float, float, float]] | None = None) -> tuple[bytes, str]:
    """Draw the panel and its empty balloons. Returns (PNG bytes, prompt sent)."""
    missing = missing_models()
    if missing:
        sys.exit("ERROR Anima model files missing in ComfyUI: " + ", ".join(missing)
                 + " — add \"anima\" to local-ai-setup's COMFYUI_MODEL_SETS and rerun it")
    _x, _y, bw, bh = panel["box"]
    w, h = model_size(bw, bh)
    prompt = build_prompt(panel, cfg, notes, fix)
    art = comfyui.result_image(comfyui.run(workflow(prompt, w, h, seed)))
    lines = panel.get("dialogue", [])
    boxes = balloons or auto_balloons([len(str(t).split()) for t in lines], cfg.get("reading") == "rtl")
    png = cv2.imencode(".png", draw_balloons(art, boxes))[1].tobytes()
    return png, prompt
