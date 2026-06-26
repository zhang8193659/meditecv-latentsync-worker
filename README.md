# meditecv-latentsync-worker

mEditEcv 的 **LatentSync 对口型** RunPod Serverless worker 镜像源。

## 这是什么

基于 [`runpod/worker-comfyui`](https://github.com/runpod-workers/worker-comfyui)(ComfyUI + serverless handler),
叠加：

- **ComfyUI-LatentSyncWrapper**（`LatentSyncNode` 对口型推理 + `VideoLengthAdjuster` 把单张静图 `loop_to_audio` 循环成匹配音频时长的帧）
- **ComfyUI-VideoHelperSuite**（`VHS_VideoCombine` 合成 mp4）
- 预烤权重：`ByteDance/LatentSync-1.6`（`latentsync_unet.pt` + `whisper/tiny.pt`）+ s3fd 人脸检测器

权重在构建期下好，冷启动零下载。

## RunPod 怎么用

RunPod Serverless → 连本 GitHub 仓库 → 自动 build 此 Dockerfile → 部署为 endpoint。
调用形状（worker-comfyui 标准）：

```
POST https://api.runpod.ai/v2/{ENDPOINT_ID}/run
Authorization: Bearer {RUNPOD_API_KEY}
{ "input": { "workflow": { ...ComfyUI API 格式 workflow... }, "images": [ {"name":"presenter.png","image":"<base64>"} ] } }
```

→ 拿 job id → 轮询 `GET /v2/{ENDPOINT_ID}/status/{id}` 到 COMPLETED。

workflow 用 mEditEcv 仓库 `comfyui_workflows/latentsync_lipsync.json`（已含 VideoLengthAdjuster）。

## 商用许可

LatentSync 模型权重商用授权需自行核（带货为商用场景）。
