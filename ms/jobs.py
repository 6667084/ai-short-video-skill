"""本地长任务（超分、渲染）：后台进程 + 状态文件，避免阻塞 MCP 调用（Hermes 默认 120s 超时）。"""
from __future__ import annotations

import json
import os
import secrets
import shlex
import subprocess
import time
from datetime import datetime
from pathlib import Path

from .config import WORKDIR

JOBS = WORKDIR / ".local_jobs"


# Remotion 同时渲染两个任务会互抢本地页面服务（报 “Visited localhost:3001 but got no response”）→ 渲染排队执行。
# mkdir 原子锁；超过 20 分钟的锁视为残留（进程被杀）自动清除。
RENDER_LOCK = "/tmp/media-studio-remotion.lock"


def render_locked(cmd: str) -> str:
    """把一段 shell 命令包成“拿到 Remotion 渲染锁才执行、结束即释放”。"""
    lk = shlex.quote(RENDER_LOCK)
    return (f"while ! mkdir {lk} 2>/dev/null; do "
            f"if [ -n \"$(find {lk} -maxdepth 0 -mmin +20 2>/dev/null)\" ]; then rmdir {lk}; fi; sleep 1; done; "
            f"trap 'rmdir {lk} 2>/dev/null' EXIT; {cmd}")


def start(kind: str, script: str, outputs: list[str], meta: dict | None = None) -> dict:
    JOBS.mkdir(parents=True, exist_ok=True)
    jid = f"{kind}_{datetime.now():%m%d%H%M%S}_{secrets.token_hex(2)}"
    log, code = JOBS / f"{jid}.log", JOBS / f"{jid}.exit"
    wrapped = f"( {script} ) > {shlex.quote(str(log))} 2>&1; echo $? > {shlex.quote(str(code))}"
    proc = subprocess.Popen(["/bin/bash", "-c", wrapped], start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    info = {"id": jid, "kind": kind, "pid": proc.pid, "started": time.time(), "outputs": outputs, "meta": meta or {},
            "log": str(log), "exit_file": str(code), "script": script}
    (JOBS / f"{jid}.json").write_text(json.dumps(info, ensure_ascii=False, indent=1))
    return {"local_job_id": jid, "status": "running", "outputs": outputs,
            "next": "调用 local_job_status(local_job_id, wait_s=100) 查看进度"}


def status(jid: str, wait_s: int = 0) -> dict:
    meta_p = JOBS / f"{jid}.json"
    if not meta_p.exists():
        raise ValueError(f"本地任务 {jid} 不存在")
    info = json.loads(meta_p.read_text())
    deadline = time.time() + max(0, min(wait_s, 110))
    code_p = Path(info["exit_file"])
    while not code_p.exists() and time.time() < deadline:
        time.sleep(3)
    log_tail = ""
    if Path(info["log"]).exists():
        txt = Path(info["log"]).read_text(errors="ignore").replace("\r", "\n")
        log_tail = "\n".join([l for l in txt.splitlines() if l.strip()][-12:])
    elapsed = round(time.time() - info["started"])
    if code_p.exists():
        rc = int(code_p.read_text().strip() or 1)
        ok = rc == 0 and all(Path(o).exists() for o in info["outputs"])
        return {"local_job_id": jid, "status": "completed" if ok else "failed", "exit_code": rc,
                "outputs": info["outputs"], "elapsed_s": elapsed, "log_tail": log_tail}
    alive = True
    try:
        os.kill(info["pid"], 0)
    except OSError:
        alive = False
    return {"local_job_id": jid, "status": "running" if alive else "failed", "elapsed_s": elapsed,
            "log_tail": log_tail, "eta_hint": info["meta"].get("eta_hint")}
