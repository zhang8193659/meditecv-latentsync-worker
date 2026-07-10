#!/usr/bin/env python3
# =====================================================================================
# download_models.py -- bake LatentSync weights into the image at BUILD time.
#
# Why this exists (the i2v-worker lesson): an earlier worker guessed HuggingFace subpaths
# in the Dockerfile, hit 404s, and the RunPod build hung for 5 days. This script NEVER
# guesses. It:
#   1. asks HuggingFace for the REAL file list via HfApi().list_repo_files(repo),
#   2. matches the files it wants against that real list,
#   3. prints a HIT/MISS line for every candidate (visible in the RunPod build log),
#   4. exits NONZERO if any inference-critical file is missing, so the build fails LOUD.
#
# Repos (verified PUBLIC -- gated=false / private=false -- so NO HF token is needed):
#   * ByteDance/LatentSync-1.6   -> latentsync_unet.pt (~5GB), whisper/tiny.pt,
#                                   stable_syncnet.pt (~1.6GB), auxiliary/*, config.json
#   * stabilityai/sd-vae-ft-mse  -> the SD VAE LatentSync decodes with (README-cited)
#
# Placement (belt-and-suspenders; the wrapper's code paths disagree, so we satisfy both):
#   * <node>/checkpoints/...                    -- where nodes.py loads unet+whisper at inference
#     (os.path.join(cur_dir, "checkpoints", "latentsync_unet.pt") etc.)
#   * ~/.latentsync16_models/...                -- where nodes.py setup_models() snapshot_downloads
#     unet+whisper to; pre-populating it makes the runtime call a no-op (no 7GB cold-start pull)
#   * HuggingFace cache (HF_HOME)               -- so any from_pretrained("stabilityai/sd-vae-ft-mse")
#     inside LatentSync's bundled inference module hits cache instead of the network
#
# If a future upstream renames files, the MISS lines + nonzero exit tell you exactly which
# candidate to fix -- change one string here and re-push, same as the i2v fix flow.
# =====================================================================================
import os
import shutil
import sys
import time
import traceback

from huggingface_hub import HfApi, hf_hub_download, snapshot_download

# --- Target locations -----------------------------------------------------------------
NODE_DIR = "/comfyui/custom_nodes/ComfyUI-LatentSyncWrapper"
CKPT_DIR = os.path.join(NODE_DIR, "checkpoints")            # inference reads from here
VAE_DIR = os.path.join(CKPT_DIR, "vae")                     # README-documented VAE slot
PERSIST_DIR = os.path.join(os.path.expanduser("~"), ".latentsync16_models")  # runtime auto-dl target

LATENTSYNC_REPO = "ByteDance/LatentSync-1.6"
VAE_REPO = "stabilityai/sd-vae-ft-mse"

# Files that MUST exist for inference. If any is absent from the real repo listing (or fails
# to download), the build must fail -- a lipsync worker without these is a broken shell.
LATENTSYNC_REQUIRED = ["latentsync_unet.pt", "whisper/tiny.pt"]
# Everything else in the repo is baked too (harmless; syncnet/auxiliary are train-time only),
# but their absence only WARNs -- they are not needed to run inference.
LATENTSYNC_SKIP = {".gitattributes", ".gitignore", "README.md"}

VAE_REQUIRED_ANY_WEIGHT = ("diffusion_pytorch_model.safetensors", "diffusion_pytorch_model.bin")
VAE_REQUIRED_CONFIG = "config.json"

# Face detector (s3fd): NOT in the LatentSync repo. The node hardcodes this SadTalker URL in
# pre_download_models() and at runtime reads it from ~/.latentsync16_models/s3fd-e19a316812.pth
# (the repo file is named ...619a... but the node saves/reads it as ...e19a... -- that rename is
# the node's own behavior, verified in nodes.py @ pinned commit 360d5283). Bake it so cold start
# needs no network and never silently loses face detection.
FACE_REPO = "vinthony/SadTalker"
FACE_SRC = "hub/checkpoints/s3fd-619a316812.pth"
FACE_DST_NAME = "s3fd-e19a316812.pth"

_api = HfApi()


def log(msg: str) -> None:
    print(f"[download_models] {msg}", flush=True)


def list_files(repo: str) -> list:
    """Real file list from HF (no token; public repos). Retried -- HF list can flake."""
    last = None
    for attempt in range(1, 5):
        try:
            files = _api.list_repo_files(repo_id=repo)
            log(f"repo {repo}: list_repo_files returned {len(files)} entries")
            return list(files)
        except Exception as e:  # noqa: BLE001
            last = e
            log(f"repo {repo}: list_repo_files attempt {attempt} failed: {e}")
            time.sleep(4 * attempt)
    raise RuntimeError(f"could not list files for {repo}: {last}")


def download(repo: str, rel_path: str, local_dir: str) -> str:
    """Download one real repo file into local_dir, preserving its subfolder. Retried."""
    last = None
    for attempt in range(1, 5):
        try:
            p = hf_hub_download(repo_id=repo, filename=rel_path, local_dir=local_dir)
            size = os.path.getsize(p) if os.path.exists(p) else -1
            log(f"  HIT  {repo}::{rel_path}  ->  {p}  ({size/1e6:.1f} MB)")
            return p
        except Exception as e:  # noqa: BLE001
            last = e
            log(f"  ...  {repo}::{rel_path} attempt {attempt} failed: {e}")
            time.sleep(4 * attempt)
    raise RuntimeError(f"download failed {repo}::{rel_path}: {last}")


def bake_latentsync() -> None:
    log(f"=== {LATENTSYNC_REPO} ===")
    files = list_files(LATENTSYNC_REPO)

    # Fail fast + LOUD if a required file is not even present in the real listing.
    missing_required = [r for r in LATENTSYNC_REQUIRED if r not in files]
    if missing_required:
        log(f"  MISS (required, not in repo listing): {missing_required}")
        log(f"  real repo listing was: {sorted(files)}")
        sys.exit(1)

    wanted = [f for f in files if f not in LATENTSYNC_SKIP]
    os.makedirs(CKPT_DIR, exist_ok=True)
    os.makedirs(PERSIST_DIR, exist_ok=True)

    for rel in wanted:
        try:
            download(LATENTSYNC_REPO, rel, CKPT_DIR)
        except Exception as e:  # noqa: BLE001
            if rel in LATENTSYNC_REQUIRED:
                log(f"  FATAL: required file {rel} failed to download: {e}")
                sys.exit(1)
            log(f"  WARN (optional): {rel} failed to download: {e}")

    # Mirror the two inference-critical files into the runtime auto-download dir so the
    # wrapper's snapshot_download(local_dir=~/.latentsync16_models) finds them already present.
    for rel in LATENTSYNC_REQUIRED:
        try:
            download(LATENTSYNC_REPO, rel, PERSIST_DIR)
        except Exception as e:  # noqa: BLE001
            log(f"  WARN: mirroring {rel} to {PERSIST_DIR} failed (runtime may re-pull): {e}")

    # Verify the critical files actually landed on disk where inference reads them.
    for rel in LATENTSYNC_REQUIRED:
        dst = os.path.join(CKPT_DIR, rel)
        if not os.path.exists(dst):
            log(f"  FATAL: expected {dst} on disk after download but it is missing")
            sys.exit(1)
    log(f"  OK: LatentSync critical weights present under {CKPT_DIR}")


def bake_vae() -> None:
    log(f"=== {VAE_REPO} ===")
    files = list_files(VAE_REPO)

    # pick the config + one weight file from the REAL listing (prefer safetensors)
    weight = next((w for w in VAE_REQUIRED_ANY_WEIGHT if w in files), None)
    if weight is None or VAE_REQUIRED_CONFIG not in files:
        log(f"  MISS: could not find {VAE_REQUIRED_CONFIG} + a weight among {VAE_REQUIRED_ANY_WEIGHT}")
        log(f"  real repo listing was: {sorted(files)}")
        sys.exit(1)

    os.makedirs(VAE_DIR, exist_ok=True)
    # (a) local copy in the README-documented checkpoints/vae/ slot
    for rel in (VAE_REQUIRED_CONFIG, weight):
        try:
            download(VAE_REPO, rel, VAE_DIR)
        except Exception as e:  # noqa: BLE001
            log(f"  FATAL: VAE file {rel} failed to download: {e}")
            sys.exit(1)

    # (b) warm the HF cache so from_pretrained("stabilityai/sd-vae-ft-mse") resolves offline-ish
    #     (a quick etag check at runtime, not a re-download).
    last = None
    for attempt in range(1, 5):
        try:
            path = snapshot_download(
                repo_id=VAE_REPO,
                allow_patterns=[VAE_REQUIRED_CONFIG, weight, "*.json"],
            )
            log(f"  HIT  {VAE_REPO} warmed into HF cache -> {path}")
            break
        except Exception as e:  # noqa: BLE001
            last = e
            log(f"  ...  snapshot_download {VAE_REPO} attempt {attempt} failed: {e}")
            time.sleep(4 * attempt)
    else:
        log(f"  WARN: could not warm HF cache for {VAE_REPO}: {last} "
            f"(from_pretrained may hit the network at cold start)")
    log(f"  OK: VAE baked under {VAE_DIR} (+ HF cache best-effort)")


def bake_face_detector() -> None:
    log(f"=== {FACE_REPO} (s3fd face detector, node hardcodes this) ===")
    os.makedirs(PERSIST_DIR, exist_ok=True)
    dst = os.path.join(PERSIST_DIR, FACE_DST_NAME)
    last = None
    for attempt in range(1, 5):
        try:
            src = hf_hub_download(repo_id=FACE_REPO, filename=FACE_SRC)
            shutil.copy(src, dst)
            size = os.path.getsize(dst) if os.path.exists(dst) else -1
            log(f"  HIT  {FACE_REPO}::{FACE_SRC}  ->  {dst}  ({size/1e6:.1f} MB)")
            return
        except Exception as e:  # noqa: BLE001
            last = e
            log(f"  ...  {FACE_REPO}::{FACE_SRC} attempt {attempt} failed: {e}")
            time.sleep(4 * attempt)
    log(f"  FATAL: could not bake s3fd face detector (node needs it at runtime): {last}")
    sys.exit(1)


def main() -> None:
    log(f"HOME={os.path.expanduser('~')}  CKPT_DIR={CKPT_DIR}  PERSIST_DIR={PERSIST_DIR}")
    if not os.path.isdir(NODE_DIR):
        log(f"FATAL: node dir {NODE_DIR} not found -- did the LatentSyncWrapper clone step run?")
        sys.exit(1)
    try:
        bake_latentsync()
        bake_vae()
        bake_face_detector()
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        log("FATAL: unexpected error while baking weights:")
        traceback.print_exc()
        sys.exit(1)
    log("ALL DONE: weights baked. If the build reached here, no required file was missing.")


if __name__ == "__main__":
    main()
