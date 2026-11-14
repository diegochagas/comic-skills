#!/usr/bin/env python3
# Copied unchanged (below this note) from GIMPhoto
# (github.com/diegochagas/gimphoto, plugins/comfyui-service/comfyui_client.py),
# MIT License: the ComfyUI graphs behind GIMPhoto's Generative Fill. inpaint_qwen.py
# drives them, so text is erased here by the same local Qwen-Image-Edit process as
# selecting it in GIMPhoto and asking to erase it. Update by copying the file again.
# Vendored from gimp-setup (github.com/diegochagas/gimp-setup,
# assets/plug-ins/comfyui/comfyui_client.py at a4fc462), MIT License,
# Copyright (c) 2026. Unchanged: GIMPhoto's Generative Fill and
# Generate Image use its tuned FLUX.2 klein / Qwen-Image-Edit graphs; the
# address is passed in (GIMPhoto's gimphoto-comfyui parasite), so its own
# lookup in ~/.config/PhotoGIMP is not used.
"""ComfyUI client shared by the gimp-setup AI plug-ins (fully local AI).

Runs mask-based inpainting and text-to-image on a local ComfyUI server
with one of two edit models:

    klein   FLUX.2 klein (distilled, 4 steps)             fast
    qwen    Qwen-Image-Edit + Lightning 4-step LoRA        slower, follows
                                                           prompts better

Pure standard library and no GIMP imports, so the same file is installed
next to every ComfyUI plug-in (Generative Fill, AI Remove Selection and
AI Restore Photo) and can be run outside GIMP:

    comfyui_client.py fill   <klein|qwen> image.png mask.png out.png "a red ball"
    comfyui_client.py hide   <klein|qwen> image.png mask.png out.png
    comfyui_client.py remove <klein|qwen> image.png mask.png out.png
    comfyui_client.py clean  <klein|qwen> image.png mask.png out.png
    comfyui_client.py generate out.png "a lighthouse at dusk" [WxH]
    comfyui_client.py restore <klein|qwen> photo.png out.png [burns]

Masks are white-on-black PNGs of the image's size: WHITE marks the area
to change. `hide` hides the selected area from the model (objects in
photos); `clean` lets the model see it (text or marks printed over a
texture); `remove` and `fill` pick one, check the result and redo the
job the other way when it failed — see _inpaint_graph and inpaint.
`restore` redraws a whole scanned print with its damage repaired (the
Restore Photo plug-in keeps only the pixels that changed).

All pixel work (scaling, masking, pre-filling) happens inside the
ComfyUI graph with core nodes; the only custom node used is
ComfyUI-GGUF, and only when the Qwen model is a .gguf file.

The model files are not hard-coded: they are picked from what the
server's loader nodes list, by name pattern.

Server address: COMFYUI_URL environment variable, then the shared
gimp-setup file ~/.config/PhotoGIMP/comfyui-url (host and Flatpak
sandbox copies), then ComfyUI's own default.
"""

import json
import os
import random
import re
import struct
import sys
import zlib
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

DEFAULT_URL = "http://127.0.0.1:8188"   # ComfyUI's default listen address
SERVICE = "comfyui"     # systemd user unit written by local-ai-setup

MODELS = {
    "klein": "FLUX.2 klein",
    "qwen": "Qwen-Image-Edit",
}

# The models work at about 1 megapixel. Small regions are enlarged (at
# most MAX_UPSCALE times) because tiny inputs come back mushy.
WORK_PIXELS = 1024 * 1024
MAX_UPSCALE = 2.0
MIN_SIDE = 256
MASK_GROW = 6            # px (at work size) the editable area is grown

# Fills are run on a tight crop: the area's box plus FILL_MARGIN of its
# size on each side (at least FILL_MARGIN_MIN px). The models are not told
# where the paintable zone ends: on a wider view they compose objects
# bigger than the zone, or off-centre, and its border cuts them (shrinking
# the zone only moves the cut inwards). What they do reliably is keep an
# object whole inside the PICTURE they are given — so the picture is made
# to be the area, with just a sliver of margin for continuity. Seeing the
# area's own background gives them the context. It also gives the fill
# more pixels. Fills neither grow nor shrink the zone (FILL_GROW).
FILL_MARGIN = 0.05
FILL_MARGIN_MIN = 8
FILL_GROW = 0

# The hidden area is pre-filled with the colors around it, diffused
# inwards on a copy PREFILL_SHRINK times smaller.
PREFILL_SHRINK = 16
PREFILL_PASSES = 10

# A hidden area sometimes comes back as a flat patch (halftone scans,
# printed paper): its fine detail, relative to the detail in a ring
# around it, is then ~0.0, against 0.45-1.1 for good fills. Below
# FLAT_RATIO the job is redone letting the model see the area. A ring
# with less detail than FLAT_MIN_DETAIL (0-255) is a smooth background,
# where a flat fill is the right answer.
FLAT_RATIO = 0.2
FLAT_MIN_DETAIL = 3.0

# A see-through fill sometimes leaves the area as it was (Qwen adds next
# to an object instead of replacing it). The area counts as changed when
# enough of it changed a lot: the share of its STATS_SIDE cells whose
# blurred change exceeds CHANGED_LEVEL (0-255) was 0.00-0.05 when that
# happened and 0.14-0.82 when the prompt was painted — 0.14 being a small
# object on a large area, which a plain mean cannot tell from a failure.
# Below CHANGED_SHARE the job is redone with the area hidden.
CHANGED_LEVEL = 30.0
CHANGED_SHARE = 0.09
STATS_SIDE = 32          # px of the small detail maps sent back
RING_WIDTH = 48          # px (at work size) of the ring around the area

# Seconds. A cold start loads several GB from disk and, on a small GPU,
# keeps part of the model in system RAM, so the first run is slow.
TIMEOUT = 1800
POLL_INTERVAL = 1.0

# Seconds to wait for a local ComfyUI that GIMP's launcher started and that
# is still booting (features/comfyui-with-gimp.sh writes the
# "comfyui-autostart" setting); about 20 s on a warm disk.
STARTUP_WAIT = 120

FILL_PROMPT = (
    "A region of this image was smeared into a blurry smooth patch. "
    "Repaint that patch with: {prompt}. Blend it seamlessly with the rest "
    "of the image, matching its style, lighting, colors, grain and "
    "perspective. Keep everything else unchanged. No text, no borders, "
    "no watermarks."
)
REMOVE_PROMPT = (
    "A region of this image was smeared into a blurry smooth patch. "
    "Restore that patch: repaint it with sharp detail so it seamlessly "
    "continues the surrounding background, with the same texture, "
    "pattern, grain, colors and lighting as the area around it. Do not "
    "add any new object, person or text. Keep everything else unchanged."
)
EDIT_PROMPT = (
    "{prompt}. Anything new is shown whole and centered with some space "
    "around it, never cut off by the picture's edges. Blend the result seamlessly with the rest of the image, "
    "keeping its texture, pattern, grain, colors and lighting. Keep "
    "everything else unchanged."
)
CLEAN_PROMPT = (
    "Remove all the text, lettering, logos, scratches, stains and marks "
    "from the image, leaving only the clean background with its original "
    "texture, pattern, grain and colors, continued seamlessly. Do not add "
    "any new object or text."
)
# A fill prompt that asks to continue the picture ("continue the image",
# "extend the background", "fill in the blank", "continuar a imagem")
# is an outpaint: the selection (typically a blank border — white,
# transparent, a canvas made bigger) is hidden from the model and the
# picture next to it is extended into it, one side at a time
# (outpaint_strips). Measured on a poster in a white band: seeing the
# blank, FLUX.2 klein framed the poster on a grey wall or drew new
# characters around it; hidden but on the whole image, it drew the poster
# again, blurry and bigger, behind itself (every seed and wording) — it
# sees a rectangle and treats it as an object. Qwen did the same. Given
# only the band and OUTPAINT_CONTEXT px of picture next to it, klein
# continued the clouds, hair and marble; distinctive things near the edge
# (a logo, a sticker) are sometimes repeated.
OUTPAINT_CONTEXT = 256
CONTINUE_WORDS = re.compile(
    r"\b(continu\w*|extend\w*|expand\w*|outpaint\w*|uncrop\w*|prolong\w*|"
    r"estend\w*|complet\w*|preench\w*|"
    r"fill\s+(in|the\s+(rest|gap|blank|empty|space|area|border|margin)s?))\b"
    r"|^\s*fill(\s+it)?\s*$",
    re.IGNORECASE)
OUTPAINT_PROMPT = (
    "The smooth blurry area is a part of the picture that is missing. "
    "Extend the picture into it: continue it seamlessly, with the same "
    "drawing, scene, colors, patterns and lighting, as if the picture had "
    "been larger. The picture fills the whole frame edge to edge: no "
    "border, frame, margin, paper, wall or shadow. Do not repeat anything "
    "that is already in the picture. No text, no logos. Request: {prompt}."
)
# Photo restoration (the same prompts as the photo-restore project).
RESTORE_PROMPT = (
    "This is a scan of an old damaged photo print. The white and brown "
    "blotches, flakes, stains, scratches, creases and specks are damage "
    "where the picture is missing, and the plain areas outside the print's "
    "edges are missing parts of the photo. Repair the photo: fill every "
    "damaged or missing area with what would naturally be there, "
    "continuing the surrounding people, clothes, floor and background "
    "seamlessly. Keep everything that is not damaged exactly as it is: "
    "same framing, same colors, same faces. No text."
)
BURN_PROMPT = (
    "This is a scan of an old damaged photo print. It is covered with "
    "chemical damage: shiny gold, orange and brown metallic flakes and "
    "specks, burn stains, rusty blotches, white spots and mottled "
    "discoloured patches. None of that is part of the picture. Remove ALL "
    "of it and show the clean photo underneath: walls, floor, sky, clothes "
    "and background must be smooth and even where the damage was. Keep "
    "every person, face, expression, object and the framing exactly as "
    "they are. No text."
)


class ComfyUIError(RuntimeError):
    pass


# ------------------------------------------------------------ server address

def _shared_file_paths(name):
    home = os.path.expanduser("~")
    xdg = os.environ.get("XDG_CONFIG_HOME", os.path.join(home, ".config"))
    return [
        os.path.join(home, ".config", "PhotoGIMP", name),
        os.path.join(xdg, "PhotoGIMP", name),
    ]


def get_url(configured=None):
    """Server address: explicit value, env var, shared file, default."""
    url = (configured or "").strip() or os.environ.get("COMFYUI_URL", "").strip()
    if not url:
        for path in _shared_file_paths("comfyui-url"):
            try:
                with open(path, encoding="utf-8") as f:
                    url = f.read().strip()
            except OSError:
                continue
            if url:
                break
    return (url or DEFAULT_URL).rstrip("/")


def start_command():
    """Shell command that starts the ComfyUI service local-ai-setup
    installs (steps/comfyui); COMFYUI_SERVICE names another systemd unit."""
    unit = os.environ.get("COMFYUI_SERVICE", "").strip() or SERVICE
    return "systemctl --user start %s" % unit


def _is_local(url):
    return (urllib.parse.urlsplit(url).hostname or "") in ("127.0.0.1", "localhost", "::1")


def autostarted():
    """True when GIMP's launcher starts and stops ComfyUI with GIMP."""
    for path in _shared_file_paths("comfyui-autostart"):
        try:
            with open(path, encoding="utf-8") as f:
                return f.read().strip().lower() in ("yes", "true", "1")
        except OSError:
            continue
    return False


def unreachable_message(url):
    host = urllib.parse.urlsplit(url).hostname or ""
    if host in ("127.0.0.1", "localhost", "::1") and autostarted():
        how = ("It is started together with GIMP, but did not answer within "
               "%d s. See why with:\n\n    journalctl --user -u %s\n\nor "
               "start it by hand with:\n\n    %s"
               % (STARTUP_WAIT, os.environ.get("COMFYUI_SERVICE", "").strip() or SERVICE,
                  start_command()))
    elif host in ("127.0.0.1", "localhost", "::1"):
        how = ("Start it in a terminal with:\n\n    %s\n\nwait until "
               "%s opens in a browser (about 20 s), then run the tool "
               "again." % (start_command(), url))
    else:
        how = "Start ComfyUI on that machine, then run the tool again."
    return ("ComfyUI is not reachable at %s. %s\n\nWrong address? Fix it "
            "in COMFYUI_URL / ~/.config/PhotoGIMP/comfyui-url." % (url, how))


# ---------------------------------------------------------------- HTTP layer

_server_seen = set()


def _wait_for_server(base):
    """A ComfyUI the launcher started with GIMP may still be booting: poll
    until it answers (True) or STARTUP_WAIT runs out (False)."""
    if base in _server_seen:
        return False                    # it answered before: it went away
    if not (_is_local(base) and autostarted()):
        return False
    deadline = time.time() + STARTUP_WAIT
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/system_stats", timeout=5):
                _server_seen.add(base)
                return True
        except (urllib.error.URLError, OSError):
            time.sleep(2)
    return False


def _request(url, data=None, headers=None, timeout=60, _retry=True):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            _server_seen.add("/".join(url.split("/")[:3]))
            return resp.read()
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            body = ""
        raise ComfyUIError("ComfyUI HTTP %d on %s: %s"
                           % (e.code, url, body[:600]))
    except (urllib.error.URLError, OSError):
        base = "/".join(url.split("/")[:3])
        if _retry and _wait_for_server(base):
            return _request(url, data, headers, timeout, _retry=False)
        raise ComfyUIError(unreachable_message(base))


def _post_json(url, payload, timeout=60):
    return _request(url, json.dumps(payload).encode("utf-8"),
                    {"Content-Type": "application/json"}, timeout)


def _upload(url, name, png_bytes):
    """Upload a PNG into ComfyUI's input folder; returns its LoadImage name.

    Fixed names are overwritten on every run (LoadImage re-reads a file
    whose content changed), so the input folder does not fill up.
    """
    boundary = uuid.uuid4().hex
    fields = (
        "--%s\r\nContent-Disposition: form-data; name=\"subfolder\"\r\n\r\n"
        "gimp-setup\r\n"
        "--%s\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\n"
        "true\r\n"
        "--%s\r\nContent-Disposition: form-data; name=\"image\"; "
        "filename=\"%s\"\r\nContent-Type: image/png\r\n\r\n"
        % (boundary, boundary, boundary, name)
    ).encode("utf-8")
    body = fields + png_bytes + ("\r\n--%s--\r\n" % boundary).encode("utf-8")
    reply = json.loads(_request(
        url + "/upload/image", body,
        {"Content-Type": "multipart/form-data; boundary=" + boundary}))
    if reply.get("subfolder"):
        return "%s/%s" % (reply["subfolder"], reply["name"])
    return reply["name"]


# ------------------------------------------------------------ model discovery

def _loader_choices(url, node, input_name):
    """File names a loader node offers; [] when the node is not installed."""
    try:
        info = json.loads(_request("%s/object_info/%s" % (url, node)))
    except ComfyUIError as e:
        if "not reachable" in str(e):
            raise
        return []
    try:
        spec = info[node]["input"]["required"][input_name]
    except KeyError:
        return []
    choices = spec[0]
    if isinstance(choices, list):
        return choices
    # Newer servers describe combos as ["COMBO", {"options": [...]}].
    if len(spec) > 1 and isinstance(spec[1], dict):
        return spec[1].get("options", [])
    return []


def _find(url, loaders, pattern, what, env=None):
    """First (node, file) whose name matches; `env` names an override."""
    wanted = os.environ.get(env, "").strip() if env else ""
    regex = re.compile(pattern, re.IGNORECASE)
    for node, input_name in loaders:
        for choice in _loader_choices(url, node, input_name):
            if (wanted and choice == wanted) or (not wanted
                                                 and regex.search(choice)):
                return node, choice
    raise ComfyUIError(
        "ComfyUI has no %s model installed (looked for a file matching "
        "/%s/ in its models folder)." % (what, wanted or pattern))


UNET_LOADERS = [("UNETLoader", "unet_name"), ("UnetLoaderGGUF", "unet_name")]
CLIP_LOADERS = [("CLIPLoader", "clip_name")]
VAE_LOADERS = [("VAELoader", "vae_name")]
LORA_LOADERS = [("LoraLoaderModelOnly", "lora_name")]


def _model_nodes(url, model):
    """Loader nodes for `model`: returns (graph, model_ref, clip_ref, vae_ref)."""
    if model == "klein":
        unet_node, unet = _find(url, UNET_LOADERS, r"flux.?2.*klein",
                                "FLUX.2 klein", "COMFYUI_KLEIN_UNET")
        # klein 9B pairs with the 8B text encoder, klein 4B with the 4B one.
        encoder = r"qwen.?3.?8b" if re.search(r"9b", unet, re.I) else r"qwen.?3.?4b"
        _, clip = _find(url, CLIP_LOADERS, encoder,
                        "FLUX.2 klein text encoder", "COMFYUI_KLEIN_CLIP")
        _, vae = _find(url, VAE_LOADERS, r"flux.?2.*vae",
                       "FLUX.2 VAE", "COMFYUI_KLEIN_VAE")
        clip_type = "flux2"
    elif model == "qwen":
        unet_node, unet = _find(url, UNET_LOADERS, r"qwen.*image.*edit",
                                "Qwen-Image-Edit", "COMFYUI_QWEN_UNET")
        _, clip = _find(url, CLIP_LOADERS, r"qwen.?2\.?5.?vl",
                        "Qwen-Image-Edit text encoder", "COMFYUI_QWEN_CLIP")
        _, vae = _find(url, VAE_LOADERS, r"qwen.?image.?vae",
                       "Qwen-Image VAE", "COMFYUI_QWEN_VAE")
        _, lora = _find(url, LORA_LOADERS, r"qwen.*image.*edit.*lightning.*4step",
                        "Qwen-Image-Edit Lightning 4-step LoRA",
                        "COMFYUI_QWEN_LORA")
        clip_type = "qwen_image"
    else:
        raise ComfyUIError("Unknown ComfyUI model: %s" % model)

    unet_inputs = {"unet_name": unet}
    if unet_node == "UNETLoader":
        unet_inputs["weight_dtype"] = "default"
    graph = {
        "unet": {"class_type": unet_node, "inputs": unet_inputs},
        "clip": {"class_type": "CLIPLoader",
                 "inputs": {"clip_name": clip, "type": clip_type,
                            "device": "default"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
    }
    model_ref = ["unet", 0]
    if model == "qwen":
        graph["shift"] = {"class_type": "ModelSamplingAuraFlow",
                          "inputs": {"model": model_ref, "shift": 3.1}}
        graph["cfgnorm"] = {"class_type": "CFGNorm",
                            "inputs": {"model": ["shift", 0], "strength": 1.0}}
        graph["lora"] = {"class_type": "LoraLoaderModelOnly",
                         "inputs": {"model": ["cfgnorm", 0], "lora_name": lora,
                                    "strength_model": 1.0}}
        model_ref = ["lora", 0]
    return graph, model_ref, ["clip", 0], ["vae", 0]


# ------------------------------------------------------------------- graphs

def _seed():
    return random.randint(0, 2 ** 48)


def _sampler_nodes(model, model_ref, positive, negative, latent, width, height):
    """Distilled 4-step sampling; the result latent is node "sampled"."""
    if model == "qwen":
        return {
            "sampled": {"class_type": "KSampler", "inputs": {
                "model": model_ref, "positive": positive, "negative": negative,
                "latent_image": latent, "seed": _seed(), "steps": 4,
                "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple",
                "denoise": 1.0}},
        }
    return {
        "noise": {"class_type": "RandomNoise", "inputs": {"noise_seed": _seed()}},
        "guider": {"class_type": "CFGGuider", "inputs": {
            "model": model_ref, "positive": positive, "negative": negative,
            "cfg": 1.0}},
        "ksel": {"class_type": "KSamplerSelect",
                 "inputs": {"sampler_name": "euler"}},
        "sigmas": {"class_type": "Flux2Scheduler", "inputs": {
            "steps": 4, "width": width, "height": height}},
        "sampled": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["noise", 0], "guider": ["guider", 0],
            "sampler": ["ksel", 0], "sigmas": ["sigmas", 0],
            "latent_image": latent}},
    }


def _generate_graph(url, prompt, width, height):
    """Text-to-image graph (FLUX.2 klein); the image is output node "out"."""
    graph, model_ref, clip, vae = _model_nodes(url, "klein")
    graph.update({
        "pos": {"class_type": "CLIPTextEncode",
                "inputs": {"clip": clip, "text": prompt}},
        "neg": {"class_type": "ConditioningZeroOut",
                "inputs": {"conditioning": ["pos", 0]}},
        "latent": {"class_type": "EmptyFlux2LatentImage", "inputs": {
            "width": width, "height": height, "batch_size": 1}},
    })
    graph.update(_sampler_nodes("klein", model_ref, ["pos", 0], ["neg", 0],
                                ["latent", 0], width, height))
    graph.update({
        "decoded": {"class_type": "VAEDecode",
                    "inputs": {"samples": ["sampled", 0], "vae": vae}},
        # PreviewImage writes to ComfyUI's temp folder, not output/.
        "out": {"class_type": "PreviewImage",
                "inputs": {"images": ["decoded", 0]}},
    })
    return graph


def _prefill_nodes(work_w, work_h):
    """Nodes that fill the masked area of "img" with the colors around it.

    Core nodes only: on a small copy the hole starts as the image's mean
    color, then a few blur-and-paste-back passes pull the border colors
    inwards. The result is node "hidden".
    """
    small_w = max(8, work_w // PREFILL_SHRINK)
    small_h = max(8, work_h // PREFILL_SHRINK)

    def scale(image, method, width, height):
        return {"class_type": "ImageScale", "inputs": {
            "image": image, "upscale_method": method, "width": width,
            "height": height, "crop": "disabled"}}

    def paste(destination, source, mask):
        return {"class_type": "ImageCompositeMasked", "inputs": {
            "destination": destination, "source": source, "x": 0, "y": 0,
            "resize_source": False, "mask": mask}}

    nodes = {
        "pf_small": scale(["img", 0], "area", small_w, small_h),
        "pf_mask_img": {"class_type": "MaskToImage",
                        "inputs": {"mask": ["mask", 0]}},
        "pf_mask_small": scale(["pf_mask_img", 0], "area", small_w, small_h),
        "pf_mask_raw": {"class_type": "ImageToMask", "inputs": {
            "image": ["pf_mask_small", 0], "channel": "red"}},
        # Any small pixel the hole touches counts as hole.
        "pf_mask_any": {"class_type": "ThresholdMask",
                        "inputs": {"mask": ["pf_mask_raw", 0], "value": 0.02}},
        "pf_mask": {"class_type": "GrowMask", "inputs": {
            "mask": ["pf_mask_any", 0], "expand": 1,
            "tapered_corners": False}},
        "pf_mean_px": scale(["pf_small", 0], "area", 1, 1),
        "pf_mean": scale(["pf_mean_px", 0], "nearest-exact", small_w, small_h),
        "pf_0": paste(["pf_small", 0], ["pf_mean", 0], ["pf_mask", 0]),
    }
    for step in range(PREFILL_PASSES):
        nodes["pf_blur_%d" % step] = {"class_type": "ImageBlur", "inputs": {
            "image": ["pf_%d" % step, 0], "blur_radius": 6, "sigma": 3.0}}
        nodes["pf_%d" % (step + 1)] = paste(
            ["pf_small", 0], ["pf_blur_%d" % step, 0], ["pf_mask", 0])
    nodes["pf_full"] = scale(["pf_%d" % PREFILL_PASSES, 0], "bicubic",
                             work_w, work_h)
    nodes["hidden"] = paste(["img", 0], ["pf_full", 0], ["mask", 0])
    return nodes


def _inpaint_graph(url, model, image_name, mask_name, prompt,
                   work_w, work_h, out_w, out_h, see_through=False,
                   check=None, crop=None, grow=MASK_GROW):
    """Inpaint graph.

    Sampling is always restricted to the masked area with a latent noise
    mask, so the rest of the image is not repainted. What the model is
    shown of that area (its reference image) depends on the job:

    see_through=False  The area is hidden: pre-filled with the colors
        around it. An object to remove cannot be redrawn from the
        reference (FLUX.2 klein redraws whatever it can see), and a plain
        gray blank is avoided because on large holes the models return
        the blank untouched.
    see_through=True   The model sees the original pixels. For text or
        marks printed over a texture (halftone scans, paper, fabric): the
        real texture shows between the strokes and gets continued, where
        a hidden area comes back as a flat patch. Also for fills: the
        new content lands on the real background instead of a smooth
        box, and "erase the text" style prompts work.

    `check` adds the outputs is_flat ("flat") or is_unchanged ("changed")
    read. `crop` = (x, y, width, height) runs the model on that part of
    the image only (out_w/out_h are then the crop's size) and pastes the
    result back. `grow` is how far (px, work size) the paintable zone
    extends beyond the area (fills: FILL_GROW).
    """
    graph, model_ref, clip, vae = _model_nodes(url, model)
    graph.update({
        "img_in": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "img": {"class_type": "ImageScale", "inputs": {
            "image": ["img_crop", 0] if crop else ["img_in", 0],
            "upscale_method": "lanczos",
            "width": work_w, "height": work_h, "crop": "disabled"}},
        "mask_in": {"class_type": "LoadImage", "inputs": {"image": mask_name}},
        "mask_img": {"class_type": "ImageScale", "inputs": {
            "image": ["mask_crop", 0] if crop else ["mask_in", 0],
            "upscale_method": "bilinear",
            "width": work_w, "height": work_h, "crop": "disabled"}},
        "mask_raw": {"class_type": "ImageToMask",
                     "inputs": {"image": ["mask_img", 0], "channel": "red"}},
        "mask": {"class_type": "GrowMask", "inputs": {
            "mask": ["mask_raw", 0], "expand": grow,
            "tapered_corners": True}},
    })
    if crop:
        x, y, crop_w, crop_h = crop
        for key, source in (("img_crop", "img_in"), ("mask_crop", "mask_in")):
            graph[key] = {"class_type": "ImageCrop", "inputs": {
                "image": [source, 0], "width": crop_w, "height": crop_h,
                "x": x, "y": y}}
    if see_through:
        reference = ["img", 0]
    else:
        graph.update(_prefill_nodes(work_w, work_h))
        reference = ["hidden", 0]
    graph.update({
        "ref_latent": {"class_type": "VAEEncode",
                       "inputs": {"pixels": reference, "vae": vae}},
        "start": {"class_type": "SetLatentNoiseMask", "inputs": {
            "samples": ["ref_latent", 0], "mask": ["mask", 0]}},
    })
    if model == "qwen":
        for key, text in (("pos_txt", prompt), ("neg_txt", "")):
            graph[key] = {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {
                "clip": clip, "vae": vae, "image1": reference,
                "prompt": text}}
        for key, src in (("pos", "pos_txt"), ("neg", "neg_txt")):
            graph[key] = {"class_type": "FluxKontextMultiReferenceLatentMethod",
                          "inputs": {"conditioning": [src, 0],
                                     "reference_latents_method":
                                         "index_timestep_zero"}}
    else:
        graph["pos_txt"] = {"class_type": "CLIPTextEncode",
                            "inputs": {"clip": clip, "text": prompt}}
        graph["neg_txt"] = {"class_type": "ConditioningZeroOut",
                            "inputs": {"conditioning": ["pos_txt", 0]}}
        for key, src in (("pos", "pos_txt"), ("neg", "neg_txt")):
            graph[key] = {"class_type": "ReferenceLatent", "inputs": {
                "conditioning": [src, 0], "latent": ["ref_latent", 0]}}

    graph.update(_sampler_nodes(model, model_ref, ["pos", 0], ["neg", 0],
                                ["start", 0], work_w, work_h))
    graph.update({
        "decoded": {"class_type": "VAEDecode",
                    "inputs": {"samples": ["sampled", 0], "vae": vae}},
        # Outside the editable area keep the input pixels exactly.
        "merged": {"class_type": "ImageCompositeMasked", "inputs": {
            "destination": ["img", 0], "source": ["decoded", 0], "x": 0,
            "y": 0, "resize_source": False, "mask": ["mask", 0]}},
        "sized": {"class_type": "ImageScale", "inputs": {
            "image": ["merged", 0], "upscale_method": "lanczos",
            "width": out_w, "height": out_h, "crop": "disabled"}},
    })
    result = ["sized", 0]
    if crop:
        graph["placed"] = {"class_type": "ImageCompositeMasked", "inputs": {
            "destination": ["img_in", 0], "source": ["sized", 0],
            "x": crop[0], "y": crop[1], "resize_source": False}}
        result = ["placed", 0]
    # PreviewImage writes to ComfyUI's temp folder, not output/.
    graph["out"] = {"class_type": "PreviewImage", "inputs": {"images": result}}
    if check == "flat":
        graph.update(_flatness_nodes())
    elif check == "changed":
        graph.update(_change_nodes())
    return graph


def _edit_graph(url, model, image_name, prompt, work_w, work_h, out_w, out_h):
    """Whole-image edit graph (no mask): the model redraws the picture
    following `prompt`, seeing the original as its reference. Output node
    "out" is the result at out_w x out_h."""
    graph, model_ref, clip, vae = _model_nodes(url, model)
    graph.update({
        "img_in": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "img": {"class_type": "ImageScale", "inputs": {
            "image": ["img_in", 0], "upscale_method": "lanczos",
            "width": work_w, "height": work_h, "crop": "disabled"}},
        "ref_latent": {"class_type": "VAEEncode",
                       "inputs": {"pixels": ["img", 0], "vae": vae}},
    })
    if model == "qwen":
        for key, text in (("pos_txt", prompt), ("neg_txt", "")):
            graph[key] = {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {
                "clip": clip, "vae": vae, "image1": ["img", 0],
                "prompt": text}}
        for key, src in (("pos", "pos_txt"), ("neg", "neg_txt")):
            graph[key] = {"class_type": "FluxKontextMultiReferenceLatentMethod",
                          "inputs": {"conditioning": [src, 0],
                                     "reference_latents_method":
                                         "index_timestep_zero"}}
        latent = ["ref_latent", 0]
    else:
        graph["pos_txt"] = {"class_type": "CLIPTextEncode",
                            "inputs": {"clip": clip, "text": prompt}}
        graph["neg_txt"] = {"class_type": "ConditioningZeroOut",
                            "inputs": {"conditioning": ["pos_txt", 0]}}
        for key, src in (("pos", "pos_txt"), ("neg", "neg_txt")):
            graph[key] = {"class_type": "ReferenceLatent", "inputs": {
                "conditioning": [src, 0], "latent": ["ref_latent", 0]}}
        graph["empty"] = {"class_type": "EmptyFlux2LatentImage", "inputs": {
            "width": work_w, "height": work_h, "batch_size": 1}}
        latent = ["empty", 0]
    graph.update(_sampler_nodes(model, model_ref, ["pos", 0], ["neg", 0],
                                latent, work_w, work_h))
    graph.update({
        "decoded": {"class_type": "VAEDecode",
                    "inputs": {"samples": ["sampled", 0], "vae": vae}},
        "sized": {"class_type": "ImageScale", "inputs": {
            "image": ["decoded", 0], "upscale_method": "lanczos",
            "width": out_w, "height": out_h, "crop": "disabled"}},
        # PreviewImage writes to ComfyUI's temp folder, not output/.
        "out": {"class_type": "PreviewImage", "inputs": {"images": ["sized", 0]}},
    })
    return graph


# The checks below send back STATS_SIDE px maps next to the mask they
# were sampled through; is_flat / is_unchanged read their means.

def _stat_output(image, key):
    return {
        key + "_small": {"class_type": "ImageScale", "inputs": {
            "image": image, "upscale_method": "area", "width": STATS_SIDE,
            "height": STATS_SIDE, "crop": "disabled"}},
        key: {"class_type": "PreviewImage",
              "inputs": {"images": [key + "_small", 0]}},
    }


def _stat_common():
    return {
        "st_black": {"class_type": "ImageBlend", "inputs": {
            "image1": ["img", 0], "image2": ["img", 0], "blend_factor": 1.0,
            "blend_mode": "difference"}},
        "st_inside": {"class_type": "GrowMask", "inputs": {
            "mask": ["mask", 0], "expand": -MASK_GROW, "tapered_corners": True}},
        "st_inside_img": {"class_type": "MaskToImage",
                          "inputs": {"mask": ["st_inside", 0]}},
    }


def _stat_masked(image, mask, key):
    return {key: {"class_type": "ImageCompositeMasked", "inputs": {
        "destination": ["st_black", 0], "source": image, "x": 0, "y": 0,
        "resize_source": False, "mask": mask}}}


def _stat_difference(image1, image2, key):
    # ComfyUI's "difference" is image1 - image2 clamped at 0: one half.
    return {key: {"class_type": "ImageBlend", "inputs": {
        "image1": image1, "image2": image2, "blend_factor": 1.0,
        "blend_mode": "difference"}}}


def _flatness_nodes():
    """Maps to tell a flat fill from a detailed one (see is_flat).

    Fine detail = image minus its blur, sampled on the result inside the
    area and on the input in a ring around the area.
    """
    def detail(image, key):
        nodes = {key + "_blur": {"class_type": "ImageBlur", "inputs": {
            "image": image, "blur_radius": 3, "sigma": 2.0}}}
        nodes.update(_stat_difference(image, [key + "_blur", 0], key))
        return nodes

    nodes = _stat_common()
    nodes.update({
        "st_near": {"class_type": "GrowMask", "inputs": {
            "mask": ["mask", 0], "expand": 4, "tapered_corners": True}},
        "st_far": {"class_type": "GrowMask", "inputs": {
            "mask": ["mask", 0], "expand": RING_WIDTH, "tapered_corners": True}},
        "st_ring": {"class_type": "MaskComposite", "inputs": {
            "destination": ["st_far", 0], "source": ["st_near", 0], "x": 0,
            "y": 0, "operation": "subtract"}},
        "st_ring_img": {"class_type": "MaskToImage",
                        "inputs": {"mask": ["st_ring", 0]}},
    })
    nodes.update(detail(["merged", 0], "st_detail_out"))
    nodes.update(detail(["img", 0], "st_detail_in"))
    nodes.update(_stat_masked(["st_detail_out", 0], ["st_inside", 0], "st_a"))
    nodes.update(_stat_masked(["st_detail_in", 0], ["st_ring", 0], "st_b"))
    nodes.update(_stat_output(["st_a", 0], "stat_fill"))
    nodes.update(_stat_output(["st_inside_img", 0], "stat_fill_mask"))
    nodes.update(_stat_output(["st_b", 0], "stat_ring"))
    nodes.update(_stat_output(["st_ring_img", 0], "stat_ring_mask"))
    return nodes


def _change_nodes():
    """Maps of how much the area changed (see is_unchanged).

    Both images are blurred first so print grain and resampling noise do
    not count as change.
    """
    nodes = _stat_common()
    for key, image in (("st_soft_out", ["merged", 0]), ("st_soft_in", ["img", 0])):
        nodes[key] = {"class_type": "ImageBlur", "inputs": {
            "image": image, "blur_radius": 8, "sigma": 4.0}}
    nodes.update(_stat_difference(["st_soft_out", 0], ["st_soft_in", 0], "st_up"))
    nodes.update(_stat_difference(["st_soft_in", 0], ["st_soft_out", 0], "st_down"))
    nodes.update(_stat_masked(["st_up", 0], ["st_inside", 0], "st_up_in"))
    nodes.update(_stat_masked(["st_down", 0], ["st_inside", 0], "st_down_in"))
    nodes.update(_stat_output(["st_up_in", 0], "stat_up"))
    nodes.update(_stat_output(["st_down_in", 0], "stat_down"))
    nodes.update(_stat_output(["st_inside_img", 0], "stat_fill_mask"))
    return nodes


# --------------------------------------------------------------- run a graph

def _run(url, graph, progress=None, timeout=TIMEOUT):
    """Queue a graph, wait for it, return {output node: image bytes}."""
    client_id = uuid.uuid4().hex
    reply = json.loads(_post_json(url + "/prompt",
                                  {"prompt": graph, "client_id": client_id}))
    prompt_id = reply.get("prompt_id")
    # A graph whose outputs only partly validate is still queued, minus
    # the bad outputs: report that instead of waiting for a missing image.
    if reply.get("node_errors"):
        if prompt_id:
            cancel(url, prompt_id)
        raise ComfyUIError("ComfyUI rejected part of the job: %s"
                           % json.dumps(reply["node_errors"])[:600])
    if not prompt_id:
        raise ComfyUIError("ComfyUI rejected the job: %s"
                           % json.dumps(reply)[:600])

    started = time.time()
    while True:
        elapsed = time.time() - started
        if elapsed > timeout:
            cancel(url, prompt_id)
            raise ComfyUIError("ComfyUI did not finish within %d s." % timeout)
        if progress:
            try:
                progress(elapsed)
            except Exception:  # noqa: BLE001 - progress must never break a run
                pass
        time.sleep(POLL_INTERVAL)

        history = json.loads(_request("%s/history/%s" % (url, prompt_id)))
        entry = history.get(prompt_id)
        if not entry:
            continue
        status = entry.get("status", {})
        if status.get("status_str") == "error":
            raise ComfyUIError("ComfyUI run failed: %s"
                               % _error_text(status.get("messages")))
        images = {}
        for node, output in entry.get("outputs", {}).items():
            for image in output.get("images", [])[:1]:
                query = urllib.parse.urlencode({
                    "filename": image["filename"],
                    "subfolder": image.get("subfolder", ""),
                    "type": image.get("type", "temp")})
                images[node] = _request("%s/view?%s" % (url, query),
                                        timeout=120)
        if "out" in images:
            return images
        if status.get("completed"):
            raise ComfyUIError("ComfyUI finished without returning an image.")


def _error_text(messages):
    for name, data in messages or []:
        if name == "execution_error":
            return "%s: %s" % (data.get("node_type", "node"),
                               str(data.get("exception_message", "")).strip())
    return json.dumps(messages)[-400:]


def cancel(url, prompt_id=None):
    """Stop the running job (and drop `prompt_id` if it is still queued)."""
    try:
        if prompt_id:
            _post_json(url + "/queue", {"delete": [prompt_id]}, timeout=10)
        _post_json(url + "/interrupt", {}, timeout=10)
    except ComfyUIError:
        pass


# ------------------------------------------------------------- entry points

def png_size(png_bytes):
    if png_bytes[:8] != b"\x89PNG\r\n\x1a\n" or len(png_bytes) < 24:
        raise ComfyUIError("The image sent to ComfyUI is not a PNG.")
    return struct.unpack(">II", png_bytes[16:24])


def _png_rows(png_bytes):
    """Yield each row's first channel (bytes) of an 8-bit PNG."""
    width, height = png_size(png_bytes)
    color_type = png_bytes[25]
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(color_type)
    if png_bytes[24] != 8 or channels is None:
        raise ComfyUIError("Unexpected PNG format from ComfyUI.")
    idat, pos = b"", 8
    while pos + 8 <= len(png_bytes):
        length, kind = struct.unpack(">I4s", png_bytes[pos:pos + 8])
        if kind == b"IDAT":
            idat += png_bytes[pos + 8:pos + 8 + length]
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = width * channels
    previous = bytearray(stride)
    for row in range(height):
        start = row * (stride + 1)
        kind = raw[start]
        line = bytearray(raw[start + 1:start + 1 + stride])
        for i in range(stride if kind else 0):
            left = line[i - channels] if i >= channels else 0
            up = previous[i]
            up_left = previous[i - channels] if i >= channels else 0
            if kind == 1:
                line[i] = (line[i] + left) & 0xFF
            elif kind == 2:
                line[i] = (line[i] + up) & 0xFF
            elif kind == 3:
                line[i] = (line[i] + ((left + up) >> 1)) & 0xFF
            elif kind == 4:
                p = left + up - up_left
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - up_left)
                predictor = (left if pa <= pb and pa <= pc
                             else up if pb <= pc else up_left)
                line[i] = (line[i] + predictor) & 0xFF
        yield bytes(line[0::channels])
        previous = line


def _png_mean(png_bytes):
    """Mean of the first channel (0-255) of a small 8-bit PNG."""
    width, height = png_size(png_bytes)
    return sum(sum(row) for row in _png_rows(png_bytes)) / float(width * height)


def mask_box(mask_png, white=True):
    """(x, y, width, height) of the box around the mask's white area (the
    black area with white=False)."""
    left = top = None
    right = bottom = -1
    for y, row in enumerate(_png_rows(mask_png)):
        if (max(row) < 128) if white else (min(row) >= 128):
            continue
        lit = [x for x, value in enumerate(row) if (value >= 128) == white]
        left = lit[0] if left is None else min(left, lit[0])
        right = max(right, lit[-1])
        top = y if top is None else top
        bottom = y
    if left is None:
        return None
    return left, top, right - left + 1, bottom - top + 1


def fill_crop(mask_png, width, height):
    """The part of the image a fill is run on (see FILL_MARGIN), or None."""
    try:
        box = mask_box(mask_png)
    except (ComfyUIError, zlib.error, IndexError, struct.error):
        return None
    if not box:
        return None
    x, y, box_w, box_h = box
    margin_x = max(FILL_MARGIN_MIN, int(box_w * FILL_MARGIN))
    margin_y = max(FILL_MARGIN_MIN, int(box_h * FILL_MARGIN))
    left, top = max(0, x - margin_x), max(0, y - margin_y)
    right = min(width, x + box_w + margin_x)
    bottom = min(height, y + box_h + margin_y)
    if (right - left, bottom - top) == (width, height):
        return None
    return left, top, right - left, bottom - top


def outpaint_strips(mask_png, width, height):
    """Crops an outpaint runs on, one per side where the area reaches
    beyond the rest of the picture: the blank band plus as much picture
    next to it (at least OUTPAINT_CONTEXT px). [] when the area is inside
    the picture (a hole, not a border)."""
    try:
        box = mask_box(mask_png, white=False)
    except (ComfyUIError, zlib.error, IndexError, struct.error):
        return []
    if not box:
        return []
    x0, y0, known_w, known_h = box
    x1, y1 = x0 + known_w, y0 + known_h

    def depth(band):
        return band + max(band, OUTPAINT_CONTEXT)

    strips = []
    if y0 > 0:
        strips.append((0, 0, width, min(height, depth(y0))))
    if y1 < height:
        top = max(0, height - depth(height - y1))
        strips.append((0, top, width, height - top))
    if x0 > 0:
        strips.append((0, 0, min(width, depth(x0)), height))
    if x1 < width:
        left = max(0, width - depth(width - x1))
        strips.append((left, 0, width - left, height))
    return strips


def is_flat(images):
    """True when a hidden-area fill came back flat on a detailed background."""
    try:
        fill_mask = _png_mean(images["stat_fill_mask"])
        ring_mask = _png_mean(images["stat_ring_mask"])
        if fill_mask < 1.0 or ring_mask < 1.0:      # nothing to compare
            return False
        fill = _png_mean(images["stat_fill"]) * 255.0 / fill_mask
        ring = _png_mean(images["stat_ring"]) * 255.0 / ring_mask
    except (KeyError, ComfyUIError, zlib.error, IndexError, struct.error):
        return False
    # "difference" keeps only the positive half of the detail.
    return ring * 2.0 >= FLAT_MIN_DETAIL and fill < ring * FLAT_RATIO


def is_unchanged(images):
    """True when a see-through fill left the area (almost) as it was."""
    try:
        area = list(_png_rows(images["stat_fill_mask"]))
        up = list(_png_rows(images["stat_up"]))
        down = list(_png_rows(images["stat_down"]))
    except (KeyError, ComfyUIError, zlib.error, IndexError, struct.error):
        return False
    cells = changed = 0
    for area_row, up_row, down_row in zip(area, up, down):
        for coverage, rise, fall in zip(area_row, up_row, down_row):
            if coverage < 128:          # cell mostly outside the area
                continue
            cells += 1
            if (rise + fall) * 255.0 / coverage > CHANGED_LEVEL:
                changed += 1
    return cells > 0 and changed < cells * CHANGED_SHARE


def _round16(value):
    return max(MIN_SIDE, int(round(value / 16.0)) * 16)


def work_size(width, height):
    """Size the model works at: ~1 MP, multiples of 16, same aspect."""
    scale = min((WORK_PIXELS / float(width * height)) ** 0.5, MAX_UPSCALE)
    return _round16(width * scale), _round16(height * scale)


MODES = ("auto", "hidden", "see_through")


def inpaint(model, image_png, mask_png, prompt, url=None, progress=None,
            timeout=TIMEOUT, mode="auto"):
    """Fill the WHITE area of `mask_png` following `prompt` (None = remove).

    `mode` is how the model is shown the area (see _inpaint_graph):
    "hidden", "see_through", or "auto", which runs a second time the other
    way when the first result fails its check:
      removal  hidden first (an object must not be seen), see-through
               when the fill comes back flat on a detailed background;
      fill     see-through first (keeps the real background around the
               new content), hidden when the area comes back unchanged.
    Fills run on a tight crop around the area (see FILL_MARGIN), except
    a continuation prompt ("continue the image"), which is an outpaint:
    one hidden pass per side the area borders (see CONTINUE_WORDS).
    Returns PNG bytes of the image's size; pixels outside the mask are
    the input's own.
    """
    url = get_url(url)
    width, height = png_size(image_png)
    if mode not in MODES:
        raise ComfyUIError("Unknown inpaint mode: %s" % mode)
    prompt = (prompt or "").strip().rstrip(".")
    # Hidden: the prompt says what to paint. See-through: it is an edit
    # instruction for what the model sees ("erase the text").
    texts = {
        False: FILL_PROMPT.format(prompt=prompt) if prompt else REMOVE_PROMPT,
        True: EDIT_PROMPT.format(prompt=prompt) if prompt else CLEAN_PROMPT,
    }
    image_name = _upload(url, "image.png", image_png)
    mask_name = _upload(url, "mask.png", mask_png)

    if prompt and mode == "auto" and CONTINUE_WORDS.search(prompt):
        text = OUTPAINT_PROMPT.format(prompt=prompt)
        strips = outpaint_strips(mask_png, width, height) or [None]
        for number, crop in enumerate(strips):
            if number:   # the next side continues the sides done so far
                image_name = _upload(url, "image.png", image_png)
            out_w, out_h = (crop[2], crop[3]) if crop else (width, height)
            work_w, work_h = work_size(out_w, out_h)
            graph = _inpaint_graph(url, model, image_name, mask_name, text,
                                   work_w, work_h, out_w, out_h, crop=crop)
            image_png = _run(url, graph, progress, timeout)["out"]
        return image_png

    crop = fill_crop(mask_png, width, height) if prompt else None
    out_w, out_h = (crop[2], crop[3]) if crop else (width, height)
    work_w, work_h = work_size(out_w, out_h)
    grow = FILL_GROW if prompt else MASK_GROW

    def run(see_through, check=None):
        graph = _inpaint_graph(url, model, image_name, mask_name,
                               texts[see_through], work_w, work_h,
                               out_w, out_h, see_through, check, crop, grow)
        return _run(url, graph, progress, timeout)

    if mode != "auto":
        return run(mode == "see_through")["out"]
    if prompt:
        images = run(True, "changed")
        if is_unchanged(images):
            images = run(False)
    else:
        images = run(False, "flat")
        if is_flat(images):
            images = run(True)
    return images["out"]


def generate(prompt, width=1024, height=1024, url=None, progress=None,
             timeout=TIMEOUT):
    """Text-to-image (FLUX.2 klein). Returns PNG bytes."""
    url = get_url(url)
    graph = _generate_graph(url, prompt, _round16(width), _round16(height))
    return _run(url, graph, progress, timeout)["out"]


# SAM 2.1 (Segment Anything) through ComfyUI-segment-anything-2, for the
# Object Selection tool; local-ai-setup installs the node
# and the model (set "sam"), features/comfyui-nodes.sh gimp-setup's own
# GimpSetupBBox node.
SAM_MODEL = "sam2.1_hiera_large.safetensors"


def _require_nodes(url, *nodes):
    for node in nodes:
        try:
            info = json.loads(_request("%s/object_info/%s" % (url, node)))
        except ComfyUIError as e:
            if "HTTP 404" not in str(e):
                raise
            info = {}
        if node not in info:
            # gimp-setup's own node comes from its features/comfyui-nodes.sh;
            # ComfyUI and every other node from local-ai-setup
            if node == "GimpSetupBBox":
                where = "Re-run gimp-setup's ./setup.sh (it adds this node)"
            else:
                where = ("Re-run local-ai-setup "
                         "(github.com/diegochagas/local-ai-setup installs it)")
            raise ComfyUIError(
                "ComfyUI has no %s node. %s, then restart ComfyUI (close "
                "and reopen GIMP)." % (node, where))


def segment(image_png, boxes, url=None, progress=None, timeout=TIMEOUT):
    """Object mask for the object inside each [x1, y1, x2, y2] box (image
    pixels), with SAM 2.1. Returns a grayscale PNG the size of the image:
    white = object."""
    url = get_url(url)
    _require_nodes(url, "Sam2Segmentation", "GimpSetupBBox")
    name = _upload(url, "object-select.png", image_png)
    graph = {
        "img": {"class_type": "LoadImage", "inputs": {"image": name}},
        "box": {"class_type": "GimpSetupBBox",
                "inputs": {"boxes": json.dumps([[round(v) for v in b] for b in boxes])}},
        "sam": {"class_type": "DownloadAndLoadSAM2Model",
                "inputs": {"model": SAM_MODEL, "segmentor": "single_image",
                           "device": "cuda", "precision": "fp16"}},
        "seg": {"class_type": "Sam2Segmentation",
                "inputs": {"sam2_model": ["sam", 0], "image": ["img", 0],
                           "keep_model_loaded": True, "bboxes": ["box", 0]}},
        "m2i": {"class_type": "MaskToImage", "inputs": {"mask": ["seg", 0]}},
        "out": {"class_type": "PreviewImage", "inputs": {"images": ["m2i", 0]}},
    }
    return _run(url, graph, progress, timeout)["out"]


def restore(model, image_png, burns=False, url=None, progress=None,
            timeout=TIMEOUT):
    """Repair a scanned photo print: the whole picture is redrawn with the
    damage (blotches, scratches, stains; chemical burns with `burns`)
    painted over. Returns PNG bytes of the image's size. The caller keeps
    only the pixels that changed (the damage) — see ai-restore-photo."""
    url = get_url(url)
    width, height = png_size(image_png)
    image_name = _upload(url, "restore.png", image_png)
    work_w, work_h = work_size(width, height)
    prompt = BURN_PROMPT if burns else RESTORE_PROMPT
    graph = _edit_graph(url, model, image_name, prompt, work_w, work_h,
                        width, height)
    return _run(url, graph, progress, timeout)["out"]


def _main(argv):
    def show(elapsed):
        sys.stderr.write("\r%4d s" % elapsed)
        sys.stderr.flush()

    if len(argv) >= 6 and argv[1] in ("fill", "remove", "hide", "clean"):
        with open(argv[3], "rb") as f:
            image = f.read()
        with open(argv[4], "rb") as f:
            mask = f.read()
        prompt = argv[6] if argv[1] == "fill" and len(argv) > 6 else None
        mode = {"hide": "hidden", "clean": "see_through"}.get(argv[1], "auto")
        data = inpaint(argv[2], image, mask, prompt, progress=show, mode=mode)
        out = argv[5]
    elif len(argv) >= 5 and argv[1] == "restore":
        with open(argv[3], "rb") as f:
            image = f.read()
        burns = len(argv) > 5 and argv[5] == "burns"
        data = restore(argv[2], image, burns, progress=show)
        out = argv[4]
    elif len(argv) >= 4 and argv[1] == "generate":
        width, height = 1024, 1024
        if len(argv) > 4:
            width, height = (int(v) for v in argv[4].lower().split("x"))
        data = generate(argv[3], width, height, progress=show)
        out = argv[2]
    else:
        sys.stderr.write(__doc__)
        return 2
    with open(out, "wb") as f:
        f.write(data)
    sys.stderr.write("\nsaved %s\n" % out)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
