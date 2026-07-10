#!/usr/bin/env python3
# =====================================================================================
# patch_handler.py -- make worker-comfyui return VHS video outputs as base64.
#
# The stock worker-comfyui 5.8.6 handler collects only `node_output["images"]`. But
# VHS_VideoCombine writes its produced mp4 under `node_output["gifs"]` (and some video
# nodes use "videos"). Without this patch the /run response has an EMPTY output.images
# and the mEditEcv provider (_extract_video) finds no video -> "completed but no video".
#
# We do THREE surgical, whitespace-agnostic substring replacements on the stock handler:
#   1. the guard      : if "images" in node_output:      -> also true for gifs/videos
#   2. the iterator    : for image_info in node_output["images"]:  -> images + gifs + videos
#   3. the count print : len(node_output['images'])       -> len over the merged list
# The per-file body below the loop already uses image_info.get(...) and works unchanged
# for VHS items (they carry the same filename/subfolder/type keys, type=="output").
#
# The exact anchor strings were copied verbatim from
#   github.com/runpod-workers/worker-comfyui  handler.py @ tag 5.8.6
# so this patch is pinned to that base image (see Dockerfile ARG WORKER_COMFYUI_VERSION).
#
# Fail-loud: if an anchor is not found (base image changed), we exit NONZERO so the build
# fails visibly instead of silently shipping a worker that drops every video. Idempotent:
# re-running on an already-patched file is a no-op.
# =====================================================================================
import os
import sys

CANDIDATES = ["/handler.py", "/comfyui/handler.py"]

# (old, new, expected_min_count) -- old must be present at least expected_min_count times.
REPLACEMENTS = [
    (
        'if "images" in node_output:',
        'if ("images" in node_output) or ("gifs" in node_output) or ("videos" in node_output):',
        1,
    ),
    (
        'for image_info in node_output["images"]:',
        'for image_info in (node_output.get("images", []) '
        '+ node_output.get("gifs", []) + node_output.get("videos", [])):',
        1,
    ),
    (
        "{len(node_output['images'])}",
        "{len(node_output.get('images', []) + node_output.get('gifs', []) "
        "+ node_output.get('videos', []))}",
        1,
    ),
]

# Marker proving a previous run already patched this file.
ALREADY_PATCHED = 'node_output.get("gifs", [])'


def log(msg: str) -> None:
    print(f"[patch_handler] {msg}", flush=True)


def find_handler() -> str:
    # 1) known copy locations
    for c in CANDIDATES:
        if os.path.isfile(c):
            try:
                txt = open(c, encoding="utf-8").read()
            except Exception:  # noqa: BLE001
                continue
            if "for node_id, node_output in outputs.items():" in txt or ALREADY_PATCHED in txt:
                return c
    # 2) shallow walk of likely roots as a fallback
    for root in ("/comfyui", "/", "/opt"):
        for dirpath, _dirs, files in os.walk(root):
            # keep the walk cheap: don't descend into big model/venv trees
            depth = dirpath.count(os.sep)
            if depth > 4:
                _dirs[:] = []
                continue
            if "handler.py" in files:
                p = os.path.join(dirpath, "handler.py")
                try:
                    txt = open(p, encoding="utf-8").read()
                except Exception:  # noqa: BLE001
                    continue
                if "for node_id, node_output in outputs.items():" in txt or ALREADY_PATCHED in txt:
                    return p
        # stop the "/" walk early once /comfyui is exhausted to avoid scanning everything
        if root == "/comfyui":
            continue
    return ""


def main() -> None:
    path = find_handler()
    if not path:
        log("FATAL: could not locate a worker-comfyui handler.py containing the output loop.")
        log(f"       looked at {CANDIDATES} and shallow-walked /comfyui, /, /opt")
        sys.exit(1)
    log(f"patching handler at: {path}")

    src = open(path, encoding="utf-8").read()

    if ALREADY_PATCHED in src:
        log("handler already patched (gifs/videos collection present) -- nothing to do.")
        return

    for old, new, expected in REPLACEMENTS:
        count = src.count(old)
        if count < expected:
            log(f"FATAL: anchor not found (base image changed?):\n    {old!r}")
            log("       Re-copy the verbatim anchors from the pinned handler.py and update REPLACEMENTS.")
            sys.exit(1)
        src = src.replace(old, new)
        log(f"  applied ({count}x): {old!r}")

    # sanity: the merged collection must now be present
    if ALREADY_PATCHED not in src:
        log("FATAL: post-patch verification failed (merged gifs collection not present).")
        sys.exit(1)

    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    log("OK: handler patched -- VHS_VideoCombine mp4 (gifs/videos) now returned as base64 in output.images.")


if __name__ == "__main__":
    main()
