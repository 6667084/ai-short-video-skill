"""即梦视频生成（通道：即梦画布 CLI `dreamina-canvas`）：画布节点 → 服务端权威报价 → 用户确认 → 按报价设积分上限运行 → 下载。

- 每个项目（job）对应一块画布，节点和产物在网页画布里可见、可挑选；本地参考素材按内容缓存，同一画布只上传一次。
- 报价来自服务端 `node quote`；提交时用 `node confirm --credit-ceiling <报价>` 签发凭证，最新价超过上限服务端拒绝执行。
- submitId 在运行前生成并落盘，中断后重试复用同一 submitId，不会重复扣费。
- 必须直连：带代理环境变量会导致即梦接口登录/上传失败。
- 积分余额：画布 CLI 不提供余额查询，借用旧 `dreamina` CLI 的 user_credit（未登录时返回 None，不影响生成）。
- 记账写入 <job>/tasks.jsonl（kind=jimeng_submit / jimeng_result，字段 jm_credits），与 reAPI 的美元花费分开。
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path

from .config import SETTINGS, WORKDIR, job_dir, log_task
from .media import probe

CANVAS = shutil.which("dreamina-canvas") or str(Path.home() / ".local/bin/dreamina-canvas")
LEGACY = shutil.which("dreamina") or str(Path.home() / ".local/bin/dreamina")
QUOTES = WORKDIR / ".quotes_jimeng"
STATE = WORKDIR / ".jimeng_canvas.json"  # {"projects": {job: projectId}, "uploads": {projectId: {key: nodeId}}}
CNY_PER_CREDIT = float(SETTINGS.get("jimeng_cny_per_credit", 0.042))  # 高级会员 ¥1168/27720 积分

# 参考单价（积分/秒，2026-10-05 画布服务端报价）：只用于 alternatives 展示，实际以 node quote 为准
REF_RATES = {"seedance_2.5": {"480p": 12, "720p": 20, "1080p": 64}}
MODE_ALIASES = {"multimodal2video": "m2v", "m2v": "m2v", "text2video": "t2v", "t2v": "t2v",
                "frames2video": "first_last_frame", "image2video": "first_last_frame", "first_last_frame": "first_last_frame"}
LOW_RES = ("480p",)
KIND = {"image": "图片", "video": "视频", "audio": "音频"}


class CanvasError(RuntimeError):
    pass


def _env() -> dict:
    return {k: v for k, v in os.environ.items() if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}


def _last_json(text: str) -> dict:
    """输出可能夹杂进度行；取最后一个完整 JSON 对象。"""
    dec = json.JSONDecoder()
    for m in reversed(list(re.finditer(r"^[{\[]", text, re.M))):
        try:
            return dec.raw_decode(text[m.start():])[0]
        except json.JSONDecodeError:
            continue
    raise CanvasError(f"即梦 CLI 输出无法解析：{text.strip()[-400:]}")


def _canvas(args: list[str], timeout: int = 300) -> dict:
    if not Path(CANVAS).exists():
        raise CanvasError("未安装即梦画布 CLI：curl -fsSL https://jimeng.jianying.com/canvas-cli/install.sh | bash（直连），"
                          "再让用户执行 dreamina-canvas auth login")
    r = subprocess.run([CANVAS, "--non-interactive", "--format", "json", *args], capture_output=True, text=True,
                       env=_env(), timeout=timeout)
    d = _last_json((r.stdout or "") + "\n" + (r.stderr or ""))
    if d.get("ok"):
        return d
    err = d.get("error") or {}
    if r.returncode == 11 or err.get("requiredAction") == "login":
        raise CanvasError("即梦画布未登录或登录已失效：请用户在终端执行 dreamina-canvas auth login")
    raise CanvasError(f"即梦画布 {' '.join(args[:2])} 失败：{err.get('message') or err}（code={err.get('code')}, "
                      f"requiredAction={err.get('requiredAction')}）")


def _state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {"projects": {}, "uploads": {}}


def _save_state(s: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=1))


def _project(job: str) -> str:
    s = _state()
    pid = s["projects"].get(job)
    if not pid:
        d = _canvas(["canvas", "create", job if not job.startswith(("/", "~")) else Path(job).name])
        pid = d["data"]["project"]["projectId"]
        s["projects"][job] = pid
        _save_state(s)
    return pid


def _ref_node(pid: str, path: Path, kind: str) -> str:
    """本地素材 → 画布节点（按路径+大小+修改时间缓存，同一画布只上传一次）。"""
    st = path.stat()
    key = f"{path}|{st.st_size}|{int(st.st_mtime)}"
    cached = _state()["uploads"].get(pid, {}).get(key)
    if cached:
        return cached
    rid = _canvas(["resource", "upload", "--project-id", pid, "--file", str(path), "--name", path.stem], 600)["data"]["resourceId"]
    node = _canvas(["node", "create", kind, "--project-id", pid, "--title", path.stem, "--resource-id", rid,
                    "--import-kind", "local_upload"])["data"]["node"]["nodeId"]
    s = _state()
    s["uploads"].setdefault(pid, {})[key] = node
    _save_state(s)
    return node


# ---------------------------------------------------------------- 余额与自检


def credit() -> dict:
    """积分余额（借用旧 dreamina CLI；画布 CLI 无余额接口）。"""
    try:
        r = subprocess.run([LEGACY, "user_credit"], capture_output=True, text=True, env=_env(), timeout=60)
        d = _last_json((r.stdout or "") + (r.stderr or ""))
        if d.get("user_id"):
            return {"total_credit": d.get("total_credit"), "vip_level": d.get("vip_level"),
                    "cny_estimate": round((d.get("total_credit") or 0) * CNY_PER_CREDIT, 1)}
        note = "旧 dreamina CLI 未登录（终端执行 dreamina login 后可查余额）"
    except Exception as e:  # noqa: BLE001
        note = f"余额查询不可用：{str(e)[:120]}"
    return {"total_credit": None, "vip_level": None, "note": note}


def doctor(network: bool = True) -> dict:
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str = "", fix: str = "", level: str = "fail"):
        checks.append({"check": name, "status": "pass" if ok else level, "detail": detail, **({"fix": fix} if not ok else {})})

    add("即梦画布 CLI 已安装", Path(CANVAS).exists(), CANVAS,
        "curl -fsSL https://jimeng.jianying.com/canvas-cli/install.sh | bash（直连）")
    if network and Path(CANVAS).exists():
        try:
            a = _canvas(["auth", "account"], 60)["data"]
            add("画布已登录且为会员", bool(a.get("isVip")), f"vipLevel={a.get('vipLevel')}",
                "请用户在终端执行 dreamina-canvas auth login；视频模型需会员")
        except Exception as e:  # noqa: BLE001
            add("画布已登录且为会员", False, str(e)[:300], "请用户在终端执行 dreamina-canvas auth login")
        c = credit()
        add("积分余额可查", c["total_credit"] is not None,
            f"{c['total_credit']} 积分（≈¥{c.get('cny_estimate')}）" if c["total_credit"] is not None else c.get("note", ""),
            "可选：终端执行 dreamina login（旧 CLI，仅用于查余额）", "warn")
    bad = [c for c in checks if c["status"] == "fail"]
    return {"healthy": not bad, "failed": len(bad), "checks": checks,
            "notes": ["生成通道：即梦画布 CLI（服务端报价 + 积分上限）", "调用自动去掉代理（必须直连）",
                      f"积分折算 ¥{CNY_PER_CREDIT}/积分（settings.json 的 jimeng_cny_per_credit 可改）"]}


# ---------------------------------------------------------------- 模型目录与校验

_MODELS: dict = {"t": 0.0, "items": []}


def _models() -> list[dict]:
    if time.time() - _MODELS["t"] > 3600:
        _MODELS["items"] = _canvas(["model", "list", "--type", "video"], 120)["data"]["items"]
        _MODELS["t"] = time.time()
    return _MODELS["items"]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _resolve(model: str, mode: str, n_refs: dict) -> tuple[str, dict]:
    want = _norm(model)
    for m in _models():
        if want in [_norm(m["model"])] + [_norm(a) for a in m.get("aliases", [])]:
            specs = [md for md in m["modes"] if md["name"] == mode]
            if not specs:
                raise ValueError(f"{m['model']} 不支持模式 {mode}；可用：{sorted({md['name'] for md in m['modes']})}")
            total = sum(n_refs.values())
            for md in specs:  # 同一模式可能有多种参考数量规格（如首尾帧 1 张/2 张）
                ref = md.get("references") or {}
                lim = {t["mediaType"]: t["max"] for t in ref.get("types", [])}
                if ref.get("min", 0) <= total <= ref.get("max", 0) and all(n <= lim.get(k, 0) for k, n in n_refs.items() if n):
                    return m["model"], md
            raise ValueError(f"{m['model']}/{mode} 的参考素材数量不符：{n_refs}；规格："
                             f"{[(md.get('references') or {}) for md in specs]}")
    raise ValueError(f"即梦画布没有模型 {model}；可用：{[m['model'] for m in _models()]}")


def _flag(spec: dict, name: str) -> dict:
    return next((f for f in spec["flags"] if f["flag"] == name), {})


# ---------------------------------------------------------------- 报价单


def _qpath(qid: str) -> Path:
    QUOTES.mkdir(parents=True, exist_ok=True)
    return QUOTES / f"{qid}.json"


def _with_placeholders(prompt: str, nodes: dict[str, list[str]]) -> str:
    """提示词里的 图片1/视频1/音频1 → {{node:…}}，保留画布连线（从大号往小号替换，避免 图片1 吃掉 图片10）。"""
    for kind, ids in nodes.items():
        for i in range(len(ids), 0, -1):
            prompt = prompt.replace(f"{KIND[kind]}{i}", "{{node:%s}}" % ids[i - 1])
    return prompt


def quote(prompt: str, job: str, mode: str = "m2v", model: str = "seedance_2.5", duration: int = 15,
          ratio: str = "9:16", resolution: str = "480p", images: list[str] | None = None, videos: list[str] | None = None,
          audios: list[str] | None = None, first: str | None = None, last: str | None = None,
          confirm_high_res: bool = False, name: str = "", dry_run: bool = False) -> dict:
    images, videos, audios = images or [], videos or [], audios or []
    mode_c = MODE_ALIASES.get(mode)
    if not mode_c:
        raise ValueError(f"mode 只能是 {sorted(MODE_ALIASES)}")
    if mode_c == "first_last_frame":
        if not first or images or videos or audios:
            raise ValueError("首帧/首尾帧模式用 first（与可选 last）传图片，不传 images/videos/audios")
        images = [first] + ([last] if last else [])
    elif first or last:
        raise ValueError("first/last 只用于首帧或首尾帧模式")
    if mode_c == "t2v" and (images or videos or audios):
        raise ValueError("文生视频不接受参考素材")
    res = resolution.lower()
    if res not in LOW_RES and not confirm_high_res:
        raise ValueError(f"默认 480p（交付时本地放大到 720p）；{resolution} 需用户明确要求或确认后传 confirm_high_res=True")
    paths = {k: [Path(p).expanduser() for p in v] for k, v in (("image", images), ("video", videos), ("audio", audios))}
    for p in [p for v in paths.values() for p in v]:
        if not p.is_file():
            raise FileNotFoundError(p)
    model_c, spec = _resolve(model, mode_c, {k: len(v) for k, v in paths.items()})
    dur = _flag(spec, "--duration")
    if dur and not dur.get("min", 0) <= duration <= dur.get("max", 999):
        raise ValueError(f"{model_c} 时长 {dur.get('min')}–{dur.get('max')}s")
    res_vals = _flag(spec, "--resolution").get("values", [])
    res_c = next((v for v in res_vals if v.lower() == res), None)
    if not res_c:
        raise ValueError(f"{model_c}/{mode_c} 分辨率只能是 {res_vals}")
    ratios = _flag(spec, "--ratio").get("values", [])
    if ratios and ratio not in ratios:
        raise ValueError(f"{model_c} 画幅只能是 {ratios}")
    name = name or f"即梦_{datetime.now():%m%d%H%M%S}"
    plan = {"mode": mode_c, "model": model_c, "duration": duration, "ratio": ratio, "resolution": res_c, "name": name,
            "refs": {k: [str(p) for p in v] for k, v in paths.items()}}
    if dry_run:
        return {"dry_run": True, "plan": plan, "next": "校验通过（未连接画布、未报价）"}
    pid = _project(job)
    nodes = {k: [_ref_node(pid, p, k) for p in v] for k, v in paths.items()}
    args = ["--project-id", pid, "--title", name, "--mode", mode_c, "--model", model_c, "--ratio", ratio,
            "--resolution", res_c, "--duration", str(duration), "--prompt", _with_placeholders(prompt, nodes)]
    for n in nodes["image"] + nodes["video"] + nodes["audio"]:
        args += ["--ref", f"node:{n}"]
    # 同一 job+name 的待确认报价复用原节点（改参数后重新报价），避免画布堆积孤立节点
    old = next((q for q in list_quotes(job, "pending") if q.get("name") == name), None)
    if old:
        node = old["nodeId"]
        _canvas(["node", "edit", "video", "--node-id", node, *args])
        _qpath(old["id"]).unlink(missing_ok=True)
    else:
        node = _canvas(["node", "create", "video", *args])["data"]["node"]["nodeId"]
    credits = _canvas(["node", "quote", "--project-id", pid, "--node-id", node])["data"].get("totalMaxCredits")
    bal = credit()["total_credit"]
    rate = REF_RATES.get(model_c, {})
    alts = [f"{r}：约 {rate[r] * duration} 积分（≈¥{rate[r] * duration * CNY_PER_CREDIT:.1f}，参考单价）"
            + ("，需用户确认" if r not in LOW_RES else "，交付时本地放大到 720p")
            for r in rate if r != res and r in [v.lower() for v in res_vals]]
    refs_txt = "/".join(f"{len(v)} {KIND[k]}" for k, v in paths.items())
    summary = (f"即梦画布 {model_c} {mode_c} {res_c} {duration}s {ratio}；参考 {refs_txt}；"
               + (f"服务端报价 {credits} 积分（≈¥{credits * CNY_PER_CREDIT:.1f}）" if credits is not None else "服务端未给出报价")
               + (f"；当前余额 {bal}，提交后约剩 {bal - credits}" if bal is not None and credits is not None else "")
               + ("；完成后本地 Lanczos 放大到 720p" if res in LOW_RES else ""))
    qid = f"jq_{datetime.now():%m%d%H%M%S}_{secrets.token_hex(3)}"
    q = {"id": qid, "job": job, "name": name, "created": time.time(), "status": "pending", "summary": summary,
         "estimate_credits": credits, "projectId": pid, "nodeId": node, "request": {**plan, "prompt": prompt}}
    _qpath(qid).write_text(json.dumps(q, ensure_ascii=False, indent=1))
    return {"quote_id": qid, "summary": summary, "estimate_credits": credits, "balance": bal, "alternatives": alts,
            "canvas_url": f"https://jimeng.jianying.com/ai-tool/ai-canvas/{pid}", "node_id": node,
            "next": "把 summary（积分）与 alternatives 给用户，用户明确同意后才调用 jimeng_submit(quote_id)。"}


def list_quotes(job: str | None = None, status: str | None = "pending") -> list[dict]:
    QUOTES.mkdir(parents=True, exist_ok=True)
    out = []
    for p in sorted(QUOTES.glob("jq_*.json")):
        q = json.loads(p.read_text())
        if (job and q["job"] != job) or (status and q["status"] != status):
            continue
        out.append({k: q.get(k) for k in ("id", "job", "name", "status", "summary", "estimate_credits", "submit_id",
                                          "projectId", "nodeId")})
    return out


def submit(quote_id: str) -> dict:
    p = _qpath(quote_id)
    if not p.exists():
        raise ValueError(f"报价单 {quote_id} 不存在；用 jimeng_list_quotes() 查看")
    lock = p.with_suffix(".lock")
    try:
        os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        raise ValueError(f"报价单 {quote_id} 正在提交中，请勿重复调用") from None
    try:
        q = json.loads(p.read_text())
        if q["status"] == "submitted":
            raise ValueError(f"报价单已提交（submitId {q.get('submit_id')}），用 jimeng_status 查询，不要重新提交")
        if q["status"] not in ("pending", "error"):
            raise ValueError(f"报价单状态为 {q['status']}，请重新报价")
        if time.time() - q["created"] > 3 * 86400:
            raise ValueError("报价单已超过 3 天，请重新报价")
        if q.get("projectId") is None or q.get("estimate_credits") is None:
            raise ValueError("该报价单没有服务端报价（旧版报价单），请重新 jimeng_quote")
        sid = q.get("submit_id") or str(uuid.uuid4())  # 先落盘再运行：中断后复用同一 submitId，不会二次扣费
        q["submit_id"] = sid
        p.write_text(json.dumps(q, ensure_ascii=False, indent=1))
        pid, node, ceiling = q["projectId"], q["nodeId"], int(q["estimate_credits"])
        try:
            tok = _canvas(["node", "confirm", "--project-id", pid, "--node-id", node, "--credit-ceiling", str(ceiling)])["data"]
            token = tok.get("creditConfirmationToken") or tok.get("token")
            run_args = ["node", "run", "--project-id", pid, "--node-id", node, "--submit-id", sid]
            if token:
                run_args += ["--credit-token", token]
            d = _canvas(run_args, 300)["data"]
        except Exception as e:
            q.update(status="error", last_error=str(e)[:500])
            p.write_text(json.dumps(q, ensure_ascii=False, indent=1))
            raise CanvasError(f"即梦画布提交失败：{e}（报价单保留同一 submitId，修正后重试 jimeng_submit 不会重复扣费）") from e
        item = (d.get("items") or [{}])[0]
        q.update(status="submitted", submit_id=item.get("submitId") or sid, submitted=time.time(), credit_ceiling=ceiling)
        p.write_text(json.dumps(q, ensure_ascii=False, indent=1))
        log_task(q["job"], {"kind": "jimeng_submit", "submit_id": q["submit_id"], "quote_id": quote_id, "jm_credits": ceiling,
                            "projectId": pid, "nodeId": node, "name": q.get("name"),
                            "request": {k: v for k, v in q["request"].items() if k != "prompt"}})
        return {"submit_id": q["submit_id"], "credit_ceiling": ceiling, "state": item.get("state"),
                "canvas_url": f"https://jimeng.jianying.com/ai-tool/ai-canvas/{pid}",
                "next": "调用 jimeng_status(submit_id, job, wait_s=100) 轮询；26 秒 720p 实测约 5 分钟"}
    finally:
        lock.unlink(missing_ok=True)


# ---------------------------------------------------------------- 任务状态


def _quote_for(submit_id: str) -> dict | None:
    for p in QUOTES.glob("jq_*.json") if QUOTES.exists() else []:
        q = json.loads(p.read_text())
        if q.get("submit_id") == submit_id:
            return q
    return None


def to_720p(src: Path) -> Path:
    """480p → 720p：Lanczos + 轻锐化；保留原始帧时间戳（即梦输出常为 60fps 封装、实际约 24fps）。"""
    info = probe(src)
    w, h = info["width"], info["height"]
    s = 720 / min(w, h)
    tw, th = round(w * s / 2) * 2, round(h * s / 2) * 2
    out = src.with_name(src.stem + "_720p.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf",
                    f"scale={tw}:{th}:flags=lanczos,unsharp=5:5:0.5,format=yuv420p", "-fps_mode", "passthrough",
                    "-c:v", "libx264", "-crf", "16", "-preset", "medium", "-c:a", "copy", str(out)], check=True)
    return out


def status(submit_id: str, job: str, wait_s: int = 100, name: str = "") -> dict:
    q = _quote_for(submit_id) or {}
    pid = q.get("projectId") or _project(job)
    deadline = time.time() + max(0, min(wait_s, 110))
    while True:
        d = _canvas(["operation", "status", submit_id, "--project-id", pid], 120)["data"]
        st = d.get("state")
        if st in ("succeeded", "failed", "canceled", "cancelled") or time.time() >= deadline:
            break
        time.sleep(10)
    res: dict = {"submit_id": submit_id, "status": st, "credit_ceiling": q.get("credit_ceiling")}
    if st in ("failed", "canceled", "cancelled"):
        res["error"] = d.get("error") or d.get("submission")
        log_task(job, {"kind": "jimeng_failed", "submit_id": submit_id, "error": str(res["error"])[:300]})
        return res
    if st != "succeeded":
        res["next"] = "仍在生成/排队，稍后再次调用 jimeng_status（不要重新提交）"
        return res
    raw = job_dir(job, "raw")
    dest = raw / f"{name or q.get('name') or f'jimeng_{submit_id[:8]}'}.mp4"
    if not dest.exists():
        rid = next((r["resourceId"] for r in d.get("resources", []) if r.get("state") == "succeeded"), None)
        if not rid:
            raise CanvasError(f"任务成功但没有可下载的产物：{d}")
        tmp = raw / f".canvas_{submit_id[:8]}"
        tmp.mkdir(parents=True, exist_ok=True)
        got = _canvas(["resource", "download", rid, "--project-id", pid, "--output", str(tmp)], 600)["data"]["path"]
        Path(got).replace(dest)
        shutil.rmtree(tmp, ignore_errors=True)
    files = [str(dest)]
    info = probe(dest)
    if min(info["width"], info["height"]) < 720:
        up = dest.with_name(dest.stem + "_720p.mp4")
        files.append(str(up if up.exists() else to_720p(dest)))
    res.update(files=files, deliver=files[-1], canvas_url=f"https://jimeng.jianying.com/ai-tool/ai-canvas/{pid}")
    logged = job_dir(job) / "tasks.jsonl"
    if not (logged.exists() and any('"jimeng_result"' in l and submit_id in l for l in logged.read_text().splitlines())):
        log_task(job, {"kind": "jimeng_result", "submit_id": submit_id, "files": files, "jm_credits": q.get("credit_ceiling")})
    return res


def spend(job: str) -> dict:
    f = job_dir(job) / "tasks.jsonl"
    per: dict[str, int] = {}
    for l in f.read_text().splitlines() if f.exists() else []:
        r = json.loads(l) if l.strip() else {}
        if r.get("kind") == "jimeng_result":
            per[r["submit_id"]] = r.get("jm_credits") or 0
    total = sum(per.values())
    return {"job": job, "tasks": len(per), "credits": total, "cny_estimate": round(total * CNY_PER_CREDIT, 1)}
