#!/usr/bin/env python3
# =====================================================================================
# run_lipsync_test.py -- real-machine smoke test for the LatentSync worker.
#
# Drives the SAME contract the mEditEcv provider uses (POST /run with
# {"input":{"workflow":<prompt>,"images":[{name,image(base64)}...]}}, then GET /status/{id},
# then pull the mp4 base64 out of output.images[]) -- but standalone, so you can validate
# the worker WITHOUT touching proj/. Stdlib only (no pip installs on your laptop).
#
# Usage:
#   export RUNPOD_API_KEY=...            # your RunPod API key (Bearer)
#   python run_lipsync_test.py \
#       --endpoint <ENDPOINT_ID> \
#       --video sample_shot.mp4 \        # a short talking-head clip (ideally 25fps, face visible)
#       --audio voice.mp3 \             # e.g. produced by:  edge-tts --text "..." --write-media voice.mp3
#       --fps 25 \
#       --out out.mp4
#
# Success = out.mp4 is written and, on playback, the mouth tracks the audio.
#
# NOTE: matches the provider exactly -- __FPS__ is injected as an INTEGER (never the string
# "25"); the input video rides the images[] channel named source_video.mp4 (a video extension
# is required so VHS_LoadVideo will load it).
# =====================================================================================
import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_WORKFLOW = os.path.join(HERE, "..", "workflows", "latentsync_lipsync.json")

IMAGE_NAME = "source_video.mp4"   # the video rides the images[] channel; MUST be a video extension
AUDIO_NAME = "voice.mp3"
VIDEO_EXTS = (".mp4", ".webm", ".mov", ".mkv", ".gif")


def fill_placeholders(node, *, image_name, audio_name, output_prefix, fps):
    """Replicates the provider's _fill_placeholders: __FPS__ -> int, others -> str substitution."""
    if isinstance(node, dict):
        return {k: fill_placeholders(v, image_name=image_name, audio_name=audio_name,
                                     output_prefix=output_prefix, fps=fps) for k, v in node.items()}
    if isinstance(node, list):
        return [fill_placeholders(v, image_name=image_name, audio_name=audio_name,
                                  output_prefix=output_prefix, fps=fps) for v in node]
    if isinstance(node, str):
        if node == "__FPS__":
            return int(fps)                       # INTEGER, not "25"
        node = node.replace("__IMAGE_FILENAME__", image_name)
        node = node.replace("__AUDIO_FILENAME__", audio_name)
        node = node.replace("__OUTPUT_PREFIX__", output_prefix)
        return node
    return node


def post_json(url, payload, api_key):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_json(url, api_key):
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def extract_video(output):
    imgs = output.get("images") if isinstance(output, dict) else None
    if not isinstance(imgs, list):
        return None
    cand = [(it.get("filename", ""), it.get("data")) for it in imgs
            if isinstance(it, dict) and it.get("type") == "base64" and it.get("data")]
    if not cand:
        return None
    vids = [c for c in cand if str(c[0]).lower().endswith(VIDEO_EXTS)]
    fn, b64 = vids[-1] if vids else cand[-1]
    return base64.b64decode(b64), fn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", required=True, help="RunPod serverless endpoint id")
    ap.add_argument("--video", required=True, help="source talking-head clip (mp4)")
    ap.add_argument("--audio", required=True, help="voiceover audio (mp3/wav)")
    ap.add_argument("--fps", type=int, default=25)
    ap.add_argument("--out", default="out.mp4")
    ap.add_argument("--workflow", default=DEFAULT_WORKFLOW)
    ap.add_argument("--timeout", type=int, default=1200, help="poll timeout seconds")
    args = ap.parse_args()

    api_key = os.environ.get("RUNPOD_API_KEY")
    if not api_key:
        print("ERROR: set RUNPOD_API_KEY env var", file=sys.stderr)
        sys.exit(2)

    with open(args.workflow, encoding="utf-8") as f:
        wf = json.load(f)
    wf.pop("_comment", None)
    prompt = fill_placeholders(wf, image_name=IMAGE_NAME, audio_name=AUDIO_NAME,
                               output_prefix="meditecv_lipsync_test", fps=args.fps)

    with open(args.video, "rb") as f:
        video_b64 = base64.b64encode(f.read()).decode("ascii")
    with open(args.audio, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode("ascii")

    payload = {"input": {"workflow": prompt, "images": [
        {"name": IMAGE_NAME, "image": video_b64},
        {"name": AUDIO_NAME, "image": audio_b64},
    ]}}

    base = f"https://api.runpod.ai/v2/{args.endpoint}"
    print(f"POST {base}/run  (video={args.video}, audio={args.audio}, fps={args.fps})")
    j = post_json(f"{base}/run", payload, api_key)
    job_id = j.get("id")
    if not job_id:
        print(f"ERROR: no job id in /run response: {j}", file=sys.stderr)
        sys.exit(1)
    print(f"job id = {job_id}")

    deadline = time.time() + args.timeout
    while True:
        time.sleep(5)
        try:
            s = get_json(f"{base}/status/{job_id}", api_key)
        except urllib.error.HTTPError as e:
            print(f"  status HTTP error: {e}")
            continue
        status = (s.get("status") or "").upper()
        print(f"  status = {status}")
        if status == "COMPLETED":
            vid = extract_video(s.get("output") or {})
            if not vid:
                print(f"ERROR: completed but no video in output.images: "
                      f"{json.dumps(s.get('output'))[:600]}", file=sys.stderr)
                sys.exit(1)
            data, fn = vid
            with open(args.out, "wb") as f:
                f.write(data)
            print(f"SUCCESS: wrote {args.out} ({len(data)/1e6:.2f} MB, source filename {fn})")
            print("Now PLAY it and confirm the mouth tracks the audio.")
            return
        if status in ("FAILED", "CANCELLED", "TIMED_OUT"):
            print(f"ERROR: job {status}: {json.dumps(s)[:800]}", file=sys.stderr)
            sys.exit(1)
        if time.time() > deadline:
            print(f"ERROR: timed out after {args.timeout}s (last status {status})", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
