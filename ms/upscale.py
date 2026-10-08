"""本地超分：seedvr2（质量最好，慢）/ realesrgan（较快，逐帧）/ lanczos（瞬时，仅插值）。均保留原音轨。

实际工作由 tools/upscale.sh 完成，这里只负责参数与后台任务登记。
"""
from __future__ import annotations

import shlex
from pathlib import Path

from . import jobs
from .config import REALESRGAN_DIR, ROOT, SEEDVR2_DIR, job_dir
from .media import probe

SCRIPT = ROOT / "tools" / "upscale.sh"


def local_upscale(video: str, job: str, method: str = "auto", short_side: int = 1080, name: str | None = None,
                  seedvr2_model: str = "seedvr2_ema_3b_fp16.safetensors", batch_size: int = 33) -> dict:
    src = Path(video).expanduser().resolve()
    info = probe(src)
    scale = short_side / min(info["width"], info["height"])
    if method == "auto":
        if scale > 1.75:
            raise ValueError(f"放大倍数 {scale:.2f}x（如 480p→1080p）本地方案画质不足：建议 cloud_upscale_quote(provider='enhance', "
                             f"tier='professional')（需用户确认费用），或明确指定 method='realesrgan'（免费，约 25 分钟/30s，皮肤偏磨皮）")
        method = "lanczos"
    if method not in ("seedvr2", "realesrgan", "lanczos", "anime"):
        raise ValueError("method 只能是 auto / lanczos / anime（卡通动画）/ realesrgan / seedvr2")
    tw, th = round(info["width"] * scale / 2) * 2, round(info["height"] * scale / 2) * 2
    out = job_dir(job, "upscaled") / f"{name or src.stem}_{method}_{short_side}p.mp4"
    args = [str(SCRIPT), method, str(src), str(out), str(tw), str(th), str(short_side), str(info["fps"] or 30),
            str(SEEDVR2_DIR), seedvr2_model, str(batch_size), str(REALESRGAN_DIR)]
    eta = {"lanczos": "数秒", "anime": f"约 {max(1, round(info['duration'] / 10))} 分钟", "seedvr2": f"约 {round(info['duration'] * 1.5)}–{round(info['duration'] * 3)} 分钟",
           "realesrgan": f"约 {round(info['duration'] * 0.9)} 分钟"}[method]
    res = jobs.start(f"upscale-{method}", " ".join(shlex.quote(a) for a in args), [str(out)],
                     {"source": str(src), "target": f"{tw}x{th}", "eta_hint": eta})
    return res | {"target": f"{tw}x{th}", "eta": eta}
