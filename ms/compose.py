"""成片合成：人脸感知排版 → Remotion 渲染透明字幕/花字层 → ffmpeg 叠加 + BGM 闪避混音 + 响度标准化。

排版规则（1080x1920 竖屏，抖音；数值取社区实测的稳妥值，抖音无官方像素规范）：
- 顶部 10%（状态栏/搜索栏）、底部 20%（文案/昵称/商品组件）为禁区；右侧 15% 在 40–85% 高度为点赞评论按钮区——硬约束，不进入。
- 字幕默认位于 72% 高度；若与当时画面中的人脸（含发顶余量）重叠，则在候选位置中选重叠最小、离默认最近的位置，
  并带“粘滞”避免逐句跳动。花字/价格卡同理，且字幕会避开同时出现的花字。
"""
from __future__ import annotations

import json
import math
import re
import shlex
import subprocess
from pathlib import Path

from . import jobs
from .config import COMPOSE_DIR, FACEBOX, job_dir
from .media import probe
from .subtitles import parse_srt

W, H, FPS = 1080, 1920, 30
TOP_UI, BOTTOM_UI = 0.10 * H, 0.80 * H        # 顶部 192px、底部 384px 为抖音 UI 区
RIGHT_UI = (0.85 * W, 0.40 * H, W, 0.85 * H)    # 右侧按钮栏
CAPTION_DEFAULT = 0.72
# 可用字体（compose/public/fonts，OFL 开源）：中文别名 → 字体族名
FONTS = {"黑体": "SourceHanSansSC", "快乐体": "ZCOOLKuaiLe", "黄油体": "ZCOOLQingKeHuangYou", "毛笔": "MaShanZheng",
         "手写": "LongCang", "宋体": "ZCOOLXiaoWei"}


def font_family(name: str | None) -> str | None:
    """字体别名或族名 → 族名；空值返回 None（用默认黑体）。"""
    if not name:
        return None
    if name in FONTS:
        return FONTS[name]
    if name in FONTS.values():
        return name
    raise ValueError(f"字体只能是 {list(FONTS)}（或族名 {list(FONTS.values())}）")
CAPTION_CANDIDATES = [round(0.46 + 0.02 * i, 2) for i in range(17)]  # 0.46–0.78：字幕只在下半屏（大特写时宁可压下巴，不上额头/头发）


def detect_faces(video: str, sps: float = 4) -> list[dict]:
    if not FACEBOX.exists():
        raise FileNotFoundError(f"人脸检测程序缺失：{FACEBOX}（运行 swiftc -O tools/facebox.swift -o tools/facebox）")
    out = subprocess.run([str(FACEBOX), str(video), str(sps)], capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    # 视频非 9:16 时按 fill 裁切映射坐标
    vw, vh = d["width"], d["height"]
    s = max(W / vw, H / vh)
    ox, oy = (vw * s - W) / 2, (vh * s - H) / 2
    frames = []
    for f in d["frames"]:
        boxes, cores = [], []
        for x, y, w, h in f["faces"]:
            bx, by, bw, bh = x * vw * s - ox, y * vh * s - oy, w * vw * s, h * vh * s
            cores.append((bx, by, bx + bw, by + bh))  # 五官核心区（Vision 人脸框）
            # 余量区：左右 10%，上方 35%（发顶），下方 25%（下巴/脖子）
            boxes.append((bx - 0.10 * bw, by - 0.35 * bh, bx + 1.10 * bw, by + 1.25 * bh))
        bodies = []
        for x, y, w, h in f.get("bodies", []):
            bodies.append((x * vw * s - ox, y * vh * s - oy, (x + w) * vw * s - ox, (y + h) * vh * s - oy))
        frames.append({"t": f["t"], "boxes": boxes, "cores": cores, "bodies": bodies})
    return frames


def _overlap(a: tuple, b: tuple) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return max(0, w) * max(0, h)


LEFT_X, RIGHT_X = 0.06 * W, 0.84 * W  # 左/右对齐时的外边界（右侧留出抖音按钮区）


def _text_box(text: str, size: int, cy: float, padding: float = 0, max_w: float = 0.86 * W, align: str = "center",
              aspect: float = 0) -> tuple:
    """估算渲染后的包围盒。多行时第 2 行起按 0.62 倍字号（与 Overlay.tsx 的副标题一致）。
    aspect>0 表示图片花字：size 即宽度，高 = size × aspect。【】是关键词高亮标记，不占宽度。"""
    if aspect:
        w, h = size, size * aspect
        x0 = LEFT_X if align == "left" else (RIGHT_X - w if align == "right" else W / 2 - w / 2)
        return (x0, cy - h / 2, x0 + w, cy + h / 2)
    lines = text.replace("【", "").replace("】", "").split("\n")
    total_h, widest = 0.0, 0.0
    for i, l in enumerate(lines):
        s = size if i == 0 else size * 0.62
        w = sum(1.0 if ord(c) > 0x2E80 else 0.58 for c in l) * s
        wraps = max(1, math.ceil(w / (max_w - 2 * padding)))
        widest = max(widest, min(w, max_w - 2 * padding))
        total_h += wraps * s * 1.18 + (s * 0.18 if i else 0)
    w = widest + 2 * padding
    h = total_h + 2 * padding * 0.55
    x0 = LEFT_X if align == "left" else (RIGHT_X - w if align == "right" else W / 2 - w / 2)
    return (x0, cy - h / 2, x0 + w, cy + h / 2)


def _ui_penalty(box: tuple) -> float:
    """进入抖音 UI 区：重罚（等效硬约束，只有在所有候选都越界时才会被选中）。"""
    p = 0.0
    if box[1] < TOP_UI:
        p += 1000 + (TOP_UI - box[1])
    if box[3] > BOTTOM_UI:
        p += 1000 + (box[3] - BOTTOM_UI)
    if _overlap(box, RIGHT_UI) > 0:
        p += 1000 + _overlap(box, RIGHT_UI) / 100
    return p


def _faces_in(frames: list[dict], start: float, end: float) -> list[tuple]:
    sel = [b for f in frames if start - 0.15 <= f["t"] <= end + 0.15 for b in f["boxes"]]
    return sel


def _place(text: str, size: int, start: float, end: float, faces: list[dict], candidates: list[float],
           avoid: list[tuple] = (), prev_y: float | None = None, padding: float = 0,
           default: float | None = None, aligns: tuple = ("center",), aspect: float = 0) -> tuple[float, dict]:
    """在候选高度中选最优：遮挡（均值 + 最差帧）重罚，偏离默认位置按距离连续扣分，带粘滞。"""
    window = [f for f in faces if start - 0.15 <= f["t"] <= end + 0.15] or [{"boxes": [], "cores": []}]
    default = candidates[0] if default is None else default
    best = None
    for frac, align in [(f, a) for f in candidates for a in aligns]:
        cy = frac * H
        box = _text_box(text, size, cy, padding, align=align, aspect=aspect)
        area = (box[2] - box[0]) * (box[3] - box[1])
        # 压五官按全权重，压头发/脖子余量按 0.3 权重
        per_frame = [(sum(_overlap(box, c) for c in f["cores"]) + 0.3 * sum(_overlap(box, b) for b in f["boxes"])) / area
                     for f in window]
        core_frame = [sum(_overlap(box, c) for c in f["cores"]) / area for f in window]
        avg, worst = sum(per_frame) / len(per_frame), max(per_frame)
        score = avg * 60 + worst * 30 + abs(frac - default) * 12 + _ui_penalty(box) + (0 if align == "center" else 1.0)
        score += sum(_overlap(box, a) for a in avoid) / area * 40
        if prev_y is not None and abs(cy - prev_y) > 1:
            score += 0.6  # 粘滞：差不多时不换位置
        if best is None or score < best[0]:
            best = (score, cy, box, avg, worst, align, max(core_frame))
    return best[1], {"face_overlap": round(best[3], 3), "worst_frame": round(best[4], 3),
                     "worst_core": round(best[6], 3), "align": best[5], "box": [round(v) for v in best[2]]}


def _px(y: float) -> float:
    return y * H if y <= 1.0 else y


def plan_layout(base_video: str, captions: list[dict], cards: list[dict], caption_size: int,
                caption_fixed_y: float | None = None, faces: list[dict] | None = None) -> dict:
    faces = faces if faces is not None else detect_faces(base_video)
    placed_cards, report = [], []
    for c in cards:
        size = int(c.get("size", 84))
        pad = size * 0.3 if c.get("bg") else 0
        pref = _px(c.get("y", 0.13)) / H
        align = c.get("align", "center")
        if c.get("fixed"):
            cy = pref * H
            info = {"face_overlap": None, "align": align}
        else:
            cands = [round(0.12 + 0.02 * i, 2) for i in range(31)]  # 0.12–0.72（顶部 10% 为 UI 区）
            best = None
            for k, scale in enumerate((1.0, 0.88, 0.76)):  # 放不下时允许缩小，每档加 1.5 分
                sz = int(size * scale)
                y, inf = _place(c["text"], sz, c["start"], c["end"], faces, cands, padding=pad * scale, default=pref,
                                aligns=("center", "left", "right"), aspect=c.get("_aspect", 0))
                sc = inf["worst_frame"] * 30 + inf["face_overlap"] * 60 + k * 1.5 + abs(y / H - pref) * 12
                if best is None or sc < best[0]:
                    best = (sc, y, inf, sz)
            _, cy, info, size = best
            align = info["align"]
        box = _text_box(c["text"], size, cy, pad * size / int(c.get("size", 84)), align=align, aspect=c.get("_aspect", 0))
        info.setdefault("box", [round(v) for v in box])  # 固定位置的花字也要有位置框（qa 检查 UI 区时读取）
        placed_cards.append({**c, "y": cy, "size": size, "align": align, "_box": box})
        info["in_ui_zone"] = _ui_penalty(box) > 0
        report.append({"type": "card", "text": (c["text"] or Path(c.get("image", "")).name)[:16], "y_frac": round(cy / H, 3), **info})
    out_caps, prev = [], None
    for cap in captions:
        avoid = [pc["_box"] for pc in placed_cards if pc["start"] < cap["end"] and pc["end"] > cap["start"]]
        if caption_fixed_y:  # 品牌模板固定字幕位置
            cy, info = _place(cap["text"], caption_size, cap["start"], cap["end"], faces, [caption_fixed_y], avoid, prev,
                              default=caption_fixed_y)
        else:
            cy, info = _place(cap["text"], caption_size, cap["start"], cap["end"], faces, CAPTION_CANDIDATES, avoid, prev,
                              default=CAPTION_DEFAULT, aligns=("center", "left", "right"))
        prev = cy
        info["in_ui_zone"] = _ui_penalty(_text_box(cap["text"], caption_size, cy, align=info["align"])) > 0
        out_caps.append({**cap, "y": cy, "align": info["align"]})
        report.append({"type": "caption", "text": cap["text"][:16], "y_frac": round(cy / H, 3), **info})
    return {"captions": out_caps, "cards": placed_cards, "report": report, "faces_sampled": len(faces)}


def _title_image(path: str, max_w: float, max_h: float) -> tuple[str, int, int]:
    """封面标题美术字：裁掉透明边，按框等比缩放，返回 data URL 与像素尺寸（1080 宽坐标系）。"""
    import base64
    import io
    from PIL import Image
    im = Image.open(Path(path).expanduser()).convert("RGBA")
    bbox = im.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
    if bbox:
        im = im.crop(bbox)
    k = min(max_w / im.width, max_h / im.height)
    w, h = max(1, round(im.width * k)), max(1, round(im.height * k))
    im = im.resize((w, h), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(), w, h


def plan_cover(faces: list[dict], end: float, title_wh: tuple[int, int] | None, date_wh: tuple[int, int]) -> dict:
    """在封面时段内为标题图与日期块选位置：避开人体（权重 1）、人脸（权重 3），二者互不重叠，尽量不压抖音 UI。"""
    win = [f for f in faces if f["t"] <= end + 0.2] or [{"bodies": [], "boxes": [], "cores": []}]

    def cost(box):
        area = max(1.0, (box[2] - box[0]) * (box[3] - box[1]))
        per = [(0.35 * sum(_overlap(box, b) for b in f.get("bodies", [])) + 2 * sum(_overlap(box, c) for c in f["boxes"])
                + 8 * sum(_overlap(box, c) for c in f["cores"])) / area for f in win]
        ui = 0.0
        if box[1] < TOP_UI:
            ui += 50
        ui += _overlap(box, RIGHT_UI) / area * 20 + max(0.0, box[3] - BOTTOM_UI) / 10
        return sum(per) / len(per) * 60 + max(per) * 30 + ui

    best = None
    ys = [0.11, 0.15, 0.20, 0.26]
    t_opts = [(None, 0.0)] if not title_wh else []
    if title_wh:
        for k, sc in enumerate((1.0, 0.85, 0.72)):
            w, h = title_wh[0] * sc, title_wh[1] * sc
            for side in ("right", "left"):
                x = W * 0.94 - w if side == "right" else W * 0.06
                for y in ys:
                    t_opts.append(((x, y * H, x + w, y * H + h, side, sc), k * 8.0))  # 尽量大
    dw, dh = date_wh
    for tb, tpen in t_opts:
        tc = cost(tb[:4]) + tpen if tb else 0.0
        if best and tc >= best[0]:
            continue
        d_opts = []
        for side in ("left", "right"):
            x = W * 0.06 if side == "left" else W * 0.94 - dw
            for y in ys + [0.32, 0.40]:
                d_opts.append((x, y * H, x + dw, y * H + dh, side, (y - 0.11) * 10))  # 优先靠上
            if tb and tb[4] == side:  # 与标题同侧：放在标题下方
                d_opts.append((x, tb[3] + 0.015 * H, x + dw, tb[3] + 0.015 * H + dh, side, 3.0))
        for db in d_opts:
            c = tc + cost(db[:4]) * 0.8 + db[5]
            if tb and _overlap(db[:4], tb[:4]) > 0:
                c += 1000
            if best is None or c < best[0]:
                best = (c, tb, db)
    _, tb, db = best
    rep = {"date_box": [round(v) for v in db[:4]], "date_align": db[4]}
    if tb:
        rep.update({"title_box": [round(v) for v in tb[:4]], "title_scale": tb[5]})
    rep["score"] = round(best[0], 2)
    return rep


TEMPLATES_DIR = COMPOSE_DIR.parent / "templates"
WEEK = "一二三四五六日"


def list_templates() -> list[str]:
    return sorted(p.stem for p in TEMPLATES_DIR.glob("*.json"))


def load_template(name: str, total: float, cover_date: str | None, cover_title: str | None,
                  cover_image: str | None = None) -> dict:
    """读取 templates/<name>.json，生成 Remotion brand 属性与字幕样式。cover_title 用 | 分隔竖排列（从右到左）。"""
    p = Path(name).expanduser() if name.endswith(".json") else TEMPLATES_DIR / f"{name}.json"
    if not p.is_file():
        raise FileNotFoundError(f"模板不存在：{name}（可用：{', '.join(list_templates())}）")
    t = json.loads(p.read_text())
    brand: dict = {}
    if t.get("watermark"):
        brand["watermark"] = t["watermark"]
    if t.get("footer"):
        brand["footer"] = t["footer"]
    cv = t.get("cover")
    if cv and (cover_date or cover_title or cover_image):
        lines = []
        if cover_date:
            from datetime import date as _date
            from lunar_python import Solar
            d = _date.fromisoformat(cover_date)
            lunar = Solar.fromYmd(d.year, d.month, d.day).getLunar()
            lines = [f"{d.year}年{d.month}月{d.day}日", f"农历{lunar.getMonthInChinese()}月{lunar.getDayInChinese()}", f"星期{WEEK[d.weekday()]}"]
        cols = [c.strip() for c in (cover_title or "").split("|") if c.strip()]
        colors = cv["title_colors"]
        brand["cover"] = {"startMs": 0, "endMs": round(min(cv["seconds"], total) * 1000), "dateLines": lines,
                          "dateColor": cv["date_color"], "dateSize": cv["date_size"], "dateTop": cv["date_top"],
                          "dateLeft": cv["date_left"], "titleColumns": [{"text": c, "color": colors[i % len(colors)]} for i, c in enumerate(cols)],
                          "titleSize": cv["title_size"], "titleTop": cv["title_top"], "titleRight": cv["title_right"],
                          "stroke": cv["stroke"]}
        if cover_image:
            src, tw, th = _title_image(cover_image, cv.get("title_max_w", 0.42) * W, cv.get("title_max_h", 0.5) * H)
            brand["cover"]["titleImage"] = {"src": src, "w": tw, "h": th}
    cap = t.get("caption", {})
    return {"brand": brand, "caption": cap, "card": t.get("card", {}), "bgm": t.get("bgm"), "bgm_volume": t.get("bgm_volume"),
            "output": t.get("output"),
            "cover_seconds": (cv or {}).get("seconds", 0) if brand.get("cover") else 0}


def _normalize_cards(cards: list[dict], hook: dict | str | None) -> list[dict]:
    out = []
    if hook:
        h = {"text": hook} if isinstance(hook, str) else dict(hook)
        out.append({"start": 0.0, "end": float(h.pop("seconds", 3.0)), "y": 0.16, "size": 136, "color": "#FFE14D",
                    "bg": None, "anim": "pop", **h})
    for c in cards or []:
        if c.get("image"):  # 图片花字（GPT-image 透明底贴纸）：size 为宽度 px
            src, w, h = _title_image(c["image"], W * 0.92, H * 0.45)
            c = {"y": 0.16, "anim": "pop", **c, "_src": src, "_aspect": h / w, "text": c.get("text", "")}
            c["size"] = min(int(c.get("size") or 640), w)
            out.append(c)
            continue
        out.append({"y": 0.16, "size": 84, "color": "#FFE14D", "bg": None, "anim": "pop", "stroke": "#111111", **c})
    return out


def compose(video_paths: list[str], job: str, output_name: str, captions: list[dict] | None, srt_path: str | None,
            cards: list[dict] | None, hook: dict | str | None, bgm_path: str | None, bgm_volume: float,
            bgm_start: float, duck: bool, keep_original_audio: bool, caption_size: int, avoid_faces: bool,
            template: str | None = None, cover_date: str | None = None, cover_title: str | None = None,
            cover_image: str | None = None, output_size: str | None = None, caption_style: dict | None = None) -> dict:
    """caption_style：覆盖字幕样式 {"font": 别名/族名, "color", "highlight", "stroke"}，用于每条视频单独的视觉风格。"""
    output_name = re.sub(r'[\\/:*?"<>|\s]+', "_", output_name).strip("._") or "final"
    vids = [str(Path(p).expanduser().resolve()) for p in video_paths]
    for v in vids:
        if not Path(v).is_file():
            raise FileNotFoundError(f"视频不存在：{v}")
    infos = [probe(v) for v in vids]
    total = sum(i["duration"] for i in infos)
    outdir = job_dir(job, "final")
    work = job_dir(job, f"final/.work/{output_name}")  # 中间文件放隐藏目录；渲染成功后删除体积大的 overlay.mov

    # 1) 底片：单段且已是 9:16 时直接用；多段先拼成高质量中间片（仅用于人脸检测与最终叠加）
    if len(vids) == 1:
        base = vids[0]
    else:
        base = str(work / "base_concat.mp4")
        fc = "".join(f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},setsar=1,fps={FPS},format=yuv420p[v{i}];"
                     + (f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}];" if inf["has_audio"]
                        else f"anullsrc=r=48000:cl=stereo,atrim=0:{inf['duration']:.3f}[a{i}];")
                     for i, inf in enumerate(infos))
        fc += "".join(f"[v{i}][a{i}]" for i in range(len(vids))) + f"concat=n={len(vids)}:v=1:a=1[v][a]"
        cmd = ["ffmpeg", "-y", "-v", "error"] + sum((["-i", v] for v in vids), []) + [
            "-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "10", "-preset", "fast",
            "-c:a", "aac", "-b:a", "256k", base]
        subprocess.run(cmd, check=True, capture_output=True)

    # 2) 字幕与花字数据
    if captions is None and srt_path:
        captions = parse_srt(srt_path)
    captions = [{"start": float(c["start"]), "end": float(c["end"]), "text": c["text"]} for c in (captions or [])]
    tpl = load_template(template, total, cover_date, cover_title, cover_image) if template else None
    output_size = output_size or (tpl or {}).get("output") or "1080p"
    if output_size not in ("1080p", "720p"):
        raise ValueError("output_size 只能是 1080p 或 720p")
    style = {"captionSize": caption_size}
    cap_y = None
    if tpl:
        c = tpl["caption"]
        caption_size = int(c.get("size", caption_size))
        style = {"captionSize": caption_size, "captionColor": c.get("color"), "strokeColor": c.get("stroke"),
                 "highlightColor": c.get("highlight"), "strokeRatio": c.get("stroke_ratio")}
        cap_y = c.get("y")
        for cd in [c for c in cards or [] if not c.get("image")]:  # 未指定样式的花字用模板默认
            cd.setdefault("color", tpl["card"].get("color"))
            cd.setdefault("stroke", tpl["card"].get("stroke"))
            cd.setdefault("size", tpl["card"].get("size"))
        if not bgm_path and tpl.get("bgm"):
            bgm_path = tpl["bgm"]
        if tpl.get("bgm_volume") is not None:
            bgm_volume = tpl["bgm_volume"]
    if caption_style:
        cs = caption_style
        style.update({k: v for k, v in {"captionFont": font_family(cs.get("font")), "captionColor": cs.get("color"),
                                         "highlightColor": cs.get("highlight"), "strokeColor": cs.get("stroke")}.items() if v})
    cards_n = _normalize_cards(cards or [], hook)

    # 3) 人脸感知排版
    warnings = []
    faces = None
    if avoid_faces:
        try:
            faces = detect_faces(base)
            layout = plan_layout(base, captions, cards_n, caption_size, cap_y, faces)
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            warnings.append(f"人脸检测不可用，已退回固定排版：{e}")
            avoid_faces = False
    cover_report = None
    if tpl and tpl["brand"].get("cover"):
        cvb = tpl["brand"]["cover"]
        lines = cvb.get("dateLines") or []
        ds = cvb["dateSize"]
        dw = max((sum(1.0 if ord(ch) > 0x2E80 else 0.6 for ch in l) * (ds if i == 0 else ds * 0.68) for i, l in enumerate(lines)), default=0) + 20
        dh = sum((ds if i == 0 else ds * 0.68) * 1.15 for i in range(len(lines))) + 10
        ti = cvb.get("titleImage")
        cover_report = plan_cover(faces or [], cvb["endMs"] / 1000, (ti["w"], ti["h"]) if ti else None, (dw, dh))
        cvb["date"] = {"x": cover_report["date_box"][0], "y": cover_report["date_box"][1], "align": cover_report["date_align"]}
        if ti:
            tb = cover_report["title_box"]
            ti.update({"x": tb[0], "y": tb[1], "w": tb[2] - tb[0], "h": tb[3] - tb[1]})
    if not avoid_faces:
        layout = {"captions": [{**c, "y": (cap_y or CAPTION_DEFAULT) * H} for c in captions],
                  "cards": [{**c, "y": _px(c["y"])} for c in cards_n], "report": []}
        if not layout["report"]:
            layout["report"] = [{"type": t, "text": c["text"][:16], "y_frac": round(c["y"] / H, 3), "face_overlap": None,
                                 "worst_frame": None, "align": "center", "in_ui_zone": False}
                                for t, lst in (("card", layout["cards"]), ("caption", layout["captions"])) for c in lst]
    props = {
        "width": W, "height": H, "fps": FPS, "durationMs": round(total * 1000),
        "captions": [{"text": c["text"], "startMs": round(c["start"] * 1000), "endMs": round(c["end"] * 1000), "y": round(c["y"]),
                      "align": c.get("align", "center")}
                     for c in layout["captions"]],
        "cards": [{k: v for k, v in {"text": c["text"], "startMs": round(c["start"] * 1000), "endMs": round(c["end"] * 1000),
                                     "y": round(c["y"]), "size": c.get("size"), "color": c.get("color"), "bg": c.get("bg"),
                                     "strike": c.get("strike", False), "anim": c.get("anim", "pop"),
                                     "align": c.get("align", "center"),
                                     "stroke": c.get("stroke", "#111111"), "font": font_family(c.get("font")),
                                     **({"image": c["_src"], "imageH": round(c["size"] * c["_aspect"])} if c.get("_src") else {})}.items()}
                  for c in layout["cards"]],
        "style": {k: v for k, v in style.items() if v is not None},
        **({"brand": tpl["brand"]} if tpl and tpl["brand"] else {}),
    }
    props_path = work / "overlay_props.json"
    props_path.write_text(json.dumps(props, ensure_ascii=False, indent=1))
    (work / "layout_report.json").write_text(json.dumps(layout["report"], ensure_ascii=False, indent=1))

    # 4) 渲染 + 合成（后台任务）
    overlay = work / "overlay.mov"
    out = outdir / f"{output_name}.mp4"
    # 720p 等低于 1080 的素材：Lanczos 放大 + 轻锐化（实测 1.5x 时≈原生 1080p），无需单独放大步骤
    sharpen = ",unsharp=5:5:0.5" if min(probe(base)["width"], probe(base)["height"]) < 1000 else ""
    fc = [f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H}{sharpen},setsar=1,fps={FPS},format=yuv420p[base]",
          "[1:v]format=yuva420p[ov]", "[base][ov]overlay=0:0:format=auto:shortest=1" +
          (",scale=720:1280:flags=lanczos" if output_size == "720p" else "") + ",format=yuv420p[vout]"]
    inputs = ["-i", base, "-i", str(overlay)]
    has_voice = probe(base)["has_audio"] and keep_original_audio
    voice = "[0:a]aresample=48000,aformat=channel_layouts=stereo,loudnorm=I=-16:TP=-2:LRA=11[voice]" if has_voice else \
        f"anullsrc=r=48000:cl=stereo,atrim=0:{total:.3f}[voice]"
    fc.append(voice)
    alabel = "voice"
    if bgm_path:
        bgm = Path(bgm_path).expanduser()
        if not bgm.is_file():
            raise FileNotFoundError(f"BGM 不存在：{bgm}")
        inputs += ["-stream_loop", "-1", "-ss", str(bgm_start), "-i", str(bgm)]  # BGM 短于成片时自动循环
        # BGM 与人声都先统一到 -16 LUFS，再乘 bgm_volume（0.45≈-7dB）；轻闪避约 3dB。实测音乐比人声低 ≈8.5dB（旧版 >20dB，听不清）
        fc.append(f"[2:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{total:.3f},asetpts=PTS-STARTPTS,"
                  f"loudnorm=I=-16:TP=-2:LRA=11,afade=t=in:d=0.4,afade=t=out:st={max(0, total - 1.5):.3f}:d=1.5,volume={bgm_volume}[bgm]")
        if duck and has_voice:
            fc += ["[voice]asplit=2[vo][sc]", "[bgm][sc]sidechaincompress=threshold=0.1:ratio=1.6:attack=30:release=400[bgmd]",
                   "[vo][bgmd]amix=inputs=2:duration=first:normalize=0[mix]"]
        else:
            fc.append("[voice][bgm]amix=inputs=2:duration=first:normalize=0[mix]")
        alabel = "mix"
    fc.append(f"[{alabel}]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]")
    ff = ["ffmpeg", "-y", "-v", "error"] + inputs + [
        "-filter_complex", ";".join(fc), "-map", "[vout]", "-map", "[aout]", "-c:v", "libx264", "-preset", "slow",
        "-crf", "16", "-profile:v", "high", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", "-t", f"{total:.3f}", str(out)]
    remotion = ["npx", "remotion", "render", "src/index.ts", "Overlay", str(overlay), "--codec=prores",
                "--prores-profile=4444", "--pixel-format=yuva444p10le", "--image-format=png",
                f"--props={props_path}", "--concurrency=8", "--log=error"]
    script = (jobs.render_locked(f"cd {shlex.quote(str(COMPOSE_DIR))} && {' '.join(shlex.quote(a) for a in remotion)}") + " && "
              f"{' '.join(shlex.quote(a) for a in ff)} && rm -f {shlex.quote(str(overlay))} && echo done")
    res = jobs.start("compose", script, [str(out)], {"eta_hint": "约 1–3 分钟"})
    return res | {"output": str(out), "duration": round(total, 2), "layout_report": str(work / "layout_report.json"),
                  "warnings": warnings,
                  "ui_zone_violations": [r for r in layout["report"] if r.get("in_ui_zone")],
                  "template": template or None, "output_size": output_size, "cover_layout": cover_report,
                  "moved_to_avoid_faces": [] if cap_y else [r for r in layout["report"] if r["type"] == "caption" and (abs(r["y_frac"] - CAPTION_DEFAULT) > 0.01 or r.get("align") != "center")]}
