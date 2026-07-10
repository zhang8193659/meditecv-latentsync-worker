# meditecv-latentsync-worker

RunPod **worker-comfyui** image that gives mEditEcv its **video-to-video re-lipsync** (L2
"真人出镜口播") capability: feed an **I2V-generated shot video + a voiceover audio**, get back
the same shot with the **mouth re-synthesized to match the audio** (a talking-head mp4).

Model: **LatentSync-1.6** (ByteDance) via the `ComfyUI-LatentSyncWrapper` custom node, plus
`ComfyUI-VideoHelperSuite` for video load/combine. It is the sibling of the already-built
`meditecv-i2v-worker` and speaks the **exact same worker contract**, so the mEditEcv provider
(`runpod_serverless_digitalhuman_provider.py`) drives it unchanged.

> ⚠️ **NOT YET GPU/RunPod-VALIDATED.** I (the dev agent) have no GPU and cannot push to your
> GitHub or trigger a RunPod build. This repo is **push-ready**; you finish it by pushing (see
> [`给同事解锁-latentsync-worker.md`](给同事解锁-latentsync-worker.md)) and then iterating from
> the RunPod **build log** (the weight HIT/MISS lines and node-install output tell you exactly
> what, if anything, to nudge). This mirrors how the i2v worker was brought up.

---

## Worker contract (identical to the i2v worker)

- **Request:** `POST https://api.runpod.ai/v2/{endpoint}/run`
  ```json
  {"input":{"workflow":<ComfyUI API-format prompt>,"images":[{"name":"...","image":"<base64>"}]}}
  ```
  The worker writes each base64 blob into ComfyUI `input/` under its `name`; workflow nodes
  reference those filenames. **Both the video and the audio ride the `images[]` channel** (the
  provider does not distinguish — the worker just drops bytes into `input/`).
- **Response:** `GET /status/{id}` → on `COMPLETED`, the mp4 comes back in `output.images[]`
  as `{"filename","type":"base64","data":<base64 mp4>}`. VHS_VideoCombine normally writes its
  file under `gifs`, which the stock handler ignores — [`patch_handler.py`](patch_handler.py)
  teaches the handler to also emit `gifs`/`videos` as base64, so the provider's `_extract_video`
  finds the mp4.

---

## Verified facts / sources (checked from source, not memory)

| Thing | Value | Source |
|---|---|---|
| Base image | `runpod/worker-comfyui:5.8.6-base` (latest release, 2026-06-17; `-base` = clean ComfyUI + comfy-cli, no models) | github.com/runpod-workers/worker-comfyui (README + releases/latest) |
| Base OS / CUDA / paths | `nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04`; ComfyUI root & WORKDIR `/comfyui`; venv `/opt/venv` on PATH; `comfy-node-install` on PATH | worker-comfyui `Dockerfile` @ tag 5.8.6 |
| Custom-node install (docs) | `RUN comfy-node-install <registry-name>` (we git-clone+pin instead for reproducibility) | worker-comfyui `docs/customization.md` |
| Handler output collection | stock loops `node_output["images"]` only → `output_data`, base64 unless `BUCKET_ENDPOINT_URL` set; VHS writes to `gifs` | worker-comfyui `handler.py` @ tag 5.8.6 |
| LatentSync node | `ComfyUI-LatentSyncWrapper`, pinned `360d5283…` (2025-09-04). Nodes: **`LatentSyncNode`** (images/audio/seed/lips_expression/inference_steps), **`VideoLengthAdjuster`** (images/audio/mode∈{normal,pingpong,loop_to_audio}/fps/silent_padding_sec). Both take an **IMAGE frame batch, not a video file**. | github.com/ShmuelRonen/ComfyUI-LatentSyncWrapper `nodes.py`, `__init__.py`, `requirements.txt`, commits/main |
| Video load/combine | `ComfyUI-VideoHelperSuite`, pinned `4ee72c06…` (2026-05-10; no release tags). **`VHS_LoadVideo`** returns `(IMAGE, frame_count, AUDIO, video_info)` → index 0 = frames. `VHS_VideoCombine` → mp4. | github.com/Kosinkadink/ComfyUI-VideoHelperSuite `videohelpersuite/load_video_nodes.py`, commits/main |
| Weights repo | **`ByteDance/LatentSync-1.6`** — **PUBLIC** (`gated:false, private:false`) → **no HF token needed**. Real files: `latentsync_unet.pt` (~5GB), `whisper/tiny.pt`, `stable_syncnet.pt` (~1.6GB), `auxiliary/{i3d_torchscript.pt,koniq_pretrained.pkl,sfd_face.pth,syncnet_v2.model,vgg16-397923af.pth,vit_g_hybrid_pt_1200e_ssv2_ft.pth}`, `config.json`. **Inference actually loads only `latentsync_unet.pt` + `whisper/tiny.pt`** (config from the node's bundled `configs/unet/stage2_512.yaml`); syncnet/auxiliary are train-time only (baked anyway, harmless). | HuggingFace API `list_repo_files`; wrapper `nodes.py` (`snapshot_download(repo_id="ByteDance/LatentSync-1.6", allow_patterns=["latentsync_unet.pt","whisper/tiny.pt"], local_dir=~/.latentsync16_models)`) |
| VAE | **`stabilityai/sd-vae-ft-mse`** — PUBLIC. Baked into `checkpoints/vae/` **and** warmed into the HF cache (LatentSync's bundled inference module loads a VAE; nodes.py doesn't, so this is belt-and-suspenders). | wrapper README + HF API |

> The earlier claim (in the wrapper README) that "LatentSync 1.6 models are on a **private**
> HuggingFace repo" is **outdated** — the HF API reports `ByteDance/LatentSync-1.6` as public.
> That's why `download_models.py` needs no token. If HF ever re-gates it, add
> `HUGGINGFACE_ACCESS_TOKEN` as a RunPod build secret and pass `token=` in `download_models.py`.

---

## The video-input finding (IMPORTANT — read before wiring into proj)

**LatentSync is inherently video-to-video, but the current proj wiring feeds a single still image.**

- The wrapper's `LatentSyncNode`/`VideoLengthAdjuster` take an **IMAGE frame batch** — something
  must decode a video into frames first. That "something" is **`VHS_LoadVideo`**.
- The proj template `comfyui_workflows/latentsync_lipsync.json` uses **`LoadImage` + `VideoLengthAdjuster(mode=loop_to_audio)`** — i.e. it takes ONE photo and *loops it* to the
  audio length. That is a **single-still-image talking-head**, **not** re-lipsyncing a real video.
- `gen_lipsync_video.py` feeds the **presenter ref still** (or a shot's first frame), and the
  provider names it `presenter.png`. So today the whole path is still→talking-head.

**This repo's `workflows/latentsync_lipsync.json` is the corrected video-to-video graph**
(`VHS_LoadVideo` + `VideoLengthAdjuster(mode=normal)`). The image is built to satisfy **both**
graphs (all five node types installed), so it is forward-compatible.

### To actually run video-to-video through the pipeline, proj/ needs 3 small edits (NOT done this round — handed back for your review):

1. **`comfyui_workflows/latentsync_lipsync.json`** — replace with this repo's version
   (node 10 `LoadImage` → `VHS_LoadVideo`; node 20 `loop_to_audio` → `normal`).
2. **`providers/runpod_serverless_digitalhuman_provider.py`** — change `_IMAGE_NAME` from
   `"presenter.png"` to **`"source_video.mp4"`** (VHS_LoadVideo needs a video extension).
   *No `_fill_placeholders` change needed* — the corrected workflow reuses `__IMAGE_FILENAME__`.
3. **`pipeline/steps/gen_lipsync_video.py`** — feed the shot's **I2V video** bytes
   (`shot.video_asset_id` → the mp4) as `image=` instead of the presenter still. This makes
   lipsync a **re-lipsync PASS over the I2V output** (it now *requires* an I2V shot video to
   exist first) rather than a standalone still→video generator — a real sequencing/semantics
   change your storyboard gate should reflect.

(The worker itself does not care which of the two graphs you send — the provider ships the
workflow per-request. So you can validate the worker today with `test/run_lipsync_test.py`
before touching proj.)

---

## Repo layout

```
Dockerfile                     # base 5.8.6-base + ffmpeg + VHS + LatentSyncWrapper + patch + weights
download_models.py             # list_repo_files -> match -> download -> HIT/MISS log -> fail-loud
patch_handler.py               # VHS gifs/videos -> base64 in output.images (fail-loud, idempotent)
workflows/latentsync_lipsync.json   # corrected VIDEO-TO-VIDEO reference graph (+ diff-vs-proj notes)
test/run_lipsync_test.py       # stdlib-only real-machine smoke test (mirrors the provider contract)
给同事解锁-latentsync-worker.md  # one-action handoff (create private repo + connect RunPod + push)
```

## Build / push / wire (summary — full steps in the handoff doc)

1. Create **private** GitHub repo `zhang8193659/meditecv-latentsync-worker` (the provider docstring
   already references this name) and push this directory.
2. RunPod → Serverless → **New Endpoint → from GitHub repo** → pick this repo/branch → RunPod
   builds the image from the `Dockerfile`. GPU: a 24GB Ampere (RTX 3090/4090) class is plenty for
   LatentSync-1.6 @ 512.
3. Watch the **build log**:
   - node installs succeed (VHS + LatentSyncWrapper),
   - `patch_handler` prints `OK: handler patched`,
   - `download_models` prints one `HIT` per weight and ends with `ALL DONE`. Any `MISS (required…)`
     → the build **fails on purpose**; fix the one filename in `download_models.py` and re-push.
4. When the worker is **ready**, run `test/run_lipsync_test.py` (below).
5. Wire into mEditEcv `.env` / `config.py`:
   - `LIPSYNC_MODELS["latentsync"].endpoint_id = "<ENDPOINT_ID>"` (registry entry already exists),
   - `LIPSYNC_ACTIVE_MODEL = "latentsync"` (already the default),
   - `DH_PROVIDER = "runpod_serverless"`, `RUNPOD_API_KEY = <key>`.
   Restart the app. (`.env` JSON override example:
   `LIPSYNC_MODELS='{"latentsync":{"workflow":"latentsync_lipsync","endpoint_id":"<ID>","probe_workflow":""}}'`.)

## Real-machine verification

```bash
export RUNPOD_API_KEY=...
# make a voiceover with edge-tts (free):
edge-tts --text "这款面膜真的太好用了，补水效果肉眼可见。" --voice zh-CN-XiaoxiaoNeural --write-media voice.mp3
python test/run_lipsync_test.py --endpoint <ENDPOINT_ID> \
    --video sample_shot.mp4 --audio voice.mp3 --fps 25 --out out.mp4
# open out.mp4 -> the mouth should track the audio.
```
`sample_shot.mp4` should be a short clip with a **clearly visible, roughly front-facing face**
(LatentSync detects/crops the face; tiny or side-profile faces degrade or fail). Prefer 25fps.

> `--fps` is injected into the workflow as an **INTEGER** (never the string `"25"`; ComfyUI 500s on
> a quoted number). LatentSync-1.6 trains at 25fps; the proj pipeline runs at `COMPOSE_FPS=30`,
> which works but 25 is optimal — if 30fps output looks off, hardcode 25 on workflow nodes 10/20/40.

## Known build frictions (iterate from the build log)

| Symptom in build log | Fix |
|---|---|
| `decord` wheel build fails on Ubuntu 24.04 / py3.12 | **Already handled**: the Dockerfile strips `decord` from requirements and installs `eva-decord` (drop-in, provides `import decord`). |
| `mediapipe` / `numpy` conflict with ComfyUI's numpy | mediapipe needs `numpy<2`; ComfyUI 5.8.6 ships numpy 1.26 so it should hold. If it breaks, pin `numpy<2` in a `RUN pip install "numpy<2"` after the node installs. |
| `face-alignment` pulls `numba`/`llvmlite` and fails to build | add `RUN pip install --no-cache-dir numba llvmlite` before the LatentSync requirements, or pin a wheel-having version. |
| `pip` resolves a torch reinstall (would break CUDA) | LatentSync's requirements do **not** list torch; if a transitive dep tries, add `--no-deps` selectively or pin the offending package. |
| First cold start still downloads ~7GB | means the baked path didn't match the runtime read path — check the `download_models` log for the resolved `CKPT_DIR`/`PERSIST_DIR` and confirm `HOME` at runtime is `/root` (see download_models "belt-and-suspenders" note). |
| `output.images` empty on COMPLETED | the handler patch didn't apply — check the build log for `[patch_handler] OK`. If it says FATAL (anchor not found), the base image handler changed; re-copy the verbatim anchors into `patch_handler.py`. |

## License / clean-room

This repo is **our own** infrastructure (Dockerfile + two helper scripts + a self-authored
workflow graph). It does **not** vendor ComfyUI / LatentSync / VHS source — those are pulled at
build time and invoked only over HTTP by mEditEcv (GPL/Apache stay process-isolated, no
contamination of the closed-source proj). **Commercial-license check for带货 (a commercial use):**
LatentSync's weights (`ByteDance/LatentSync-1.6`) must be cleared for commercial use before you
ship — verify the model card / license each time, as required by the lipsync-worker todo.
