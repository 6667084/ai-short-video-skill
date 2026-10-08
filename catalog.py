"""抓取 reAPI 视频模型目录：能力参数来自 llms.txt，价格来自模型页内嵌的 pricing 数据（与实际扣费一致）。

用法：uv run python catalog.py  → 写入 catalog.json
"""
from __future__ import annotations

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import httpx

from ms.config import PROXY

BASE = "https://reapi.ai"
OUT = Path(__file__).with_name("catalog.json")


def _get(c: httpx.Client, url: str) -> str:
    r = c.get(url)
    r.raise_for_status()
    return r.text


def _slugs(c: httpx.Client) -> list[str]:
    html = _get(c, f"{BASE}/models")
    return sorted(set(re.findall(r'/models/([a-z0-9][a-z0-9.\-]+)', html)) - {"models"})


def _parse_llms(txt: str) -> dict:
    def field(name: str) -> str | None:
        m = re.search(rf"^- {name}: (.+)$", txt, re.M)
        return m.group(1).strip() if m else None

    params = {}
    for m in re.finditer(r"^- `(\w+)` \(([^)]*)\)(?: — (.*))?$", txt, re.M):
        params[m.group(1)] = {"spec": m.group(2), "desc": (m.group(3) or "").strip()}
    return {
        "summary": field("What it does"),
        "vendor": field("Vendor"),
        "category": field("Category"),
        "model_ids": re.findall(r"`([^`]+)`", field("Model ids") or field("Wire model id") or ""),
        "endpoint": field("Endpoint"),
        "params": params,
    }


def _parse_prices(html: str, slug: str) -> list[dict]:
    s = html.replace('\\"', '"')
    out, seen = [], set()
    for m in re.finditer(r'\{"modeKey":"([^"]+)","categoryKey":"([^"]+)","unit":"([^"]+)","priceUSD":([\d.]+),"credits":(\d+)\}', s):
        mode = m.group(1).rsplit(".", 1)[-1]
        cat = m.group(2).rsplit(".", 1)[-1]
        key = (mode, cat, m.group(3))
        if key in seen:
            continue
        seen.add(key)
        out.append({"mode": mode, "category": cat, "unit": m.group(3), "usd": float(m.group(4)), "credits": int(m.group(5))})
    return out


def _one(slug: str) -> dict | None:
    with httpx.Client(proxy=PROXY, trust_env=False, timeout=40, follow_redirects=True) as c:
        try:
            llms = _get(c, f"{BASE}/models/{slug}/llms.txt")
        except Exception:
            return None
        info = _parse_llms(llms)
        if info["category"] != "video-gen":
            return None
        try:
            info["prices"] = _parse_prices(_get(c, f"{BASE}/models/{slug}"), slug)
        except Exception as e:
            info["prices"], info["price_error"] = [], str(e)
        info["slug"] = slug
        info["preview"] = "Coming soon" in (info["summary"] or "") or "Preview models" in llms and not info["prices"]
        return info


def build() -> dict:
    with httpx.Client(proxy=PROXY, trust_env=False, timeout=40) as c:
        slugs = _slugs(c)
    with ThreadPoolExecutor(8) as ex:
        models = [m for m in ex.map(_one, slugs) if m]
    data = {"updated": date.today().isoformat(), "models": sorted(models, key=lambda m: m["slug"])}
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    return data


if __name__ == "__main__":
    d = build()
    for m in d["models"]:
        p = [x["usd"] for x in m["prices"] if x["unit"].endswith("second")]
        rng = f"${min(p):.3f}–{max(p):.3f}/s" if p else ("|".join(f"${x['usd']}/{x['unit']}" for x in m["prices"][:2]) or "无价格")
        print(f"{m['slug']:28} {','.join(m['model_ids'])[:60]:60} {rng}", file=sys.stdout)
