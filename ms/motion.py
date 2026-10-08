"""代码生成视频片段与画面替换（本地、免费）。

motion_clip：背景图 + 图层（真实产品图 / 文字）→ Remotion 渲染的动画片段（数桶、算账、定版等），产品像素不经 AI 重画。
broll_insert：把插入镜头覆盖到主视频的指定时段，只换画面、保留主视频音轨（口播不断），可选末帧定格延长。
"""
from __future__ import annotations

import json
import shlex
from pathlib import Path

from . import jobs
from .compose import _title_image, font_family
from .config import COMPOSE_DIR
from .config import job_dir
from .media import run
from .media import probe

W, H, FPS = 1080, 1920, 30
ANIMS = ("pop", "drop", "slide_left", "slide_right", "slide_up", "fade", "none")


def _bg_data_url(path: str) -> str:
    """背景图裁成 9:16、缩到 1080x1920 后转 data URL（避免把超大图塞进 props）。"""
    import base64
    import io
    from PIL import Image, ImageOps
    im = Image.open(Path(path).expanduser()).convert("RGB")
    im = ImageOps.fit(im, (W, H), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def motion_clip(job: str, name: str, duration: float, layers: list[dict], background: str | None = None,
                bg_color: str = "#FFFFFF", bg_motion: str = "zoom_in", transparent: bool = False) -> dict:
    """transparent=True：透明背景（ProRes 4444 .mov，无音轨），用 broll_insert 叠在主视频上而不是替换画面
    （如人物做展示手势、画面一侧留空，真实产品从空位弹出）。"""
    if not 0.5 <= duration <= 30:
        raise ValueError("duration 须在 0.5–30 秒")
    out_layers = []
    for i, l in enumerate(layers):
        anim = l.get("anim") or "pop"
        if anim not in ANIMS:
            raise ValueError(f"图层 {i} 的 anim 只能是 {ANIMS}")
        start, end = float(l.get("start", 0)), l.get("end")
        item = {"x": round(float(l.get("x", 0.5)) * W), "y": round(float(l.get("y", 0.5)) * H),
                "startMs": round(start * 1000), "anim": anim, "float": bool(l.get("bob", False)),
                "rotate": float(l.get("rotate", 0)), "z": int(l.get("z", i)), "shadow": bool(l.get("shadow", True))}
        if end not in (None, 0, ""):
            item["endMs"] = round(float(end) * 1000)
        if l.get("image"):
            src = Path(l["image"]).expanduser()
            if not src.is_file():
                raise FileNotFoundError(f"图层 {i} 图片不存在：{src}")
            w = float(l.get("w", 0.4)) * W
            data, pw, ph = _title_image(str(src), w, 10 * H)  # 裁透明边，按宽度等比缩放
            item |= {"src": data, "w": pw, "h": ph}
        elif l.get("text"):
            if l.get("font"):
                item["font"] = font_family(l["font"])
            item |= {"text": l["text"], "size": int(l.get("size", 96)), "color": l.get("color") or "#FFFFFF",
                     "stroke": l.get("stroke") or "#111111", "strike": bool(l.get("strike", False))}
            if l.get("bg"):
                item["bg"] = l["bg"]
        else:
            raise ValueError(f"图层 {i} 需要 image 或 text")
        out_layers.append(item)
    bg = {"color": "transparent" if transparent else bg_color, "motion": bg_motion}
    if transparent and background:
        raise ValueError("transparent=True 时不能设置 background")
    if background:
        if not Path(background).expanduser().is_file():
            raise FileNotFoundError(f"背景图不存在：{background}")
        bg["src"] = _bg_data_url(background)
    props = {"width": W, "height": H, "fps": FPS, "durationMs": round(duration * 1000), "bg": bg, "layers": out_layers}
    raw = job_dir(job, "raw")
    work = raw / ".motion"
    work.mkdir(parents=True, exist_ok=True)
    props_path = work / f"{name}_props.json"
    props_path.write_text(json.dumps(props, ensure_ascii=False))
    silent, out = work / f"{name}_silent.mp4", raw / f"{name}.mp4"
    if transparent:  # 带 alpha 的 ProRes 4444；叠加时保留主视频画面
        out = raw / f"{name}.mov"
        remotion = ["npx", "remotion", "render", "src/index.ts", "Motion", str(out), "--codec=prores", "--prores-profile=4444",
                    "--image-format=png", "--pixel-format=yuva444p10le", f"--props={props_path}", "--concurrency=8", "--log=error"]
        script = jobs.render_locked(f"cd {shlex.quote(str(COMPOSE_DIR))} && {' '.join(shlex.quote(a) for a in remotion)}") + " && echo done"
    else:
        remotion = ["npx", "remotion", "render", "src/index.ts", "Motion", str(silent), "--codec=h264", "--crf=16",
                    f"--props={props_path}", "--concurrency=8", "--log=error"]
        ff = ["ffmpeg", "-y", "-v", "error", "-i", str(silent), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
              "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)]
        script = (jobs.render_locked(f"cd {shlex.quote(str(COMPOSE_DIR))} && {' '.join(shlex.quote(a) for a in remotion)}") + " && "
                  f"{' '.join(shlex.quote(a) for a in ff)} && rm -f {shlex.quote(str(silent))} && echo done")
    # 偶发“Visited localhost but got no response”（图层多、props 大时并发页面加载超时）：失败后降并发重试一次
    rq = " ".join(shlex.quote(a) for a in remotion)
    script = script.replace(rq, f"( {rq} || (sleep 2 && {rq.replace('--concurrency=8', '--concurrency=4')}) )", 1)
    res = jobs.start("motion", script, [str(out)], {"eta_hint": "约 20–60 秒"})
    return res | {"output": str(out), "duration": duration, "layers": len(out_layers), "transparent": transparent}


def probe_pix_fmt(path: Path) -> str | None:
    r = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=pix_fmt", "-of", "csv=p=0", str(path)])
    return (r.stdout if hasattr(r, "stdout") else str(r)).strip() or None


def broll_insert(base: str, inserts: list[dict], job: str, name: str, freeze_tail: float = 0.0,
                 trim_end: float = 0.0) -> dict:
    """inserts: [{"video": 路径, "start": 秒, "duration": 秒(0=插入片段全长)}]。输出 1080x1920/30fps、保留 base 音轨。
    插入片段带 alpha（motion_clip(transparent=True) 的 .mov）时按透明度叠加在主视频上，否则整画面替换。"""
    b = Path(base).expanduser()
    if not b.is_file():
        raise FileNotFoundError(f"主视频不存在：{b}")
    info = probe(b)
    bw, bh, total = W, H, info["duration"]  # 统一输出 1080x1920：低分辨率主片先 Lanczos+轻锐化放大，插入镜头保持原生清晰度
    if trim_end and trim_end < total:
        total = trim_end
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(b)]
    sharpen = ",unsharp=5:5:0.5" if min(info["width"], info["height"]) < 1000 else ""
    fc = [f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H}{sharpen},setsar=1,fps={FPS}[b0]"]
    prev, used = "b0", []
    for i, ins in enumerate(inserts, start=1):
        v = Path(ins["video"]).expanduser()
        if not v.is_file():
            raise FileNotFoundError(f"插入镜头不存在：{v}")
        s = float(ins["start"])
        d = float(ins.get("duration") or 0) or probe(v)["duration"]
        if s < 0 or s >= total:
            raise ValueError(f"插入 {i} 的 start={s} 超出主视频时长 {total:.2f}s")
        e = min(total, s + d)
        cmd += ["-i", str(v)]
        alpha = "a" in (probe_pix_fmt(v) or "").replace("gray", "")  # yuva444p10le / argb 等：叠加而非替换
        fc.append(f"[{i}:v]{'format=yuva444p,' if alpha else ''}scale={bw}:{bh}:force_original_aspect_ratio=increase:flags=lanczos,crop={bw}:{bh},"
                  f"fps={FPS},setpts=PTS-STARTPTS+{s}/TB[i{i}]")
        fc.append(f"[{prev}][i{i}]overlay=eof_action=pass:enable='between(t,{s},{e})'[v{i}]")
        prev = f"v{i}"
        used.append({"video": str(v), "start": s, "end": round(e, 3), "mode": "overlay" if alpha else "replace"})
    vchain = f"[{prev}]trim=0:{total:.3f},setpts=PTS-STARTPTS"
    achain = f"[0:a]atrim=0:{total:.3f},asetpts=PTS-STARTPTS"
    if freeze_tail > 0:
        vchain += f",tpad=stop_mode=clone:stop_duration={freeze_tail}"
        achain += f",apad=pad_dur={freeze_tail}"
    fc += [vchain + ",format=yuv420p[vout]", achain + "[aout]"]
    out = job_dir(job, "raw") / f"{name}.mp4"
    has_audio = info.get("has_audio")
    if not has_audio:
        fc = fc[:-1]
    cmd += ["-filter_complex", ";".join(fc), "-map", "[vout]"] + (["-map", "[aout]"] if has_audio else []) + [
        "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-r", str(FPS), "-c:a", "aac", "-b:a", "192k", str(out)]
    run(cmd)
    return {"video": str(out), "duration": round(probe(out)["duration"], 2), "inserts": used,
            "freeze_tail": freeze_tail, "kept_audio": bool(has_audio)}
