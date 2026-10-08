"""ffprobe / 抽帧联系表 / 子进程封装。"""
from __future__ import annotations

import json
import math
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

LABEL_FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        raise RuntimeError(f"命令失败: {' '.join(map(str, cmd[:4]))} ...\n{r.stderr[-2500:]}")
    return r


def probe(path: str | Path) -> dict:
    d = json.loads(run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)]).stdout)
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in d["streams"] if s["codec_type"] == "audio"), None)
    fps = None
    if v and v.get("avg_frame_rate", "0/0") != "0/0":
        n, dd = v["avg_frame_rate"].split("/")
        fps = round(int(n) / int(dd), 3) if int(dd) else None
    return {"duration": float(d["format"].get("duration", 0)), "width": v and v["width"], "height": v and v["height"],
            "fps": fps, "has_audio": a is not None}


def contact_sheet(video: str | Path, count: int = 9, out: str | Path | None = None, width: int = 360) -> str:
    src = Path(video).expanduser()
    dur = probe(src)["duration"]
    if not out:  # 审片图放在同目录的隐藏 .review/ 下，避免成片目录杂乱
        (src.parent / ".review").mkdir(exist_ok=True)
        out = src.parent / ".review" / (src.stem + "_contact.jpg")
    out = Path(out)
    with tempfile.TemporaryDirectory() as td:
        frames = []
        for i in range(count):
            t = dur * (i + 0.5) / count
            f = Path(td) / f"{i}.jpg"
            run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1", "-vf", f"scale={width}:-2", str(f)])
            frames.append((t, Image.open(f).convert("RGB")))
    w, h = frames[0][1].size
    cols = min(count, 3)
    rows = math.ceil(count / cols)
    img = Image.new("RGB", (cols * w, rows * (h + 28)), "black")
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(LABEL_FONT, 20)
    for i, (t, fr) in enumerate(frames):
        x, y = (i % cols) * w, (i // cols) * (h + 28)
        img.paste(fr, (x, y + 28))
        d.text((x + 8, y + 3), f"{t:.1f}s", font=font, fill="white")
    img.save(out, quality=88)
    return str(out)
