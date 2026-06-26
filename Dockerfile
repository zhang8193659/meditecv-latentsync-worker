# mEditEcv —— LatentSync 对口型 RunPod Serverless worker
# 基础:runpod/worker-comfyui(ComfyUI + serverless handler,不含模型)
# 在其上装 LatentSync + VideoHelperSuite 自定义节点,并把权重「烤」进镜像,
# 避免 serverless 冷启动时现下 5G 权重导致首请求超时。
FROM runpod/worker-comfyui:5.8.6-base

# ---- 自定义节点 ----
# VideoHelperSuite:提供 VHS_VideoCombine(把帧+音频合成 mp4)
RUN cd /comfyui/custom_nodes && \
    git clone --depth 1 https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git && \
    pip install -r ComfyUI-VideoHelperSuite/requirements.txt

# LatentSync wrapper:提供 LatentSyncNode(对口型推理)+ VideoLengthAdjuster(loop_to_audio
# 把单张静图循环成匹配音频时长的帧序列 —— ecv 只给一张人物图,靠它扩成视频喂 LatentSync)
RUN cd /comfyui/custom_nodes && \
    git clone --depth 1 https://github.com/ShmuelRonen/ComfyUI-LatentSyncWrapper.git && \
    pip install -r ComfyUI-LatentSyncWrapper/requirements.txt

# ---- 预烤权重(构建期下好,冷启动零下载)----
# LatentSync 1.6 UNet + whisper tiny → 节点的 checkpoints/(节点 setup_models 期望路径)
RUN python -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='ByteDance/LatentSync-1.6', allow_patterns=['latentsync_unet.pt','whisper/tiny.pt'], local_dir='/comfyui/custom_nodes/ComfyUI-LatentSyncWrapper/checkpoints')"

# s3fd 人脸检测器 → ~/.latentsync16_models/(节点 pre_download_models 期望路径与文件名)
RUN python -c "from huggingface_hub import hf_hub_download; import os,shutil; os.makedirs('/root/.latentsync16_models',exist_ok=True); p=hf_hub_download(repo_id='vinthony/SadTalker', filename='hub/checkpoints/s3fd-619a316812.pth'); shutil.copy(p,'/root/.latentsync16_models/s3fd-e19a316812.pth')"

# 标记依赖已装,跳过 wrapper 首次运行时的运行时 pip 自装(serverless 内不宜联网装包)
RUN touch /root/.latentsync16_dependencies_installed

# worker-comfyui 自带 serverless handler 作为 ENTRYPOINT,无需覆盖。
