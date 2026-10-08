"""字幕：whisper.cpp 逐字时间戳 + 脚本原文序列对齐（字幕文字 100% 来自脚本）。"""
from __future__ import annotations

import difflib
import json
import re
import tempfile
from pathlib import Path

from .config import WHISPER_MODEL
from .media import probe, run

_ZH = "零一二三四五六七八九"


def _int_zh(n: int) -> str:
    if n < 10:
        return _ZH[n]
    if n < 20:
        return "十" + (_ZH[n % 10] if n % 10 else "")
    if n < 100:
        return _ZH[n // 10] + "十" + (_ZH[n % 10] if n % 10 else "")
    if n < 1000:
        r = n % 100
        tail = "" if r == 0 else ("零" + _ZH[r] if r < 10 else _ZH[r // 10] + "十" + (_ZH[r % 10] if r % 10 else ""))
        return _ZH[n // 100] + "百" + tail
    return "".join(_ZH[int(c)] for c in str(n))


def norm_speech(text: str) -> str:
    """统一为可比对的口语串：阿拉伯数字转中文读法，去标点空白。"""
    text = re.sub(r"(\d+)\.(\d+)", lambda m: _int_zh(int(m[1])) + "点" + "".join(_ZH[int(c)] for c in m[2]), text)
    text = re.sub(r"\d+", lambda m: _int_zh(int(m[0])), text)
    return re.sub(r"[\W_]+", "", text).lower()


def fmt_ts(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def parse_srt(path: str | Path) -> list[dict]:
    def ts(s: str) -> float:
        h, m, rest = s.strip().split(":")
        sec, ms = rest.replace(".", ",").split(",")
        return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000

    cues = []
    for block in re.split(r"\n\s*\n", Path(path).expanduser().read_text(encoding="utf-8").strip()):
        lines = block.strip().splitlines()
        idx = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if idx is None:
            continue
        a, b = lines[idx].split("-->")
        cues.append({"start": ts(a), "end": ts(b), "text": "\n".join(lines[idx + 1:]).strip()})
    return cues


def write_srt(cues: list[dict], path: Path) -> None:
    path.write_text("\n".join(f"{k}\n{fmt_ts(c['start'])} --> {fmt_ts(c['end'])}\n{c['text']}\n"
                              for k, c in enumerate(cues, 1)), encoding="utf-8")


def align(video: str, lines: list, max_tail: float = 0.35) -> dict:
    src = Path(video).expanduser()
    dur = probe(src)["duration"]
    shows, says = [], []
    for l in lines:
        show = l if isinstance(l, str) else l["show"]
        shows.append(show)
        says.append(l if isinstance(l, str) else (l.get("say") or show))
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "a.wav"
        run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
        run(["whisper-cli", "-m", str(WHISPER_MODEL), "-l", "zh", "-ml", "1", "-oj", "-of", str(Path(td) / "w"),
             "--prompt", ("以下是普通话的句子，使用简体中文。" + "".join(says))[:800], "-f", str(wav)])
        toks = json.loads((Path(td) / "w.json").read_text()).get("transcription", [])
    asr_c, asr_t = [], []
    for t in toks:
        a, b = t["offsets"]["from"] / 1000, t["offsets"]["to"] / 1000
        if a >= dur:
            continue  # 静音段幻觉
        s = norm_speech(t["text"])
        for i, ch in enumerate(s):
            asr_c.append(ch)
            asr_t.append((a + (b - a) * i / len(s), a + (b - a) * (i + 1) / len(s)))
    sc_c, owner = [], []
    for i, s in enumerate(says):
        for ch in norm_speech(s):
            sc_c.append(ch)
            owner.append(i)
    hit: dict[int, list] = {}
    for blk in difflib.SequenceMatcher(None, sc_c, asr_c, autojunk=False).get_matching_blocks():
        for k in range(blk.size):
            hit.setdefault(owner[blk.a + k], []).append(asr_t[blk.b + k])
    n = len(lines)
    starts = [min(x[0] for x in hit[i]) if i in hit else None for i in range(n)]
    ends = [max(x[1] for x in hit[i]) if i in hit else None for i in range(n)]
    known = [i for i in range(n) if starts[i] is not None]
    for i in range(n):  # 未命中行在相邻已知行之间线性插值
        if starts[i] is None:
            prev = max([k for k in known if k < i], default=None)
            nxt = min([k for k in known if k > i], default=None)
            a = ends[prev] if prev is not None else 0.0
            b = starts[nxt] if nxt is not None else dur
            span = (nxt if nxt is not None else n) - (prev if prev is not None else -1)
            pos = i - (prev if prev is not None else -1)
            starts[i], ends[i] = a + (b - a) * (pos - 1) / span, a + (b - a) * pos / span
    cues = []
    for i in range(n):
        a = max(0.0, starts[i] - 0.05)
        nxt = starts[i + 1] - 0.05 if i + 1 < n else dur
        b = min(max(ends[i] + max_tail, a + 0.6), nxt) if nxt - ends[i] > 0.4 else nxt
        cues.append({"start": round(a, 3), "end": round(min(b, dur), 3), "text": shows[i]})
    srt = src.with_suffix(".srt")
    write_srt(cues, srt)
    return {"srt": str(srt), "matched_lines": f"{len(hit)}/{n}", "cues": cues}


def transcribe(video: str, hint: str | None = None) -> dict:
    """无脚本时的纯识别（句级），仅供参考/审核口播。"""
    src = Path(video).expanduser()
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "a.wav"
        run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
        run(["whisper-cli", "-m", str(WHISPER_MODEL), "-l", "zh", "-oj", "-of", str(Path(td) / "w"),
             "--prompt", ("以下是普通话的句子，使用简体中文。" + (hint or ""))[:800], "-f", str(wav)])
        data = json.loads((Path(td) / "w.json").read_text())
    dur = probe(src)["duration"]
    segs = [{"start": s["offsets"]["from"] / 1000, "end": min(dur, s["offsets"]["to"] / 1000), "text": s["text"].strip()}
            for s in data.get("transcription", []) if s["offsets"]["from"] / 1000 < dur and s["text"].strip()]
    return {"segments": segs, "text": "".join(s["text"] for s in segs)}
