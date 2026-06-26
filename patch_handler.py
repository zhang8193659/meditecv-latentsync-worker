"""构建期给 worker-comfyui 的 /handler.py 打补丁。

原版 handler 收集输出时只处理 history 里每个节点的 ``images`` 键,
把 VHS_VideoCombine 的视频产物(放在 ``gifs`` 键,虽叫 gifs 实为 mp4)当
"其它输出"忽略 —— 结果对口型 mp4 不会被返回(任务成功但 output 为空)。

本补丁在「if "images" in node_output:」之前注入几行,把 ``gifs``/``videos``
键的条目并入 ``images``,使其复用既有的「/view 取字节 → base64 返回」逻辑。
条目结构一致({filename,subfolder,type}),mp4 会以 type=base64、扩展名 .mp4 返回。

幂等:已打过补丁(出现 setdefault("images") 注入标记)则跳过。
"""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/handler.py"
src = open(path, encoding="utf-8").read()

MARK = "meditecv-video-output-patch"
if MARK in src:
    print("handler already patched, skip")
    sys.exit(0)

ANCHOR = 'if "images" in node_output:'
lines = src.splitlines(keepends=True)
out = []
done = False
for line in lines:
    if (not done) and line.lstrip().startswith(ANCHOR):
        indent = line[: len(line) - len(line.lstrip())]
        inject = (
            f"{indent}# --- {MARK}: 把 VHS 视频输出(gifs/videos)并入 images,使视频也被返回 ---\n"
            f'{indent}for _vk in ("gifs", "videos"):\n'
            f"{indent}    if _vk in node_output:\n"
            f'{indent}        node_output.setdefault("images", []).extend(node_output[_vk])\n'
        )
        out.append(inject)
        done = True
    out.append(line)

if not done:
    sys.stderr.write(f"PATCH FAILED: anchor not found: {ANCHOR!r}\n")
    sys.exit(1)

open(path, "w", encoding="utf-8").write("".join(out))
print("handler patched OK (gifs/videos -> images)")
