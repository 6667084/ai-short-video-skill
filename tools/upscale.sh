#!/bin/bash
# 用法: upscale.sh <method> <src> <out> <target_w> <target_h> <short_side> <fps> <seedvr2_dir> <seedvr2_model> <batch> <realesrgan_dir>
set -euo pipefail
METHOD="$1"; SRC="$2"; OUT="$3"; TW="$4"; TH="$5"; SHORT="$6"; FPS="$7"
SVR_DIR="$8"; SVR_MODEL="$9"; BATCH="${10}"; RESR_DIR="${11}"

WORK="${OUT%.mp4}.work"          # 临时目录：固定位于输出文件旁、带 .work 后缀
mkdir -p "$WORK"
TMP="$WORK/video_noaudio.mp4"

mux() {  # 拼回原音轨（若有），并确保精确尺寸
  ffmpeg -y -v error -i "$TMP" -i "$SRC" -map 0:v:0 -map '1:a:0?' \
    -vf "scale=${TW}:${TH}:flags=lanczos,format=yuv420p" -c:v libx264 -crf 14 -preset slow \
    -c:a copy -movflags +faststart "$OUT"
}

case "$METHOD" in
  lanczos)
    ffmpeg -y -v error -i "$SRC" -vf "scale=${TW}:${TH}:flags=lanczos,unsharp=5:5:0.5,format=yuv420p" \
      -c:v libx264 -crf 14 -preset slow -c:a copy -movflags +faststart "$OUT"
    ;;
  seedvr2)
    cd "$SVR_DIR"
    PYTORCH_ENABLE_MPS_FALLBACK=1 HTTPS_PROXY="${HTTPS_PROXY:-}" \
      .venv/bin/python -u inference_cli.py "$SRC" --resolution "$SHORT" --batch_size "$BATCH" \
      --dit_model "$SVR_MODEL" --color_correction lab --video_backend ffmpeg --output "$TMP"
    mux
    ;;
  realesrgan)
    mkdir -p "$WORK/in" "$WORK/out"
    ffmpeg -y -v error -i "$SRC" -fps_mode passthrough "$WORK/in/%06d.png"  # 只取真实帧：即梦等 60fps 封装、实际 24fps 的视频否则会被复制帧拉长
    (cd "$RESR_DIR" && ./realesrgan-ncnn-vulkan -i "$WORK/in" -o "$WORK/out" -n realesrgan-x4plus -s 4 -f png -j 2:4:4)
    ffmpeg -y -v error -framerate "$FPS" -i "$WORK/out/%06d.png" -c:v libx264 -crf 12 -preset fast -pix_fmt yuv420p "$TMP"
    mux
    ;;
  anime)  # 卡通/3D 动画专用：realesr-animevideov3 x2（快，不臆造纹理），再精确缩放到目标尺寸
    mkdir -p "$WORK/in" "$WORK/out"
    ffmpeg -y -v error -i "$SRC" -fps_mode passthrough "$WORK/in/%06d.png"  # 只取真实帧：即梦等 60fps 封装、实际 24fps 的视频否则会被复制帧拉长
    (cd "$RESR_DIR" && ./realesrgan-ncnn-vulkan -i "$WORK/in" -o "$WORK/out" -n realesr-animevideov3 -s 2 -f png -j 2:4:4)
    ffmpeg -y -v error -framerate "$FPS" -i "$WORK/out/%06d.png" -c:v libx264 -crf 12 -preset fast -pix_fmt yuv420p "$TMP"
    mux
    ;;
  *) echo "unknown method $METHOD"; exit 2 ;;
esac

# 清理：只删除本脚本创建的 .work 目录
case "$WORK" in *.work) find "$WORK" -mindepth 1 -delete && rmdir "$WORK" ;; esac
echo "done: $OUT"
