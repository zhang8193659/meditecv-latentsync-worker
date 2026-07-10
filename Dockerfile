# meditecv-latentsync-worker
# =====================================================================================
# RunPod worker-comfyui image for LatentSync video-to-video re-lipsync (mEditEcv L2).
#
# Capability: given an I2V-generated shot video (frames) + a voiceover audio,
#   re-synthesize the mouth region so lips match the audio -> return a talking-head mp4.
#
# Contract (identical to the sibling i2v worker):
#   request : {"input":{"workflow":<comfy API prompt>,"images":[{name,image(base64)}...]}}
#             worker writes each base64 blob into ComfyUI input/ under `name`; the workflow
#             nodes reference those filenames (video + audio both ride the images[] channel).
#   response: COMPLETED -> output.images[] ; VHS_VideoCombine's mp4 is base64 in there
#             (see patch_handler.py, which teaches the stock handler to also emit gifs/videos).
#
# All facts below were verified from source (see README "Verified facts / sources"):
#   - base image           : runpod/worker-comfyui:5.8.6-base  (latest release 2026-06-17)
#   - ComfyUI root          : /comfyui   (WORKDIR)   ;  venv: /opt/venv (already on PATH)
#   - custom node CLI        : comfy-node-install (on PATH)  -- we git-clone+pin instead for reproducibility
#   - LatentSync nodes       : ShmuelRonen/ComfyUI-LatentSyncWrapper  (LatentSyncNode, VideoLengthAdjuster)
#   - video load/combine     : Kosinkadink/ComfyUI-VideoHelperSuite   (VHS_LoadVideo, VHS_VideoCombine)
#   - weights (PUBLIC, no HF token needed): ByteDance/LatentSync-1.6  +  stabilityai/sd-vae-ft-mse
#
# NOTE: This image has NOT been GPU/RunPod-validated. It is push-ready; iterate from the
#       RunPod build log (weight HIT/MISS lines + node install output). See README + handoff.
# =====================================================================================

# FROM tag is HARDCODED (not a build ARG). RunPod's GitHub-build validator statically parses the
# Dockerfile before building and rejects a build-arg-templated FROM with "Invalid Dockerfile
# configuration" (it can't resolve ${...} at parse time). The prior working build used this exact
# concrete tag. Custom-node commits are likewise inlined below (no ARG) to keep the file plain.
FROM runpod/worker-comfyui:5.8.6-base

USER root

# --- System libraries -----------------------------------------------------------------
# ffmpeg is a hard requirement of LatentSync (must be on PATH) and VHS.
# libgl1 / libglib2.0-0 : opencv + mediapipe runtime libs (Ubuntu 24.04 uses libgl1, not -mesa-glx).
# libsndfile1           : soundfile.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        git \
        libgl1 \
        libglib2.0-0 \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# --- Custom nodes ---------------------------------------------------------------------
WORKDIR /comfyui/custom_nodes

# VideoHelperSuite: supplies VHS_LoadVideo (source video -> IMAGE frame batch + AUDIO)
# and VHS_VideoCombine (frames + audio -> mp4). Both are required by the lipsync graph.
RUN git clone https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git && \
    cd ComfyUI-VideoHelperSuite && \
    git checkout 4ee72c065db22c9d96c2427954dc69e7b908444b && \
    { python -m pip install --no-cache-dir -r requirements.txt || uv pip install --no-cache-dir -r requirements.txt; }

# ComfyUI-LatentSyncWrapper: supplies LatentSyncNode + VideoLengthAdjuster.
# Known build friction handled inline:
#   * `decord` (in its requirements) has no reliable wheel on Ubuntu 24.04 / py3.12 ->
#     drop it and install the drop-in fork `eva-decord`, which provides `import decord`.
#     If the upstream `decord` ever ships a working wheel you may delete the sed + eva line.
RUN git clone https://github.com/ShmuelRonen/ComfyUI-LatentSyncWrapper.git && \
    cd ComfyUI-LatentSyncWrapper && \
    git checkout 360d5283d7276aee68b4237b1387e594e4ce640e && \
    sed -i '/^decord/d;/^ *decord/d' requirements.txt && \
    { python -m pip install --no-cache-dir -r requirements.txt || uv pip install --no-cache-dir -r requirements.txt; } && \
    { python -m pip install --no-cache-dir eva-decord || uv pip install --no-cache-dir eva-decord; }

# The wrapper checks ~/.latentsync16_dependencies_installed at first import and, if ABSENT, runs
# `pip install` at RUNTIME (bad in serverless: slow first request + offline-fragile). All Python
# deps are installed above at build time, so set the flag to skip that runtime path.
# (Verified in ComfyUI-LatentSyncWrapper nodes.py @ pinned commit 360d5283.)
RUN touch /root/.latentsync16_dependencies_installed

# --- Teach the stock handler to return VHS video outputs as base64 --------------------
# The stock worker-comfyui handler only collects node_output["images"]; VHS_VideoCombine
# writes its mp4 under node_output["gifs"]. patch_handler.py surgically extends the
# collector to also walk "gifs"/"videos". It FAILS the build (nonzero exit) if it cannot
# find/patch the handler, so a base-image change is loud rather than silently dropping video.
COPY patch_handler.py /patch_handler.py
RUN python /patch_handler.py

# --- Reference workflow (informational; the real prompt is sent per-request) ----------
# The authoritative workflow is proj/backend/.../comfyui_workflows/latentsync_lipsync.json,
# sent inside each /run payload. This copy documents the graph the image is built to satisfy.
COPY workflows/latentsync_lipsync.json /workflows/latentsync_lipsync.json

# --- Bake model weights (LAST: biggest layer, so node/patch edits don't re-pull ~7GB) -
# download_models.py enumerates the REAL repo file list via HfApi().list_repo_files (never
# guesses subpaths), matches by filename, prints HIT/MISS per file, and exits nonzero if any
# inference-critical file is missing. Repos are PUBLIC (gated=false) -> no HF token required.
COPY download_models.py /download_models.py
RUN python /download_models.py

# Reset workdir for the base image's entrypoint (CMD ["/start.sh"] is inherited).
WORKDIR /comfyui
