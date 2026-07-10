# =====================================================================================
# meditecv-latentsync-worker — RunPod worker-comfyui image for LatentSync video-to-video
# re-lipsync (mEditEcv L2).  Given an I2V shot video (frames) + a voiceover audio, re-synthesize
# the mouth region so lips match the audio -> return a talking-head mp4.
#
# Worker contract (same as the sibling i2v worker):
#   request : {"input":{"workflow":<comfy API prompt>,"images":[{name,image(base64)}...]}}
#   response: COMPLETED -> output.images[]  (VHS_VideoCombine mp4 as base64; see patch_handler.py)
#
# RunPod GitHub-build note: this Dockerfile uses ONLY FROM / RUN / COPY (no ARG / USER / WORKDIR).
# RunPod's build validator rejected earlier versions that used a build-arg-templated FROM and
# USER/WORKDIR directives with "Invalid Dockerfile configuration". The prior COMPLETED build used
# exactly this plain FROM+RUN+COPY shape, so we mirror it. All node/weight logic lives inside RUN
# (opaque to the validator).  NOT GPU/RunPod-validated end to end — iterate from the build log.
#   base: runpod/worker-comfyui:5.8.6-base | nodes: LatentSyncWrapper + VideoHelperSuite
#   weights (PUBLIC): ByteDance/LatentSync-1.6 + stabilityai/sd-vae-ft-mse + s3fd (vinthony/SadTalker)
# =====================================================================================
FROM runpod/worker-comfyui:5.8.6-base

# System libs: ffmpeg is a hard requirement of LatentSync + VHS; libgl1/libglib2.0-0 for
# opencv/mediapipe; libsndfile1 for soundfile.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg git libgl1 libglib2.0-0 libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# VideoHelperSuite: VHS_LoadVideo (source video -> IMAGE frame batch + AUDIO) and
# VHS_VideoCombine (frames + audio -> mp4). Pinned by commit for reproducibility.
RUN cd /comfyui/custom_nodes && \
    git clone https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git && \
    cd ComfyUI-VideoHelperSuite && \
    git checkout 4ee72c065db22c9d96c2427954dc69e7b908444b && \
    pip install --no-cache-dir -r requirements.txt

# LatentSyncWrapper: LatentSyncNode + VideoLengthAdjuster. `decord` has no Ubuntu-24.04/py3.12
# wheel -> drop it from requirements and install the drop-in fork `eva-decord` (provides
# `import decord`).
RUN cd /comfyui/custom_nodes && \
    git clone https://github.com/ShmuelRonen/ComfyUI-LatentSyncWrapper.git && \
    cd ComfyUI-LatentSyncWrapper && \
    git checkout 360d5283d7276aee68b4237b1387e594e4ce640e && \
    sed -i '/^decord/d;/^ *decord/d' requirements.txt && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir eva-decord

# torchcodec: needed at runtime by LatentSync ("TorchCodec is required for save_with_torchcodec"),
# NOT in the node's requirements.txt. Hard rule: it must NOT change torch — the base's torch is a
# CUDA build; any torch swap breaks ComfyUI startup and the serverless worker gets stuck
# "initializing" (invisible, no logs). So we:
#   (a) pin torch to the EXACT installed version via a constraints file, so pip selects a torchcodec
#       compatible with it and NEVER touches torch (if none is compatible, the build fails here);
#   (b) verify torchcodec actually IMPORTS at build time -> a broken torchcodec fails the BUILD
#       (visible via GitBuild.state=FAILED) instead of silently crash-looping worker startup.
RUN python -c "import torch;print('torch=='+torch.__version__.split('+')[0])" > /tmp/torch-constraint.txt && \
    echo "pinning torch: $(cat /tmp/torch-constraint.txt)" && \
    pip install --no-cache-dir -c /tmp/torch-constraint.txt torchcodec && \
    python -c "import torch, torchcodec; print('IMPORT OK: torch', torch.__version__, '| torchcodec', torchcodec.__version__)"

# The wrapper checks ~/.latentsync16_dependencies_installed at first import and, if absent, runs
# `pip install` at RUNTIME (bad in serverless). Deps are installed above at build time, so set the
# flag to skip that runtime path. (Verified in nodes.py @ pinned commit 360d5283.)
RUN touch /root/.latentsync16_dependencies_installed

# Teach the stock worker-comfyui handler to also return VHS video outputs (gifs/videos) as base64;
# otherwise the lipsync mp4 (written under node_output["gifs"]) is dropped. Fails the build loudly
# if the handler's anchors are not found (base image changed).
COPY patch_handler.py /patch_handler.py
RUN python /patch_handler.py

# Reference workflow (informational; the real prompt is sent per-request inside /run).
COPY workflows/latentsync_lipsync.json /workflows/latentsync_lipsync.json

# Bake model weights LAST (biggest layer). download_models.py enumerates the REAL repo file list via
# HfApi().list_repo_files (never guesses subpaths), prints HIT/MISS per file, and exits nonzero if
# any inference-critical file is missing. Repos are PUBLIC -> no HF token. Also bakes the s3fd face
# detector to ~/.latentsync16_models/s3fd-e19a316812.pth (the node hardcodes that path/name).
COPY download_models.py /download_models.py
RUN python /download_models.py
