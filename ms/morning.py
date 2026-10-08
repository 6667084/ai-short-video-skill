"""早安/节气类轻量视频：日期节气天气上下文、edge-tts 配音（逐词时间戳 → 字幕）、静态图运镜成片。全部免费。"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

from .config import PROXY, ROOT, SETTINGS, job_dir
from .media import probe, run

TERMS = json.loads((ROOT / "ms" / "data" / "solar_terms.json").read_text())
WEEK = "一二三四五六日"
WMO = {0: "晴", 1: "晴间多云", 2: "多云", 3: "阴", 45: "雾", 48: "冻雾", 51: "毛毛雨", 53: "毛毛雨", 55: "毛毛雨",
       56: "冻毛毛雨", 57: "冻毛毛雨", 61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "冻雨", 71: "小雪", 73: "中雪",
       75: "大雪", 77: "雪粒", 80: "阵雨", 81: "阵雨", 82: "强阵雨", 85: "阵雪", 86: "强阵雪", 95: "雷阵雨", 96: "雷阵雨伴冰雹",
       99: "强雷阵雨伴冰雹"}
MORNING = SETTINGS.get("morning", {})
DEFAULT_VOICE = MORNING.get("voice", "zh-CN-XiaoxiaoNeural")


def _client() -> httpx.Client:
    return httpx.Client(proxy=PROXY, trust_env=False, timeout=30)


def _pick_date(d: str | None) -> date:
    if d:
        return date.fromisoformat(d)
    now = datetime.now()
    return (now + timedelta(days=1)).date() if now.hour >= 15 else now.date()  # 下午以后默认做明天的


def _calendar(day: date) -> dict:
    from lunar_python import Solar
    solar = Solar.fromYmd(day.year, day.month, day.day)
    lunar = solar.getLunar()
    today_term = lunar.getJieQi() or ""
    prev = lunar.getPrevJieQi()
    nxt = Solar.fromYmd(*map(int, (day + timedelta(days=1)).isoformat().split("-"))).getLunar().getNextJieQi() if today_term \
        else lunar.getNextJieQi()  # 节气当天 getNextJieQi 会返回当天本身
    cur = today_term or prev.getName()
    nxt_day = date.fromisoformat(nxt.getSolar().toYmd())
    festivals = [*solar.getFestivals(), *lunar.getFestivals()]
    return {
        "date": day.isoformat(), "weekday": f"星期{WEEK[day.weekday()]}",
        "lunar": f"农历{lunar.getMonthInChinese()}月{lunar.getDayInChinese()}",
        "festivals": festivals,
        "is_term_day": bool(today_term), "term": cur,
        "term_started": today_term and day.isoformat() or prev.getSolar().toYmd(),
        "next_term": nxt.getName(), "days_to_next_term": (nxt_day - day).days,
        "phenology": lunar.getWuHou(), "term_info": TERMS.get(cur, {}),
    }


def _geocode(c: httpx.Client, city: str) -> dict:
    r = c.get("https://geocoding-api.open-meteo.com/v1/search", params={"name": city, "count": 1, "language": "zh"}).json()
    if not r.get("results"):
        raise ValueError(f"找不到城市：{city}（可改用更常见的名称，如“北京”“广州”）")
    g = r["results"][0]
    return {"name": g["name"], "lat": g["latitude"], "lon": g["longitude"], "admin": g.get("admin1", "")}


def _weather(day: date, city: str) -> dict:
    with _client() as c:
        g = _geocode(c, city)
        daily = ("weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,uv_index_max,"
                 "wind_speed_10m_max,relative_humidity_2m_mean,sunrise")
        f = c.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": g["lat"], "longitude": g["lon"], "timezone": "Asia/Shanghai", "daily": daily,
            "start_date": day.isoformat(), "end_date": day.isoformat()}).json()
        if "daily" not in f:
            raise ValueError(f"天气预报不可用：{f.get('reason', f)}（仅支持未来约 16 天）")
        d = {k: v[0] for k, v in f["daily"].items()}
        pm = None
        try:
            a = c.get("https://air-quality-api.open-meteo.com/v1/air-quality", params={
                "latitude": g["lat"], "longitude": g["lon"], "timezone": "Asia/Shanghai", "hourly": "pm2_5",
                "start_date": day.isoformat(), "end_date": day.isoformat()}).json()
            vals = [v for v in a["hourly"]["pm2_5"][7:20] if v is not None]  # 白天 7–19 点
            pm = round(max(vals)) if vals else None
        except Exception:
            pass
    tmax, tmin = d["temperature_2m_max"], d["temperature_2m_min"]
    return {"city": g["name"], "province": g["admin"], "weather": WMO.get(d["weather_code"], "未知"),
            "temp_max": round(tmax), "temp_min": round(tmin), "temp_range": round(tmax - tmin),
            "rain_prob": d["precipitation_probability_max"], "uv_max": d["uv_index_max"],
            "wind_max_kmh": d["wind_speed_10m_max"], "humidity": d["relative_humidity_2m_mean"],
            "sunrise": (d.get("sunrise") or "")[-5:], "pm25_day_max": pm, "source": "Open-Meteo 模型预报（仅供参考）"}


def _alerts(w: dict) -> list[str]:
    a = []
    if w["temp_range"] >= 10:
        a.append(f"昼夜温差{w['temp_range']}℃，早晚记得添衣")
    if (w["rain_prob"] or 0) >= 50 or "雨" in w["weather"]:
        a.append("今天可能有雨，出门带伞、注意路滑")
    if "雪" in w["weather"]:
        a.append("有降雪，出行注意防滑保暖")
    if w["temp_max"] >= 33:
        a.append("高温天气，避开正午外出，及时补水")
    if w["temp_min"] <= 5:
        a.append("早晚气温低，注意头颈手脚保暖")
    if (w["uv_max"] or 0) >= 6:
        a.append("紫外线较强，外出注意防晒")
    if (w["humidity"] or 100) <= 35:
        a.append("空气干燥，多喝温水")
    if (w["wind_max_kmh"] or 0) >= 38:
        a.append("风力较大，远离广告牌和高空悬挂物")
    if w.get("pm25_day_max") and w["pm25_day_max"] > 75:
        a.append("空气质量欠佳，减少户外运动，外出可戴口罩")
    return a


def context(day: str | None, city: str | None) -> dict:
    d = _pick_date(day)
    city = city or MORNING.get("city")
    out = {"calendar": _calendar(d)}
    if city:
        try:
            w = _weather(d, city)
            out["weather"], out["weather_alerts"] = w, _alerts(w)
        except Exception as e:
            out["weather_error"] = str(e)
    else:
        out["weather_error"] = "未设置城市：传 city，或在 ~/.config/media-mcp/settings.json 的 morning.city 中设置默认城市"
    cal = out["calendar"]
    out["mascot"] = MORNING.get("mascot")
    out["writing_guide"] = ("口播 60–90 字（约 15–22 秒）：早安问候 + 日期/节气一句 + 今日天气一句 + 2–3 条贴合天气与节气的生活提醒 + 温暖收尾。"
                            "优先用 weather_alerts，再用 term_info.tips；只给一般生活建议，不提药物/疗效/偏方。"
                            f"节气相关：今天{'正是' + cal['term'] if cal['is_term_day'] else '处于' + cal['term']}，"
                            f"距{cal['next_term']}还有{cal['days_to_next_term']}天。")
    return out


# ---------------- TTS ----------------

def _split_line(line: str) -> tuple[str, str]:
    show, _, say = line.partition("|")
    return show.strip(), (say or show).strip()


async def _synth(text: str, voice: str, rate: str, pitch: str, out: Path) -> list[tuple[float, float, str]]:
    import edge_tts
    words = []
    comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, proxy=PROXY, boundary="WordBoundary")
    with open(out, "wb") as f:
        async for ch in comm.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                words.append((ch["offset"] / 1e7, (ch["offset"] + ch["duration"]) / 1e7, ch["text"]))
    return words


def tts(lines: list[str], job: str, name: str, voice: str | None, rate: str, pitch: str) -> dict:
    if not lines:
        raise ValueError("lines 不能为空")
    pairs = [_split_line(x) for x in lines]
    spoken = []
    for _, say in pairs:  # 每行末尾补停顿标点，让断句与字幕一致
        spoken.append(say if re.search(r"[，。！？、；,.!?;…]$", say) else say + "，")
    full = "".join(spoken)
    out = job_dir(job, "audio") / f"{name}.mp3"
    words = asyncio.run(_synth(full, voice or DEFAULT_VOICE, rate, pitch, out))
    if not words:
        raise RuntimeError("TTS 未返回逐词时间戳（edge-tts 服务异常，可稍后重试）")
    # 逐词时间 → 字符区间
    spans, cur = [], 0
    for s, e, w in words:
        i = full.find(w, cur)
        if i < 0:
            continue
        spans.append((i, i + len(w), s, e))
        cur = i + len(w)
    cues, pos = [], 0
    for (show, _), say in zip(pairs, spoken):
        a, b = pos, pos + len(say)
        pos = b
        hit = [x for x in spans if x[0] < b and x[1] > a]
        if not hit:
            continue
        cues.append({"start": round(hit[0][2], 2), "end": round(hit[-1][3], 2),
                     "text": re.sub(r"[，。、；,.;]+$", "", show)})
    for c, n in zip(cues, cues[1:]):  # 字幕延续到下一句开始前，避免闪断
        c["end"] = round(min(n["start"] - 0.05, c["end"] + 0.6), 2)
    dur = probe(out)["duration"]
    if cues:
        cues[-1]["end"] = round(min(dur, cues[-1]["end"] + 0.6), 2)
    return {"audio": str(out), "duration": round(dur, 2), "voice": voice or DEFAULT_VOICE, "captions": cues,
            "chars_per_s": round(len(re.sub(r"\W", "", full)) / max(dur, 0.1), 1)}


# ---------------- 静态图 → 运镜视频 ----------------

MOTIONS = {
    "zoom_in": ("1+0.10*on/{n}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
    "zoom_out": ("1.10-0.10*on/{n}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
    "pan_up": ("1.10", "iw/2-(iw/zoom/2)", "(ih-ih/zoom)*(1-on/{n})"),
    "pan_down": ("1.10", "iw/2-(iw/zoom/2)", "(ih-ih/zoom)*on/{n}"),
}


def still_to_video(image: str, job: str, name: str, audio: str | None, duration: float, motion: str, tail: float) -> dict:
    img = Path(image).expanduser()
    if not img.is_file():
        raise FileNotFoundError(f"图片不存在：{img}")
    if motion not in MOTIONS:
        raise ValueError(f"motion 只能是 {list(MOTIONS)}")
    if audio:
        audio = str(Path(audio).expanduser())
        if not Path(audio).is_file():
            raise FileNotFoundError(f"音频不存在：{audio}")
    dur = duration if duration > 0 else ((probe(audio)["duration"] + tail) if audio else 8.0)
    fps, n = 30, int(round(dur * 30))
    z, x, y = (s.format(n=max(n - 1, 1)) for s in MOTIONS[motion])
    # 先裁成 9:16 并放大到 2 倍再运镜，减小 zoompan 的整数像素抖动
    vf = (f"scale=2160:3840:force_original_aspect_ratio=increase:flags=lanczos,crop=2160:3840,"
          f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s=1080x1920:fps={fps},format=yuv420p")
    out = job_dir(job, "raw") / f"{name}.mp4"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", str(fps), "-t", f"{dur:.3f}", "-i", str(img)]
    cmd += ["-i", audio] if audio else ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]  # 无配音时补静音轨
    cmd += ["-map", "0:v", "-map", "1:a", "-vf", vf, "-frames:v", str(n), "-c:v", "libx264", "-preset", "medium",
            "-crf", "16", "-r", str(fps), "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-af", f"apad=whole_dur={dur:.3f}", "-t", f"{dur:.3f}", str(out)]
    run(cmd)
    return {"video": str(out), "duration": round(probe(out)["duration"], 2), "motion": motion}
