"""成片质检（QA 关卡）与环境自检（doctor）。

QA 阈值依据：
- 响度：抖音/短视频通行 -14 LUFS（±1.5），真峰值 ≤ -1 dBTP，避免平台二次压缩削波。
- 字幕：每条 ≥0.6s 才读得完；中文阅读速度 ≤9 字/秒（Netflix 中文字幕规范）。
- 遮挡：layout_report 中 worst_frame > 0.10 视为挡脸；in_ui_zone 为进入抖音 UI 区。
"""
from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw

from .config import CFG, COMPOSE_DIR, ENV_PATH, FACEBOX, PROJECTS_ROOT, PROXY, REALESRGAN_DIR, ROOT, WHISPER_MODEL
from .media import contact_sheet, probe

MAX_CPS = 9.0
MIN_CAPTION_S = 0.6
FACE_LIMIT = 0.10


def _loudness(video: str) -> dict:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", video, "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True)
    txt = r.stderr
    i = re.findall(r"I:\s+(-?[\d.]+) LUFS", txt)
    tp = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", txt)
    return {"lufs": float(i[-1]) if i else None, "true_peak": float(tp[-1]) if tp else None}


def _black_and_silence(video: str) -> dict:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", video, "-vf", "blackdetect=d=0.3:pic_th=0.95",
                        "-af", "silencedetect=n=-45dB:d=1.5", "-f", "null", "-"], capture_output=True, text=True)
    blacks = re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", r.stderr)
    silences = re.findall(r"silence_start: ([\d.]+)", r.stderr)
    return {"black_segments": [(float(a), float(b)) for a, b in blacks], "long_silences_at": [float(x) for x in silences]}


def safe_zone_sheet(video: str, count: int = 9) -> str:
    """联系表 + 抖音 UI 禁区半透明红色标注（顶部 10%、底部 20%、右侧按钮栏）。"""
    from .compose import BOTTOM_UI, RIGHT_UI, TOP_UI, H, W

    sheet = Path(contact_sheet(video, count))
    img = Image.open(sheet).convert("RGBA")
    cols = min(count, 3)
    cell_w = img.width // cols
    cell_h = cell_w * H // W
    rows = (count + cols - 1) // cols
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    k = cell_w / W
    for i in range(rows * cols):
        x0, y0 = (i % cols) * cell_w, (i // cols) * (cell_h + 28) + 28
        red = (255, 40, 40, 70)
        d.rectangle([x0, y0, x0 + cell_w, y0 + TOP_UI * k], fill=red)
        d.rectangle([x0, y0 + BOTTOM_UI * k, x0 + cell_w, y0 + cell_h], fill=red)
        d.rectangle([x0 + RIGHT_UI[0] * k, y0 + RIGHT_UI[1] * k, x0 + cell_w, y0 + RIGHT_UI[3] * k], fill=red)
    out = sheet.with_name(sheet.stem + "_safezone.jpg")
    Image.alpha_composite(img, ov).convert("RGB").save(out, quality=88)
    return str(out)


def qa_check(video: str) -> dict:
    """对 video_compose 的成片做自动质检；同目录下的 .work/<name>/ 中有排版报告时一并检查。"""
    v = str(Path(video).expanduser())
    if not Path(v).is_file():
        raise FileNotFoundError(v)
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str, fix: str = "", level: str = "fail"):
        checks.append({"check": name, "status": "pass" if ok else level, "detail": detail, **({"fix": fix} if not ok else {})})

    info = probe(v)
    add("分辨率 9:16（1080x1920 或 720x1280）", (info["width"], info["height"]) in ((1080, 1920), (720, 1280)), f"{info['width']}x{info['height']}",
        "用 video_compose 重新导出")
    add("帧率 30fps", abs((info["fps"] or 0) - 30) < 0.1, f"{info['fps']}", "用 video_compose 重新导出", "warn")
    add("有音轨", info["has_audio"], str(info["has_audio"]), "检查原视频音频或 BGM")
    work = Path(v).parent / ".work" / Path(v).stem
    if not work.exists():
        work = Path(v).with_suffix(".work")  # 旧版位置
    props_p = work / "overlay_props.json"
    if props_p.exists():
        props = json.loads(props_p.read_text())
        exp = props["durationMs"] / 1000
        add("时长与工程一致", abs(info["duration"] - exp) < 0.15, f"{info['duration']:.2f}s / 预期 {exp:.2f}s",
            "重新运行 video_compose")
        caps = props.get("captions", [])
        short = [c["text"] for c in caps if (c["endMs"] - c["startMs"]) / 1000 < MIN_CAPTION_S]
        add("字幕停留 ≥0.6s", not short, f"{len(short)} 条过短" + (f"：{short[:3]}" if short else ""),
            "合并相邻短句或调整台词节奏", "warn")
        fast = []
        for c in caps:
            n = len(re.sub(r"[\W_]", "", c["text"]))
            dur = max(0.01, (c["endMs"] - c["startMs"]) / 1000)
            if n / dur > MAX_CPS:
                fast.append(f"{c['text']}（{n / dur:.1f} 字/秒）")
        add("阅读速度 ≤9 字/秒", not fast, f"{len(fast)} 条过快" + (f"：{fast[:3]}" if fast else ""),
            "缩短该句字幕文字（保留关键词），或拆成两句", "warn")
        overlaps = [caps[i]["text"] for i in range(len(caps) - 1) if caps[i]["endMs"] > caps[i + 1]["startMs"] + 20]
        add("字幕时间不重叠", not overlaps, f"{len(overlaps)} 处", "重新运行 subtitles_align")
    else:
        add("排版工程文件", False, "未找到 .work/overlay_props.json（非 video_compose 产物？）", "", "warn")
    rep_p = work / "layout_report.json"
    if rep_p.exists():
        rep = json.loads(rep_p.read_text())
        key = lambda r: r.get("worst_core", r.get("worst_frame")) or 0  # 新报告按五官核心区，旧报告退回整体值
        faces = [f"{r['type']}「{r['text']}」{key(r)}" for r in rep if key(r) > FACE_LIMIT]
        add("字幕/花字不挡五官", not faces, f"{len(faces)} 处最差帧压到五官 >{FACE_LIMIT}" + (f"：{faces[:3]}" if faces else ""),
            "缩短文字、减小 size，或对该卡片设 fixed 位置后人工确认", "warn")
        from .compose import _ui_penalty  # 按实际包围盒重新判定，不依赖报告里的标记字段
        ui = [f"{r['type']}「{r['text']}」y={r['box'][1]}–{r['box'][3]}px" for r in rep
              if r.get("in_ui_zone") or (r.get("box") and _ui_penalty(tuple(r["box"])) > 0)]
        add("不进入抖音 UI 区", not ui, f"{len(ui)} 处" + (f"：{ui[:3]}" if ui else ""), "缩短文字或减小字号后重新合成")
    loud = _loudness(v)
    if loud["lufs"] is not None:
        add("响度 -14±1.5 LUFS", abs(loud["lufs"] + 14) <= 1.5, f"{loud['lufs']} LUFS", "重新合成（loudnorm 会自动处理）")
    if loud["true_peak"] is not None:
        add("真峰值 ≤ -1 dBTP", loud["true_peak"] <= -1.0, f"{loud['true_peak']} dBTP", "降低 bgm_volume 后重新合成", "warn")
    bs = _black_and_silence(v)
    add("无黑场", not bs["black_segments"], f"{bs['black_segments'][:3]}", "检查拼接段或首尾帧", "warn")
    add("无 ≥1.5s 静音", not bs["long_silences_at"], f"{bs['long_silences_at'][:5]}", "检查口播/BGM 是否缺失", "warn")
    sheet = safe_zone_sheet(v)
    verdict = "fail" if any(c["status"] == "fail" for c in checks) else ("warn" if any(c["status"] == "warn" for c in checks) else "pass")
    return {"verdict": verdict, "checks": checks, "review_image": sheet,
            "next": "用看图工具打开 review_image 人工复核（红色为抖音 UI 遮挡区）。verdict=fail 时按 fix 修正后重新合成。"}


# ---------------------------------------------------------------- doctor


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def doctor(network: bool = True, scope: str = "all") -> dict:
    """环境自检。scope：all | cos | image | reapi | speech | edit | almanac（各 MCP 服务只查自己用到的项）。"""
    checks: list[dict] = []
    need = {"all": {"cos", "image", "reapi", "speech", "edit", "almanac"}}.get(scope, {scope})
    if "reapi" in need:
        need = need | {"cos"}  # reAPI 的本地素材经 COS 中转

    def add(name: str, ok: bool, detail: str = "", fix: str = "", level: str = "fail"):
        checks.append({"check": name, "status": "pass" if ok else level, "detail": detail, **({"fix": fix} if not ok else {})})

    cmds = {"uv"} | ({"ffmpeg", "ffprobe"} if need & {"speech", "edit"} else set()) | ({"whisper-cli"} if "speech" in need else set()) \
        | ({"node", "npx"} if "edit" in need else set())
    for b in sorted(cmds):
        add(f"命令 {b}", shutil.which(b) is not None, shutil.which(b) or "未找到", f"brew install {b.replace('whisper-cli', 'whisper-cpp')}")
    if "speech" in need:
        add("whisper 模型", WHISPER_MODEL.exists(), str(WHISPER_MODEL),
            "下载 ggml-large-v3-turbo-q5_0.bin 到 ~/.local/share/whisper/")
    if "edit" in need:
        add("人脸检测 facebox", FACEBOX.exists(), str(FACEBOX), f"cd {ROOT} && swiftc -O tools/facebox.swift -o tools/facebox")
        add("Remotion 依赖", (COMPOSE_DIR / "node_modules/remotion").exists(), "", "cd ~/media-studio-mcp/compose && npm install")
        add("Remotion 浏览器", (COMPOSE_DIR / "node_modules/.remotion/chrome-headless-shell").exists(), "",
            "cd compose && npx remotion browser ensure", "warn")
        add("字体 思源黑体", (COMPOSE_DIR / "public/fonts/SourceHanSansSC-Heavy.otf").exists(), "", "重新下载 SourceHanSansSC-Heavy.otf")
        add("Real-ESRGAN（可选）", (REALESRGAN_DIR / "realesrgan-ncnn-vulkan").exists(), str(REALESRGAN_DIR), "", "warn")
    keys = (("COS_SECRET_ID", "COS_SECRET_KEY", "COS_REGION", "COS_BUCKET") if "cos" in need else ()) \
        + (("IMAGE_API_BASE", "IMAGE_API_KEY") if "image" in need else ()) + (("REAPI_API_KEY",) if "reapi" in need else ())
    if keys:
        add(".env 存在", ENV_PATH.exists(), str(ENV_PATH), "创建 ~/.config/media-mcp/.env")
        if ENV_PATH.exists():
            mode = oct(ENV_PATH.stat().st_mode & 0o777)
            add(".env 权限 600", mode == "0o600", mode, f"chmod 600 {ENV_PATH}")
    for k in keys:
        add(f"配置 {k}", bool(CFG.get(k)), "已配置" if CFG.get(k) else "缺失", f"在 {ENV_PATH} 中填写 {k}")
    cat = ROOT / "catalog.json"
    if "reapi" in need and cat.exists():
        age = (date.today() - date.fromisoformat(json.loads(cat.read_text()).get("updated", "2000-01-01"))).days
        add("价格目录新鲜度 ≤7 天", age <= 7, f"{age} 天前", "调用 refresh_catalog()", "warn")
    if need & {"reapi", "edit", "speech"}:
        free_gb = shutil.disk_usage(Path.home()).free / 1e9
        add("磁盘剩余 ≥20GB", free_gb >= 20, f"{free_gb:.0f} GB", f"清理 {PROJECTS_ROOT} 各项目 产出/ 中的旧原片", "warn")
    if PROXY and need & {"cos", "image", "reapi", "almanac"}:
        m = re.match(r"\w+://([^:/]+):(\d+)", PROXY)
        if m:
            add("本地代理可达", _port_open(m[1], int(m[2])), PROXY, "启动 v2rayN / sing-box")

    if network:
        def reapi_chk():
            from .reapi import balance
            b = balance()
            return ("reAPI 余额", b["usd"] > 1, f"${b['usd']}", "到 reapi.ai 充值")

        def image_chk():
            import httpx
            with httpx.Client(trust_env=False, proxy=CFG.get("IMAGE_PROXY") or None, timeout=20) as c:
                r = c.get(CFG["IMAGE_API_BASE"].rstrip("/") + "/models", headers={"Authorization": f"Bearer {CFG['IMAGE_API_KEY']}"})
            ids = [m["id"] for m in r.json().get("data", [])]
            img = [i for i in ids if "image" in i]
            return ("GPT-image 接口", r.status_code == 200 and bool(img), ", ".join(img) or f"HTTP {r.status_code}",
                    "检查 CLIProxyAPI 服务与 IMAGE_API_KEY")

        def cos_chk():
            from .cos import COS_PREFIX, client
            client().list_objects(Bucket=CFG["COS_BUCKET"], Prefix=COS_PREFIX, MaxKeys=1)
            return ("COS 访问", True, f"{CFG['COS_BUCKET']}/{COS_PREFIX}", "")

        fns = [f for f, s in ((reapi_chk, "reapi"), (image_chk, "image"), (cos_chk, "cos")) if s in need]
        with ThreadPoolExecutor(3) as ex:
            for fut, name in [(ex.submit(f), f.__name__) for f in fns]:
                try:
                    add(*fut.result(timeout=40))
                except Exception as e:
                    add(name, False, str(e)[:200], "检查网络/代理与对应密钥")
    bad = [c for c in checks if c["status"] == "fail"]
    return {"healthy": not bad, "failed": len(bad), "warnings": sum(c["status"] == "warn" for c in checks), "checks": checks}
