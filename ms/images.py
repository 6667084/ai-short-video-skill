"""GPT-image（通过 CLIProxyAPI 暴露的 OpenAI 兼容接口）。"""
from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

import httpx

from .config import CFG, cfg, job_dir

IMAGE_MODEL = cfg("IMAGE_MODEL", "gpt-image-2.5")


def _client() -> httpx.Client:
    return httpx.Client(base_url=cfg("IMAGE_API_BASE").rstrip("/"), trust_env=False, proxy=CFG.get("IMAGE_PROXY") or None,
                        headers={"Authorization": f"Bearer {cfg('IMAGE_API_KEY')}"}, timeout=httpx.Timeout(600, connect=30))


def _save(resp: dict, job: str, name: str) -> list[str]:
    out, d = [], job_dir(job, "images")
    items = resp.get("data") or []
    if not items:
        raise RuntimeError(f"接口未返回图片（可能被内容审核拦截或上游超时，可改写提示词后重试）：{str(resp)[:300]}")
    for i, item in enumerate(items):
        stem = name if len(items) == 1 else f"{name}_{i + 1}"
        path, n = d / f"{stem}.png", 2
        while path.exists():
            path, n = d / f"{stem}_v{n}.png", n + 1
        if item.get("b64_json"):
            path.write_bytes(base64.b64decode(item["b64_json"]))
        elif item.get("url"):
            with httpx.Client(trust_env=False, timeout=120) as c:
                path.write_bytes(c.get(item["url"]).content)
        out.append(str(path))
    return out


def generate(prompt: str, job: str, name: str, size: str, n: int, quality: str | None, model: str | None,
             background: str | None = None) -> dict:
    body = {"model": model or IMAGE_MODEL, "prompt": prompt, "size": size, "n": n}
    if background:
        body["background"] = background
        body["output_format"] = "png"
    if quality:
        body["quality"] = quality
    with _client() as c:
        r = c.post("/images/generations", json=body)
    if r.status_code >= 400:
        raise RuntimeError(f"生图失败 {r.status_code}: {r.text[:500]}")
    paths = _save(r.json(), job, name)
    out = {"paths": paths, "model": body["model"]}
    if background == "transparent":  # 模型偶尔把“透明”画成灰白棋盘格（不透明 RGB）：明确告警，避免贴上片后挡画面
        from PIL import Image
        bad = [p for p in paths if "A" not in Image.open(p).getbands() or Image.open(p).getchannel("A").getextrema()[0] == 255]
        if bad:
            out["warning"] = f"要求透明背景但这些图没有透明区域（可能把棋盘格画进了图里），请重新生成：{bad}"
    return out


def edit(prompt: str, image_paths: list[str], job: str, name: str, size: str, quality: str | None, model: str | None,
         background: str | None = None) -> dict:
    files = [("image[]", (Path(p).name, Path(p).expanduser().read_bytes(), mimetypes.guess_type(p)[0] or "image/png"))
             for p in image_paths]
    data = {"model": model or IMAGE_MODEL, "prompt": prompt, "size": size}
    if background:
        data["background"] = background
        data["output_format"] = "png"
    if quality:
        data["quality"] = quality
    with _client() as c:
        r = c.post("/images/edits", data=data, files=files)
    if r.status_code >= 400:
        raise RuntimeError(f"改图失败 {r.status_code}: {r.text[:500]}")
    return {"paths": _save(r.json(), job, name), "model": data["model"]}


def _harmonize(ov, bg, strength: float):
    """按场景与产品的平均色调，逐通道增益：色偏全程向场景靠拢，亮度只靠拢一半（避免产品发灰）。"""
    from PIL import Image, ImageStat
    r, g, b, a = ov.split()
    om = ImageStat.Stat(Image.merge("RGB", (r, g, b)), mask=a.point(lambda v: 255 if v > 8 else 0)).mean
    bm = ImageStat.Stat(bg.convert("RGB")).mean
    ol, bl = sum(om) / 3, sum(bm) / 3
    gains = [((bc / bl) / (oc / ol)) ** strength * (bl / ol) ** (strength * 0.5) for bc, oc in zip(bm, om)]
    chans = [c.point(lambda v, k=k: min(255, round(v * k))) for c, k in zip((r, g, b), gains)]
    return Image.merge("RGBA", (*chans, a))


def composite(background: str, overlay: str, job: str, name: str, height: float = 0.45, x: float = 0.5,
              bottom: float = 0.9, rotate: int = 0, shadow: bool = True, harmonize: float = 0.0) -> dict:
    """把透明底产品图（真实像素，不经 AI 重画）贴进场景图，做图生视频首帧或封面底图。
    height=产品高度占画面比例；x=产品中心横向位置；bottom=产品底边位置（0–1）；rotate=顺时针角度（180 = 倒扣在饮水机上）。
    harmonize=0–1：把产品的色温/亮度向场景靠拢（只做整体调色，不改变标签形状），解决“冷色产品贴进暖色场景像贴纸”。"""
    from PIL import Image, ImageDraw, ImageFilter
    bg = Image.open(Path(background).expanduser()).convert("RGBA")
    ov = Image.open(Path(overlay).expanduser()).convert("RGBA")
    if ov.getchannel("A").getextrema()[0] == 255:
        raise ValueError("产品图没有透明背景：请先抠图（或用 image_edit background=transparent 生成透明底版本后核对标签）")
    box = ov.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
    ov = ov.crop(box)
    if rotate:
        ov = ov.rotate(-rotate, expand=True, resample=Image.BICUBIC)
    k = height * bg.height / ov.height
    ov = ov.resize((max(1, round(ov.width * k)), max(1, round(ov.height * k))), Image.LANCZOS)
    left, top = round(x * bg.width - ov.width / 2), round(bottom * bg.height - ov.height)
    if harmonize > 0:
        ov = _harmonize(ov, bg, min(1.0, harmonize))
    if shadow:  # 接地阴影：底边下方一条柔和椭圆
        sh = Image.new("RGBA", bg.size, (0, 0, 0, 0))
        w = ov.width * 0.9
        ImageDraw.Draw(sh).ellipse((left + (ov.width - w) / 2, top + ov.height - ov.height * 0.035,
                                    left + (ov.width + w) / 2, top + ov.height + ov.height * 0.035), fill=(0, 0, 0, 110))
        bg.alpha_composite(sh.filter(ImageFilter.GaussianBlur(ov.width * 0.04)))
    bg.alpha_composite(ov, (left, top))
    d = job_dir(job, "images")
    path, n = d / f"{name}.png", 2
    while path.exists():
        path, n = d / f"{name}_v{n}.png", n + 1
    bg.convert("RGB").save(path)
    return {"path": str(path), "product_box": [left, top, left + ov.width, top + ov.height], "size": list(bg.size),
            "harmonize": harmonize}
