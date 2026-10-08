"""配置：密钥只从 ~/.config/media-mcp/.env 读取（MEDIA_MCP_ENV 可覆盖），绝不出现在工具返回值里。"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

# 桌面端 Agent（ZCode、Antigravity 等从 Dock 启动）不继承终端 PATH：补上 Homebrew 等常用目录，确保能找到 ffmpeg/node/whisper
_EXTRA_PATH = ["/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin", str(Path.home() / ".local/bin")]
os.environ["PATH"] = os.pathsep.join(dict.fromkeys(
    [d for d in _EXTRA_PATH if Path(d).is_dir()] + os.environ.get("PATH", "/usr/bin:/bin").split(os.pathsep)))

ENV_PATH = Path(os.environ.get("MEDIA_MCP_ENV", "~/.config/media-mcp/.env")).expanduser()
ROOT = Path(__file__).resolve().parent.parent


def _load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


CFG = {**_load_env(ENV_PATH), **{k: v for k, v in os.environ.items() if k.startswith(("COS_", "IMAGE_", "REAPI_", "MEDIA_"))}}


def cfg(key: str, default: str | None = None) -> str:
    v = CFG.get(key, default)
    if v is None:
        raise RuntimeError(f"缺少配置 {key}（请在 {ENV_PATH} 中填写）")
    return v


WORKDIR = Path(cfg("MEDIA_WORKDIR", "~/Movies/AI-Studio")).expanduser()
REAPI_BASE = cfg("REAPI_BASE", "https://reapi.ai/api/v1").rstrip("/")
WHISPER_MODEL = Path(cfg("WHISPER_MODEL", "~/.local/share/whisper/ggml-large-v3-turbo-q5_0.bin")).expanduser()
SEEDVR2_DIR = Path(cfg("SEEDVR2_DIR", "~/tools/seedvr2")).expanduser()
REALESRGAN_DIR = Path(cfg("REALESRGAN_DIR", "~/tools/realesrgan")).expanduser()
COMPOSE_DIR = ROOT / "compose"

# 非密钥偏好设置（默认城市、配音、IP 形象等），可放心读取与修改
SETTINGS_PATH = Path(os.environ.get("MEDIA_MCP_SETTINGS", "~/.config/media-mcp/settings.json")).expanduser()
SETTINGS: dict = json.loads(SETTINGS_PATH.read_text()) if SETTINGS_PATH.exists() else {}
# 海外请求（reAPI、COS、天气、edge-tts）走的代理：.env 的 MEDIA_PROXY 优先，其次 settings.json 的 "proxy"；都没有则直连
PROXY = cfg("MEDIA_PROXY", SETTINGS.get("proxy", "")) or None
FACEBOX = ROOT / "tools" / "facebox"


# 每个项目一个文件夹：<PROJECTS_ROOT>/<项目名>/（产物在其中的 产出/）；报价单、后台任务等系统状态仍在 WORKDIR
PROJECTS_ROOT = Path(os.environ.get("MEDIA_PROJECTS_ROOT") or SETTINGS.get("projects_root") or "~/Desktop/Self-media").expanduser()


def job_dir(job: str, sub: str = "") -> Path:
    """job 取值：
    - 项目名（如 "景田纯净水"）→ <PROJECTS_ROOT>/景田纯净水/产出/
    - 绝对路径或 ~ 开头 → 直接使用（如 "~/Desktop/Self-media/某项目/产出"）
    - 空、"misc" 或 "_" 开头（测试/临时）→ <WORKDIR>/<名>/"""
    name = re.sub(r"[^\w\-.一-鿿]", "_", job or "misc")
    if job and job.startswith(("/", "~")):
        root = Path(job).expanduser()
    elif name == "misc" or name.startswith("_"):
        root = WORKDIR / name
    else:
        root = PROJECTS_ROOT / name / "产出"
    p = root / sub if sub else root
    p.mkdir(parents=True, exist_ok=True)
    return p


def log_task(job: str, rec: dict) -> None:
    with open(job_dir(job) / "tasks.jsonl", "a") as f:
        f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), **rec}, ensure_ascii=False) + "\n")
