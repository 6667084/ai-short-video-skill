#!/usr/bin/env python3
"""抖音/视频号封面：从主片取一帧 + 透明底标题美术字，放在 3:4 安全区下部（不挡眼睛和嘴）。

用法：
  python make_cover.py --video raw/篇名_主片.mp4 --t 1.6 --title images/篇名_封面标题.png --out final/品牌_篇名_抖音封面.png
  可选：--width 860（标题宽 px）--bottom 1680（标题底边 y，3:4 安全区为 y 240–1680）--preview（另存 360×640 预览图）
依赖：ffmpeg、Pillow。
"""
from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

W, H = 1080, 1920
SAFE_TOP, SAFE_BOTTOM = 240, 1680  # 9:16 画面按 3:4 居中裁切后的可见区


def grab(video: str, t: float) -> Image.Image:
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "f.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(t), "-i", str(Path(video).expanduser()), "-frames:v", "1",
                        "-vf", f"scale={W}:{H}:flags=lanczos,unsharp=5:5:0.5", str(out)], check=True)
        return Image.open(out).convert("RGBA")


def make_cover(video: str, t: float, title: str, out: str, width: int = 860, bottom: int = SAFE_BOTTOM) -> dict:
    bg = grab(video, t)
    tt = Image.open(Path(title).expanduser()).convert("RGBA")
    box = tt.getchannel("A").point(lambda a: 255 if a > 10 else 0).getbbox()
    if box is None:
        raise SystemExit("标题图是全透明的")
    if tt.getchannel("A").getextrema()[0] == 255:
        raise SystemExit("标题图没有透明背景：请重新生成透明底标题（不要抠白凑合）")
    tt = tt.crop(box)
    tt = tt.resize((width, round(tt.height * width / tt.width)), Image.LANCZOS)
    y = bottom - tt.height
    if y < SAFE_TOP:
        raise SystemExit(f"标题太高（{tt.height}px），超出 3:4 安全区：请减小 --width")
    bg.alpha_composite(tt, ((W - width) // 2, y))
    p = Path(out).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    bg.convert("RGB").save(p)
    return {"cover": str(p), "title_box": [(W - width) // 2, y, (W + width) // 2, bottom]}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", required=True)
    ap.add_argument("--t", type=float, required=True, help="取帧时间（秒）：选表情最夸张、脸最大的一帧")
    ap.add_argument("--title", required=True, help="透明底标题美术字 PNG")
    ap.add_argument("--out", required=True)
    ap.add_argument("--width", type=int, default=860)
    ap.add_argument("--bottom", type=int, default=SAFE_BOTTOM)
    ap.add_argument("--preview", action="store_true")
    a = ap.parse_args()
    r = make_cover(a.video, a.t, a.title, a.out, a.width, a.bottom)
    if a.preview:
        pv = Path(r["cover"]).with_name(Path(r["cover"]).stem + "_preview.jpg")
        Image.open(r["cover"]).resize((360, 640)).save(pv)
        r["preview"] = str(pv)
    print(r)
