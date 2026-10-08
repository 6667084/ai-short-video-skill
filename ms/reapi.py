"""reAPI：模型目录 / 方案对比 / 报价单 / 提交 / 任务状态。

付费操作一律两步：*_quote() 生成报价单（不上传、不扣费）→ 用户确认 → submit_quote(quote_id)。
"""
from __future__ import annotations

import json
import math
import os
import re
import secrets
import time
from datetime import datetime
from pathlib import Path

import httpx

from . import cos
from .config import PROJECTS_ROOT, PROXY, REAPI_BASE, ROOT, WORKDIR, cfg, job_dir, log_task
from .media import probe

CATALOG = ROOT / "catalog.json"
QUOTES = WORKDIR / ".quotes"

# ---------------------------------------------------------------- 模型族（已适配字段映射的模型）
# price_mode: 目录价格表里的 mode；res: 允许分辨率；max_dur: 单次最长秒数
FAMILIES: dict[str, dict] = {
    "seedance-2.5": {"slug": "seedance-2-5", "shape": "seedance", "max_dur": 30, "min_dur": 4,
                     "variants": {"standard": ("doubao-seedance-2.5-face", "face", ["480p", "720p", "1080p"]),
                                  "eco": ("doubao-seedance-2.5-eco", "eco", ["720p", "1080p"])},
                     "limits": {"images": 30, "videos": 10, "audios": 10}, "draft": True,
                     "quality": 3, "note": "质量最好；真人口播对口型强；单条最长 30s；可 480p 草稿"},
    "seedance-2.0": {"slug": "seedance-2-0", "shape": "seedance", "max_dur": 15, "min_dur": 4,
                     "variants": {"standard": ("doubao-seedance-2.0-face", "face", ["480p", "720p", "1080p"]),
                                  "fast": ("doubao-seedance-2.0-fast-face", "fastFace", ["480p", "720p"]),
                                  "eco": ("doubao-seedance-2.0-eco", "eco", ["720p", "1080p"])},
                     "limits": {"images": 9, "videos": 3, "audios": 3},
                     "quality": 2, "note": "上一代旗舰，单条最长 15s；fast 档性价比高"},
    "seedance-2.0-mini": {"slug": "seedance-2-0-mini", "shape": "seedance", "max_dur": 15, "min_dur": 4,
                          "variants": {"standard": ("seedance-2.0-mini", "standard", ["480p", "720p"]),
                                       "eco": ("doubao-seedance-2.0-eco-mini", "eco", ["720p", "1080p"])},
                          "limits": {"images": 9, "videos": 3, "audios": 3},
                          "quality": 1,
                          "note": "极低价：适合草稿、空镜/动态背景、已验证的固定 IP；真人出镜人物偏假偏糊，首帧小字也会被重画；单条最长 15s"},
    "wan-3.0": {"slug": "wan-3-0", "shape": "wan", "max_dur": 30, "min_dur": 2,
                "variants": {"standard": ("wan3.0-video", "video", ["480P", "720P", "1080P", "2K", "4K"])},
                "limits": {"images": 10, "videos": 5, "audios": 5, "video_total": 15, "ref_plus_out": 30},
                "quality": 2, "note": "性价比高，单条最长 30s；参考视频总长≤15s 且 参考+输出≤30s；排队较慢"},
    "wan-3.0-prime": {"slug": "wan-3-0-video-prime", "shape": "wan-prime", "max_dur": 30, "min_dur": 2,
                      "variants": {"standard": ("wan3.0-video-prime", "video", ["480P", "720P", "1080P", "2K", "4K"])},
                      "limits": {"images": 10, "videos": 5, "audios": 5, "video_total": 15, "ref_plus_out": 30},
                      "quality": 2, "note": "Wan 3.0 快速档，出片更快，价格约 1.6 倍"},
}


# 用途 → 最低画质档（FAMILIES[*]["quality"]：3 旗舰 / 2 中档 / 1 极低价）。2026-10-04 实测：
# Mini eco 720p 真人口播人物偏假偏糊，首帧模式下标签小字第 0 帧就被重画 → 投放主片不用 Mini。
USE_CASES: dict[str, tuple[int, str]] = {
    "ad_people": (3, "真人出镜/生活场景的带货投放主片：人物是信任载体，需旗舰档 Seedance 2.5（standard 人物最好）；中低档人物偏假偏糊"),
    "talking_head": (2, "单人口播（非投放主片）：至少 Seedance 2.0 / Wan 3.0 中档"),
    "scenery": (1, "无人物特写的空镜、动态背景、产品后期贴图底片：低档即可"),
    "draft": (1, "测脚本/节奏的草稿：低档即可，定稿换档重生成"),
    "series": (1, "已验证效果的固定 IP 系列（如每日早安）：沿用已确认的档位"),
}

# 会改变费用或素材的字段：只能通过正式参数传入（报价据此计算），禁止经 extra 覆盖
PRICED_FIELDS = {"model", "duration", "resolution", "draft", "draft_task_id", "image_urls", "video_urls", "audio_urls",
                 "image_with_roles", "reference_image_urls", "reference_video_urls", "reference_audio_urls",
                 "first_frame_url", "last_frame_url", "file_url", "link_url", "audio_url", "size", "aspect_ratio"}


def _catalog() -> dict:
    return json.loads(CATALOG.read_text()) if CATALOG.exists() else {"models": []}


def _price(slug: str, mode: str, category: str) -> float | None:
    for m in _catalog()["models"]:
        if m["slug"] == slug:
            for p in m["prices"]:
                if p["mode"] == mode and p["category"] == category:
                    return p["usd"]
    return None


def _price_any(slug: str, category: str) -> float | None:
    for m in _catalog()["models"]:
        if m["slug"] == slug:
            for p in m["prices"]:
                if p["category"] == category:
                    return p["usd"]
    return None


def estimate(family: str, variant: str, resolution: str, duration: int, ref_video_sec: float = 0.0,
             finalize: bool = False) -> dict:
    f = FAMILIES[family]
    _, mode, _ = f["variants"][variant]
    if f["shape"] == "seedance":
        res = resolution.lower()
        if finalize:  # 草稿升 1080p：按 1080p 无参考价，计 时长+参考视频秒数
            rate, secs = _price(f["slug"], mode, "1080p_text"), duration + math.ceil(ref_video_sec)
        elif ref_video_sec > 0:
            rate = _price(f["slug"], mode, f"{res}_ref")
            secs = max(math.ceil(ref_video_sec) + duration, math.ceil(5 * duration / 3))
        else:
            rate, secs = _price(f["slug"], mode, f"{res}_text"), duration
    else:  # Wan 价格表：mode=r480/r720/r1080/r2k/r4k，category=video
        rate = _price(f["slug"], "r" + resolution.upper().rstrip("P").lower(), "video")
        secs = duration + math.ceil(ref_video_sec)
    usd = round(rate * secs, 3) if rate is not None else None
    return {"usd": usd, "rate_per_s": rate, "billable_seconds": secs}


def _require_price(est: dict, what: str) -> None:
    if est.get("usd") is None:
        raise ValueError(f"{what} 价格未知（catalog 缺少该档位），请先 refresh_catalog() 再报价")


# ---------------------------------------------------------------- 方案对比


def options(duration: int, ref_video_seconds: float = 0.0, max_usd: float | None = None, use_case: str = "") -> dict:
    """为一条 duration 秒的视频列出各模型/分辨率的费用（含需拆段数、超分建议）。
    use_case（见 USE_CASES）给每行标 fit：低于该用途最低画质档的标“不推荐”，并排到后面。"""
    if use_case and use_case not in USE_CASES:
        raise ValueError(f"未知 use_case={use_case!r}，可选：{', '.join(USE_CASES)}")
    min_q, why = USE_CASES.get(use_case, (0, ""))
    rows = []
    for fam, f in FAMILIES.items():
        for var, (mid, _, ress) in f["variants"].items():
            segs = math.ceil(duration / f["max_dur"])
            seg_dur = math.ceil(duration / segs)
            ref = ref_video_seconds
            notes = [f["note"]]
            lim = f["limits"]
            if ref and lim.get("video_total") and ref > lim["video_total"]:
                notes.append(f"参考视频需压缩到≤{lim['video_total']}s（如 2 倍速快剪版）")
                ref = lim["video_total"]
            if ref and lim.get("ref_plus_out") and ref + seg_dur > lim["ref_plus_out"]:
                notes.append(f"参考+输出须≤{lim['ref_plus_out']}s：带白模时单段最长 {lim['ref_plus_out'] - ref:.0f}s")
                segs = math.ceil(duration / max(1, lim["ref_plus_out"] - ref))
                seg_dur = math.ceil(duration / segs)
            if segs > 1:
                notes.append(f"需拆 {segs} 段生成后拼接（每段≈{seg_dur}s，衔接处可能不连贯）")
            for res in ress:
                e = estimate(fam, var, res, seg_dur, ref / segs if segs > 1 else ref)
                if e["usd"] is None:
                    continue
                total = round(e["usd"] * segs, 2)
                path, up = _path_to_1080(res, duration)
                final = round(total + up, 2)
                if max_usd is not None and final > max_usd:
                    continue
                fit = "推荐" if f["quality"] >= min_q else f"不推荐：{why}"
                rows.append({"family": fam, "variant": var, "model_id": mid, "resolution": res, "segments": segs,
                             "usd": total, "to_1080p": path, "total_1080p_usd": final, "quality": f["quality"],
                             "fit": fit, "notes": "；".join(notes)})
    rows.sort(key=lambda r: (r["fit"] != "推荐", r["total_1080p_usd"]))
    best = {}
    for r in rows:  # 每个模型族/档位：480p+云超分 vs 720p+本地放大，标出更省的路线
        key = (r["family"], r["variant"])
        if r["resolution"].lower() in ("480p", "720p"):
            if key not in best or r["total_1080p_usd"] < best[key]["total_1080p_usd"]:
                best[key] = r
    for r in rows:
        r["cheapest_route_for_model"] = best.get((r["family"], r["variant"])) is r
    return {
        "duration": duration, "ref_video_seconds": ref_video_seconds, "use_case": use_case or None, "options": rows,
        "upscale": {"local_lanczos_720p": "免费、瞬时：720p→1080p 实测≈原生（video_compose 自动处理）",
                    "local_realesrgan": "免费：480p 输入约 25 分钟/30s，皮肤偏磨皮（兜底）",
                    "cloud_enhance_standard": f"${round(duration * (_price('enhance-video-1-0', 'standard', '1080p') or 0), 2)}（enhance-video standard 1080p）",
                    "cloud_enhance_professional": f"${round(duration * (_price('enhance-video-1-0', 'professional', '1080p') or 0), 2)}（enhance-video professional 1080p）",
                    "cloud_topaz_2x": f"${round(duration * (_price('topaz-video-upscaler', 'standard', 'perSecond') or 0), 2)}（Topaz 2x standard）"},
        "rule": "比较 total_1080p_usd：720p 比 480p 贵出的部分 < 云超分费(≈$0.042/s) 时选 720p+本地放大，否则 480p+云端专业超分。"
                "同价位优先 720p（真实细节、无超分伪影）。画面有产品标签/包装文字特写时不走 480p+云超分（AI 超分会臆造字形，如 16.8L→188L），至少原生 720p（如 Seedance 2.5 eco 720p）。"
                "先按用途选画质档（use_case，见 fit），再在推荐档里比价；不要为省钱把真人出镜的投放主片降到 Mini。给用户 2–3 档并说明差价，由用户确认。",
        "catalog_updated": _catalog().get("updated"),
    }


CLOUD_PRO_PER_S = 0.042279  # enhance-video professional 1080p，实测 480p→1080p 画质最佳


def _path_to_1080(resolution: str, duration: float) -> tuple[str, float]:
    """到 1080p 成片的后处理路线与费用。实测：720p→1080p（1.5x）本地 Lanczos+锐化≈原生；480p→1080p（2.25x）需云端专业超分。"""
    r = resolution.lower().rstrip("p")
    if r in ("1080", "2k", "4k"):
        return "原生，无需放大", 0.0
    if r in ("720", "768"):
        return "本地 Lanczos+锐化放大（免费、瞬时，≈原生）", 0.0
    rate = _price("enhance-video-1-0", "professional", "1080p") or CLOUD_PRO_PER_S
    return "云端 enhance 专业档超分", round(rate * math.ceil(duration), 2)


def models_summary() -> list[dict]:
    out = []
    for m in _catalog()["models"]:
        secs = [p["usd"] for p in m["prices"] if p["unit"].endswith("second")]
        dur = m["params"].get("duration", {}).get("spec", "")
        out.append({"slug": m["slug"], "ids": m["model_ids"][:4], "per_second_usd": [min(secs), max(secs)] if secs else None,
                    "duration": re.sub(r".*range ", "", dur) if dur else None,
                    "adapted": any(f["slug"] == m["slug"] for f in FAMILIES.values()),
                    "summary": (m["summary"] or "")[:140]})
    return out


# ---------------------------------------------------------------- 报价单


def _qpath(qid: str) -> Path:
    QUOTES.mkdir(parents=True, exist_ok=True)
    return QUOTES / f"{qid}.json"


def _save_quote(kind: str, job: str, request: dict, est: dict, summary: str) -> dict:
    qid = f"q_{datetime.now():%m%d%H%M%S}_{secrets.token_hex(3)}"
    q = {"id": qid, "kind": kind, "job": job, "request": request, "estimate": est, "summary": summary,
         "created": time.time(), "status": "pending"}
    _qpath(qid).write_text(json.dumps(q, ensure_ascii=False, indent=1))
    return {"quote_id": qid, "summary": summary, "estimate_usd": est.get("usd"),
            "next": "把 summary 和 estimate_usd 给用户确认；确认后调用 submit_quote(quote_id)。"}


def _ref_seconds(videos: list[str]) -> float:
    total = 0.0
    for v in videos:
        if re.match(r"^https?://", v):
            continue
        total += probe(v)["duration"]
    return total


def quote_video(prompt: str, job: str, family: str, variant: str, duration: int, size: str, resolution: str,
                images: list[str], videos: list[str], audios: list[str], first_frame: str | None,
                last_frame: str | None, draft: bool, generate_audio: bool, seed: int | None, extra: dict) -> dict:
    if family not in FAMILIES:
        raise ValueError(f"family 只能是 {list(FAMILIES)}（其他模型用 quote_raw）")
    f = FAMILIES[family]
    if variant not in f["variants"]:
        raise ValueError(f"{family} 的 variant 只能是 {list(f['variants'])}")
    mid, _, ress = f["variants"][variant]
    res = resolution.upper() if f["shape"].startswith("wan") else resolution.lower()
    if res not in ress:
        raise ValueError(f"{family}/{variant} 分辨率只能是 {ress}")
    if not (f["min_dur"] <= duration <= f["max_dur"]):
        raise ValueError(f"{family} 时长范围 {f['min_dur']}–{f['max_dur']}s")
    lim = f["limits"]
    for k, items in (("images", images), ("videos", videos), ("audios", audios)):
        if len(items) > lim[k]:
            raise ValueError(f"{family} 最多 {lim[k]} 个 {k}")
    for p in images + videos + audios + [x for x in (first_frame, last_frame) if x]:
        if not re.match(r"^https?://", p) and not Path(p).expanduser().is_file():
            raise FileNotFoundError(p)
    ref = _ref_seconds(videos)
    if lim.get("video_total") and ref > lim["video_total"]:
        raise ValueError(f"{family} 参考视频总长需≤{lim['video_total']}s（当前 {ref:.1f}s）")
    if lim.get("ref_plus_out") and ref + duration > lim["ref_plus_out"]:
        raise ValueError(f"{family} 参考视频+输出需≤{lim['ref_plus_out']}s（当前 {ref:.1f}+{duration}）")
    if draft and not f.get("draft"):
        raise ValueError("只有 seedance-2.5 standard 支持 draft")
    banned = PRICED_FIELDS & set(extra or {})
    if banned:
        raise ValueError(f"extra 不能包含影响计费或素材的字段 {sorted(banned)}——请用对应参数传入，以保证报价与实际扣费一致")
    est = estimate(family, variant, res, duration, ref)
    _require_price(est, f"{family}/{variant} {res}")
    alts = []
    for r2 in ress:
        if r2 == res:
            continue
        e2 = estimate(family, variant, r2, duration, ref)
        if e2["usd"] is not None:
            path, up = _path_to_1080(r2, duration)
            alts.append(f"{r2}：生成 ${e2['usd']} + {path} ${up} = 1080p 成片 ${round(e2['usd'] + up, 2)}")
    path0, up0 = _path_to_1080(res, duration)
    est["to_1080p"] = path0
    est["total_1080p_usd"] = round((est["usd"] or 0) + up0, 2)
    req = {"family": family, "variant": variant, "model_id": mid, "prompt": prompt, "duration": duration, "size": size,
           "resolution": res, "images": images, "videos": videos, "audios": audios, "first_frame": first_frame,
           "last_frame": last_frame, "draft": draft, "generate_audio": generate_audio, "seed": seed, "extra": extra,
           "ref_video_seconds": ref}
    refs = f"{len(images)} 图/{len(videos)} 视频({ref:.0f}s)/{len(audios)} 音频"
    summary = (f"{family}/{variant}（{mid}）{res} {duration}s {size}{' 草稿' if draft else ''}；参考 {refs}；"
               f"计费 {est['billable_seconds']}s × ${est['rate_per_s']}/s ≈ ${est['usd']}；"
               f"到 1080p：{path0}，成片总价 ≈ ${est['total_1080p_usd']}")
    out = _save_quote("video", job, req, est, summary)
    out["alternatives"] = alts
    out["next"] = "把 summary 与 alternatives（其他分辨率的 1080p 成片总价）一起给用户，由用户确认分辨率与金额后再 submit_quote。"
    return out


def quote_finalize(draft_task_id: str, job: str) -> dict:
    rec = None
    for line in (job_dir(job) / "tasks.jsonl").read_text().splitlines() if (job_dir(job) / "tasks.jsonl").exists() else []:
        r = json.loads(line)
        if r.get("task_id") == draft_task_id and r.get("kind") == "video":
            rec = r
    dur = rec["request"]["duration"] if rec else 30
    ref = rec.get("ref_video_seconds", 0) if rec else 0
    est = estimate("seedance-2.5", "standard", "1080p", dur, ref, finalize=True)
    summary = f"Seedance 2.5 草稿 {draft_task_id} → 1080p 成片；计费 {est['billable_seconds']}s × ${est['rate_per_s']}/s ≈ ${est['usd']}"
    if ref:
        summary += "（注意：带参考视频的草稿升级会重复计参考秒数，通常比直接提交 1080p 更贵）"
    return _save_quote("finalize", job, {"draft_task_id": draft_task_id}, est, summary)


def quote_music(style: str, job: str, title: str, duration: int, instrumental: bool, lyrics: str | None,
                negative_tags: str | None) -> dict:
    body = {"model": "suno-music", "version": "V6", "custom_mode": True, "instrumental": instrumental,
            "style": style, "title": title, "duration": max(10, duration)}
    if not instrumental:
        if not lyrics:
            raise ValueError("非纯音乐需要 lyrics")
        body["prompt"] = lyrics
    if negative_tags:
        body["negative_tags"] = negative_tags
    usd = _price_any("suno-v6", "generate") or 0.066
    return _save_quote("music", job, {"body": body}, {"usd": usd},
                       f"Suno V6 {'纯音乐' if instrumental else '带人声'} {duration}s × 2 条，风格「{style[:60]}」≈ ${usd}")


def quote_cloud_upscale(video: str, job: str, provider: str, tier: str, resolution: str) -> dict:
    try:
        dur = probe(video)["duration"]  # ffprobe 也能直接读 http(s) URL
    except Exception as e:
        raise ValueError(f"无法读取源视频时长（{e}）；请确认路径/URL 可访问") from e
    if provider == "enhance":
        rate = _price("enhance-video-1-0", tier, resolution.lower())
        body = {"model": "enhance-video-1.0", "tool_version": tier, "scene": "aigc", "resolution": resolution.lower(),
                "bitrate_level": "high"}
    elif provider == "topaz":
        rate = _price("topaz-video-upscaler", tier, "perSecond")
        body = {"model": "topaz-video-upscaler", "upscale_factor": "2"}
    else:
        raise ValueError("provider 只能是 enhance / topaz")
    if rate is None:
        raise ValueError(f"{provider}/{tier}/{resolution} 价格未知，请 refresh_catalog() 或换档位")
    usd = round(rate * math.ceil(dur), 3)
    return _save_quote("upscale", job, {"video": video, "body": body}, {"usd": usd, "rate_per_s": rate},
                       f"云端超分 {provider}/{tier} → {resolution}，源 {dur:.1f}s × ${rate}/s ≈ ${usd}")


def quote_raw(model_id: str, params: dict, job: str, estimate_usd: float | None) -> dict:
    return _save_quote("raw", job, {"body": {"model": model_id, **params}}, {"usd": estimate_usd},
                       f"原始请求 {model_id}（参数 {list(params)}），预估 ${estimate_usd if estimate_usd is not None else '未知'}")


def list_quotes(job: str | None = None, status: str | None = "pending") -> list[dict]:
    QUOTES.mkdir(parents=True, exist_ok=True)
    out = []
    for p in sorted(QUOTES.glob("q_*.json")):
        q = json.loads(p.read_text())
        if (job and q["job"] != job) or (status and q["status"] != status):
            continue
        out.append({k: q[k] for k in ("id", "kind", "job", "summary", "status")} | {"usd": q["estimate"].get("usd")})
    return out


# ---------------------------------------------------------------- 提交


def _client() -> httpx.Client:
    return httpx.Client(base_url=REAPI_BASE, trust_env=False, proxy=PROXY,
                        headers={"Authorization": f"Bearer {cfg('REAPI_API_KEY')}"}, timeout=httpx.Timeout(120, connect=30))


def _post(path: str, body: dict) -> dict:
    with _client() as c:
        r = c.post(path, json=body)
    if r.status_code >= 400:
        raise RuntimeError(f"reAPI {r.status_code}: {r.text[:800]}")
    return r.json()


def _build_video_body(q: dict) -> dict:
    r, job = q["request"], q["job"]
    f = FAMILIES[r["family"]]
    imgs = [cos.as_url(x, job) for x in r["images"]]
    vids = [cos.as_url(x, job) for x in r["videos"]]
    auds = [cos.as_url(x, job) for x in r["audios"]]
    ff = cos.as_url(r["first_frame"], job) if r["first_frame"] else None
    lf = cos.as_url(r["last_frame"], job) if r["last_frame"] else None
    body: dict = {"model": r["model_id"], "prompt": r["prompt"], "duration": r["duration"], "resolution": r["resolution"]}
    if f["shape"] == "seedance":
        body["generate_audio"] = r["generate_audio"]
        body["return_last_frame"] = True
        if ff or lf:
            body["image_with_roles"] = [{"url": ff, "role": "first_frame"}] + ([{"url": lf, "role": "last_frame"}] if lf else [])
            body["size"] = "adaptive"
        else:
            body["size"] = r["size"]
            if imgs:
                body["image_urls"] = imgs
        if r["draft"]:
            body["draft"] = True
    elif f["shape"] == "wan":
        body["audio"] = r["generate_audio"]
        if ff or lf:
            body["image_with_roles"] = [{"url": ff, "role": "first_frame"}] + ([{"url": lf, "role": "last_frame"}] if lf else [])
        else:
            body["size"] = r["size"]
            if imgs:
                body["image_urls"] = imgs
                body["generation_type"] = "reference"
    else:  # wan-prime
        body["audio"] = r["generate_audio"]
        if ff:
            body["first_frame_url"] = ff
        if lf:
            body["last_frame_url"] = lf
        if not (ff or lf):
            body["aspect_ratio"] = r["size"]
        if imgs:
            body["reference_image_urls"] = imgs
    key_v, key_a = ("reference_video_urls", "reference_audio_urls") if f["shape"] == "wan-prime" else ("video_urls", "audio_urls")
    if vids:
        body[key_v] = vids
    if auds:
        body[key_a] = auds
    if r["seed"] is not None:
        body["seed"] = r["seed"]
    if r["model_id"] == "seedance-2.0-mini":  # 标准版 Mini 用 reference_* / aspect_ratio 字段名（eco-mini 仍是 Seedance 原生字段）
        for a, b in (("image_urls", "reference_image_urls"), ("video_urls", "reference_video_urls"),
                     ("audio_urls", "reference_audio_urls"), ("size", "aspect_ratio")):
            if a in body:
                body[b] = body.pop(a)
        for it in body.pop("image_with_roles", []):
            body[f"{it['role']}_url"] = it["url"]
        body.pop("return_last_frame", None)
    body.update(r["extra"] or {})
    return body


def _upload_paths_in(obj, job: str):
    """raw 请求：把 *_url / *_urls 字段里的本地路径自动上传。"""
    if isinstance(obj, dict):
        return {k: (cos.as_url(v, job) if k.endswith("_url") and isinstance(v, str) else
                    [cos.as_url(x, job) for x in v] if k.endswith("_urls") and isinstance(v, list) else
                    _upload_paths_in(v, job)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_upload_paths_in(x, job) for x in obj]
    return obj


def submit(quote_id: str, allow_resubmit: bool = False) -> dict:
    p = _qpath(quote_id)
    if not p.exists():
        raise ValueError(f"报价单 {quote_id} 不存在；用 list_quotes() 查看待确认报价")
    lock = p.with_suffix(".lock")
    try:  # 原子锁：防止同一报价被并发提交两次（重复扣费）
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        raise ValueError(f"报价单 {quote_id} 正在提交中或已提交，请勿重复调用") from None
    try:
        return _submit_locked(p, quote_id, allow_resubmit)
    finally:
        lock.unlink(missing_ok=True)


def _submit_locked(p: Path, quote_id: str, allow_resubmit: bool) -> dict:
    q = json.loads(p.read_text())
    if q["status"] not in ("pending", "error") and not allow_resubmit:
        raise ValueError(f"报价单已是 {q['status']}（task {q.get('task_id')}），重复提交需 allow_resubmit=True")
    if time.time() - q["created"] > 7 * 86400:
        raise ValueError("报价单已超过 7 天（价格可能已变），请重新报价")
    need = q["estimate"].get("usd") or 0
    try:
        bal = balance()["usd"]
    except Exception:
        bal = None
    if bal is not None and need > bal:
        raise ValueError(f"余额不足：需要约 ${need}，当前余额 ${bal}。请先在 reapi.ai 充值")
    job, kind = q["job"], q["kind"]
    if kind == "video":
        body, path = _build_video_body(q), "/videos/generations"
    elif kind == "finalize":
        body = {"model": FAMILIES["seedance-2.5"]["variants"]["standard"][0], "draft_task_id": q["request"]["draft_task_id"],
                "resolution": "1080p", "return_last_frame": True, "bitrate_mode": "high"}
        path = "/videos/generations"
    elif kind == "music":
        body, path = q["request"]["body"], "/audio/generations"
    elif kind == "upscale":
        body = {**q["request"]["body"], "video_url": cos.as_url(q["request"]["video"], job)}
        path = "/videos/generations"
    elif kind == "raw":
        body, path = _upload_paths_in(q["request"]["body"], job), "/videos/generations"
    else:
        raise ValueError(kind)
    try:
        resp = _post(path, body)
    except Exception as e:  # 提交失败不扣费：记录原因，报价单保持可重试
        q.update(status="error", last_error=str(e)[:500], last_error_at=time.time())
        p.write_text(json.dumps(q, ensure_ascii=False, indent=1))
        raise
    q.update(status="submitted", task_id=resp.get("id"), submitted=time.time())
    p.write_text(json.dumps(q, ensure_ascii=False, indent=1))
    log_task(job, {"kind": kind, "task_id": resp.get("id"), "quote_id": quote_id, "request": q["request"],
                   "ref_video_seconds": q["request"].get("ref_video_seconds", 0), "estimate_usd": q["estimate"].get("usd")})
    return {"task_id": resp.get("id"), "status": resp.get("status"), "quote_id": quote_id, "estimate_usd": q["estimate"].get("usd"),
            "next": "调用 task_status(task_id, job, wait_s=90) 轮询；完成后自动下载到 raw/"}


# ---------------------------------------------------------------- 任务状态


def _download(url: str, dest: Path) -> None:
    last: Exception | None = None
    for proxy in (None, PROXY):
        try:
            part = dest.with_name(dest.name + ".part")
            with httpx.Client(trust_env=False, proxy=proxy, timeout=httpx.Timeout(300, connect=20), follow_redirects=True) as c:
                with c.stream("GET", url) as r:
                    r.raise_for_status()
                    with open(part, "wb") as fh:
                        for chunk in r.iter_bytes(1 << 20):
                            fh.write(chunk)
            part.replace(dest)  # 原子落盘：中断不会留下“看似完整”的残缺文件
            return
        except Exception as e:  # 直连失败再走代理
            last = e
    raise RuntimeError(f"下载失败 {url}: {last}")


def task_status(task_id: str, job: str, wait_s: int = 90, name: str | None = None) -> dict:
    deadline = time.time() + max(0, min(wait_s, 110))  # 适配 Hermes 等 120s 工具超时
    with _client() as c:
        while True:
            r = c.get(f"/tasks/{task_id}")
            if r.status_code >= 400:
                raise ValueError(f"查询任务失败 {r.status_code}: {r.text[:300]}（task_id 是否正确？）")
            d = r.json()
            if d.get("status") in ("completed", "failed") or time.time() >= deadline:
                break
            time.sleep(5)
    status = d.get("status")
    res: dict = {"task_id": task_id, "status": status}
    if status == "processing":
        res["next"] = "仍在生成，稍后再次调用 task_status"
    if status == "failed":
        res["error"] = d.get("error")
        res["refund"] = "失败任务 reAPI 自动全额退款"
        log_task(job, {"kind": "failed", "task_id": task_id, "error": d.get("error")})
    if status == "completed":
        out = d.get("output") or {}
        raw = job_dir(job, "raw")
        stem = name or task_id[-10:]
        urls = [(u, ".mp4", "") for u in out.get("video_urls", [])] + [(u, ".mp3", "") for u in out.get("audio_urls", [])]
        if out.get("last_frame_url"):
            urls.append((out["last_frame_url"], ".jpg", "_lastframe"))
        files = []
        for i, (u, ext, tag) in enumerate(urls):
            ext = Path(u.split("?")[0]).suffix or ext
            suffix = tag or (f"_{i + 1}" if len(urls) > 1 else "")
            dest = raw / f"{stem}{suffix}{ext}"
            if not dest.exists():
                _download(u, dest)
            files.append(str(dest))
            cos.remember_source(dest, u, job)  # 之后对该文件做云端处理时直接用 reAPI CDN 原链接
        credits = (d.get("usage") or {}).get("credits")
        res.update(files=files, credits=credits, usd=round(credits / 1000, 3) if credits else None)
        if not any(json.loads(l).get("kind") == "result" and json.loads(l).get("task_id") == task_id
                   for l in ((job_dir(job) / "tasks.jsonl").read_text().splitlines()
                             if (job_dir(job) / "tasks.jsonl").exists() else []) if l.strip()):
            log_task(job, {"kind": "result", "task_id": task_id, "files": files, "credits": credits})
    return res


def balance() -> dict:
    with _client() as c:
        b = c.get("/balance").json()
    return {**b, "usd": round(b.get("balance", 0) / 1000, 3)}


def spend_summary(job: str | None = None) -> dict:
    """按 job 汇总已完成任务的实际花费（来自 tasks.jsonl 的 credits）。"""
    if job:
        jobs = [job_dir(job)]
    else:  # 系统临时目录 + 每个项目文件夹（产出/ 或项目根）
        jobs = [p for p in WORKDIR.iterdir() if p.is_dir() and not p.name.startswith(".")]
        if PROJECTS_ROOT.exists():
            jobs += [p / "产出" if (p / "产出").is_dir() else p for p in PROJECTS_ROOT.iterdir() if p.is_dir()]
    out, total = {}, 0
    for d in jobs:
        f = d / "tasks.jsonl"
        if not f.exists():
            continue
        per_task: dict[str, int] = {}  # 同一任务可能被多次查询并记录，按 task_id 去重
        for l in f.read_text().splitlines():
            r = json.loads(l) if l.strip() else {}
            if r.get("kind") == "result" and r.get("task_id"):  # v2 记录在 credits，v1 记录在 usage.credits
                per_task[r["task_id"]] = r.get("credits") or (r.get("usage") or {}).get("credits") or 0
        credits = sum(per_task.values())
        if credits:
            out[d.parent.name if d.name == "产出" else d.name] = round(credits / 1000, 3)
            total += credits
    return {"by_job_usd": out, "total_usd": round(total / 1000, 3)}
