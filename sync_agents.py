"""把 media 系列 7 个 MCP 服务与全部 Skill 同步到本机所有 Agent（可重复执行，修改前自动备份）。

用法：uv run python sync_agents.py [--dry-run | --print-json]
新 Agent：在 ~/.config/media-mcp/agents.json 登记（格式见 README），或用 --print-json 拿配置粘贴到它的设置界面。
Skill 正本在 ./skill/<name>/，各 Agent 目录内放软链接，改一处全生效。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HOME = Path.home()
ROOT = Path(__file__).resolve().parent
CMD = shutil.which("uv") or "/opt/homebrew/bin/uv"
# 按能力拆分的 7 个服务，共用 ms/ 代码与 ~/.config/media-mcp/ 配置；每个都可单独用于非视频任务
SERVERS = {name: ["--directory", str(ROOT), "run", "python", "-m", f"servers.{mod}"] for name, mod in (
    ("cos-storage", "cos_storage"),    # COS 文件存储：任意文件 → 公网链接
    ("media-image", "media_image"),    # GPT-image 生图/改图 + 产品真实像素合成
    ("media-reapi", "media_reapi"),    # reAPI 云端生成（视频/音乐/云超分），按美元扣费
    ("media-jimeng", "media_jimeng"),  # 即梦画布 CLI 视频生成，按会员积分扣费
    ("media-edit", "media_edit"),      # 本地后期：成片合成、质检、审片、放大
    ("media-speech", "media_speech"),  # 语音：转写、字幕对齐、TTS 配音
    ("almanac", "almanac"),            # 日历与天气：农历、节气、节日、天气提醒
)}
RETIRED = ["media-studio"]  # 已拆分的旧服务：从各 Agent 中移除
DRY = "--dry-run" in sys.argv
STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")

# 以后新装的 Agent：在 ~/.config/media-mcp/agents.json 里加一项即可（无需改代码），格式见 README
EXTRA_AGENTS_FILE = HOME / ".config/media-mcp/agents.json"
EXTRA = json.loads(EXTRA_AGENTS_FILE.read_text()).get("agents", []) if EXTRA_AGENTS_FILE.exists() else []

SKILL_TARGETS = {
    "claude-code": HOME / ".claude/skills",
    "codex/kimi/通用(.agents)": HOME / ".agents/skills",
    "hermes": HOME / ".hermes/skills/media",
    "antigravity": HOME / ".gemini/config/skills",
    # zcode 自动读取 ~/.agents/skills，无需单独链接
    **{a["name"]: Path(a["skills_dir"]).expanduser() for a in EXTRA if a.get("skills_dir")},
}


def backup(p: Path) -> None:
    if p.exists() and not DRY:
        shutil.copy2(p, p.with_name(p.name + f".bak-media-studio-{STAMP}"))


def write(p: Path, text: str) -> None:
    if DRY:
        print(f"  [dry-run] would write {p}")
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def link_skills() -> list[str]:
    log = []
    for skill_dir in sorted((ROOT / "skill").iterdir()):
        if not (skill_dir / "SKILL.md").exists():
            continue
        for agent, base in SKILL_TARGETS.items():
            if not base.parent.exists():
                log.append(f"skip {agent}: {base.parent} 不存在")
                continue
            dst = base / skill_dir.name
            if dst.is_symlink() and dst.resolve() == skill_dir.resolve():
                log.append(f"ok   {agent}: {dst} 已链接")
                continue
            if not DRY:
                base.mkdir(parents=True, exist_ok=True)
                if dst.exists() or dst.is_symlink():
                    # 备份移出技能目录，避免被 Agent 当成同名技能重复加载
                    bak = ROOT / "backups" / f"{agent.split('/')[0]}-{dst.name}-{STAMP}"
                    bak.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(dst), str(bak))
                    log.append(f"     旧版已移至 {bak}")
                dst.symlink_to(skill_dir)
            log.append(f"link {agent}: {dst} -> {skill_dir}")
    return log


def claude_code(NAME: str, ARGS: list[str] | None) -> str:
    """ARGS=None 表示移除（已退役的服务）。"""
    r = subprocess.run(["claude", "mcp", "get", NAME], capture_output=True, text=True)
    if ARGS is None:
        if r.returncode != 0:
            return f"ok   claude-code: {NAME} 未注册"
        if not DRY:
            subprocess.run(["claude", "mcp", "remove", "-s", "user", NAME], capture_output=True)
        return f"del  claude-code: {NAME}"
    if r.returncode == 0 and str(ROOT) in r.stdout and ARGS[-1] in r.stdout:
        return f"ok   claude-code: {NAME} 已注册"
    if not DRY:
        subprocess.run(["claude", "mcp", "remove", "-s", "user", NAME], capture_output=True)
        subprocess.run(["claude", "mcp", "add", "-s", "user", NAME, "--", CMD, *ARGS], check=True, capture_output=True)
    return f"add  claude-code: {NAME} (user scope)"


def codex(NAME: str, ARGS: list[str] | None) -> str:
    p = HOME / ".codex/config.toml"
    if not p.exists():
        return "skip codex: 无 config.toml"
    txt = p.read_text()
    pat = re.compile(rf"\n*\[mcp_servers\.{re.escape(NAME)}\]\n(?:(?!\[)[^\n]*\n?)*")
    rest = pat.sub("\n", txt).rstrip("\n") + "\n"
    block = "" if ARGS is None else (f'\n[mcp_servers.{NAME}]\ncommand = "{CMD}"\nargs = {json.dumps(ARGS)}\n'
                                     f"startup_timeout_sec = 60\ntool_timeout_sec = 180\n")
    m = pat.search(txt)
    if ARGS is not None and m and m.group().strip() == block.strip():
        return f"ok   codex: {NAME} 已是最新"
    new = rest + block
    if new == txt or (ARGS is None and not m):
        return f"ok   codex: {NAME} {'未注册' if ARGS is None else '已是最新'}"
    backup(p)
    write(p, new)
    return f"{'del ' if ARGS is None else 'set '} codex: {NAME} → ~/.codex/config.toml"


def hermes(NAME: str, ARGS: list[str] | None) -> str:
    p = HOME / ".hermes/config.yaml"
    if not p.exists():
        return "skip hermes: 无 config.yaml"
    txt = p.read_text()
    pat = re.compile(rf"^  {re.escape(NAME)}:\n(?:    [^\n]*\n)*", re.M)
    entry = "" if ARGS is None else (f"  {NAME}:\n    command: \"{CMD}\"\n    args: {json.dumps(ARGS)}\n"
                                     f"    timeout: 180\n    connect_timeout: 90\n")
    if pat.search(txt):
        new = pat.sub(lambda m: entry, txt, count=1)
    elif ARGS is None:
        return f"ok   hermes: {NAME} 未注册"
    elif re.search(r"^mcp_servers:\s*\{\}\s*$", txt, re.M):
        new = re.sub(r"^mcp_servers:\s*\{\}\s*$", "mcp_servers:\n" + entry.rstrip("\n"), txt, count=1, flags=re.M)
    elif re.search(r"^mcp_servers:\s*$", txt, re.M):
        new = re.sub(r"^mcp_servers:\s*$", "mcp_servers:\n" + entry.rstrip("\n"), txt, count=1, flags=re.M)
    else:
        new = txt.rstrip("\n") + "\nmcp_servers:\n" + entry
    if new == txt:
        return f"ok   hermes: {NAME} 已是最新"
    backup(p)
    write(p, new)
    return f"{'del ' if ARGS is None else 'set '} hermes: {NAME} → ~/.hermes/config.yaml（重启 Hermes 生效）"


def _entry(fmt: str, ARGS: list[str]) -> dict:
    if fmt == "zcode":  # ZCode：mcp.servers.<名>，带 type / enabled / timeoutMs
        return {"type": "stdio", "command": CMD, "args": ARGS, "enabled": True, "timeoutMs": 300000}
    return {"command": CMD, "args": ARGS}  # 通用 mcpServers（Claude Desktop / Cursor / Kimi / Gemini 等）


def json_mcp(agent: str, p: Path, NAME: str, ARGS: list[str] | None, fmt: str = "mcpServers") -> str:
    """fmt：mcpServers = {"mcpServers": {名: {...}}}；zcode = {"mcp": {"servers": {名: {...}}}}。"""
    if not p.parent.exists():
        return f"skip {agent}: {p.parent} 不存在"
    data = {}
    if p.exists() and p.read_text().strip():
        data = json.loads(p.read_text())
    servers = data.setdefault("mcp", {}).setdefault("servers", {}) if fmt == "zcode" else data.setdefault("mcpServers", {})
    if ARGS is None:
        if NAME not in servers:
            return f"ok   {agent}: {NAME} 未注册"
        backup(p)
        servers.pop(NAME)
        write(p, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return f"del  {agent}: {NAME}"
    want = _entry(fmt, ARGS)
    if servers.get(NAME) == want:
        return f"ok   {agent}: {NAME} 已是最新"
    backup(p)
    servers[NAME] = want
    write(p, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return f"set  {agent}: {NAME} → {p}"


# 内置支持的 Agent：(名称, MCP 配置文件, 格式)。Claude Code 用 claude CLI、Codex 用 TOML、Hermes 用 YAML，单独处理。
JSON_AGENTS = [
    ("kimi-code", HOME / ".kimi-code/mcp.json", "mcpServers"),
    ("antigravity", HOME / ".gemini/config/mcp_config.json", "mcpServers"),
    ("zcode", HOME / ".zcode/cli/config.json", "zcode"),
]
for a in EXTRA:
    if a.get("mcp_file"):
        JSON_AGENTS.append((a["name"], Path(a["mcp_file"]).expanduser(), a.get("format", "mcpServers")))


RULES_BEGIN, RULES_END = "<!-- media-studio:begin -->", "<!-- media-studio:end -->"
RULES = f"""{RULES_BEGIN}
## 媒体与文件工具（media 系列 MCP，由 ~/media-studio-mcp/sync_agents.py 同步，请勿手改此块）
- 7 个 MCP 按能力拆分，可单独用于任何任务：`cos-storage`（任意文件→公网链接，30 天自动删除）、`media-image`（GPT-image 生图/改图、产品真实像素合成）、`media-jimeng`（即梦画布 CLI 视频，**扣会员积分**）、`media-reapi`（云端视频/音乐/超分，**扣美元**）、`media-edit`（本地成片合成/质检/放大）、`media-speech`（转写、字幕对齐、TTS 配音）、`almanac`（农历/节气/节日/天气）。
- 抖音/短视频/带货/口播/字幕/超分：用 `douyin-video` skill；每日早安/节气养生等固定 IP 系列：用 `daily-health-video` skill（如已安装）。开工先跑相关服务的 doctor。
- 每个项目一个文件夹 `<projects_root>/<项目名>/`（默认 `~/Desktop/Self-media`，见 settings.json）（使用说明.md、素材/、文案/、产出/），工具的 job 参数填项目名即写入 `<项目>/产出/`。
- 任何会扣费的操作（`submit_quote`、`jimeng_submit`）必须先把报价单 summary（金额/积分）与 alternatives 给用户，并获得明确同意；默认执行用户提出的方案，不擅自升级模型或分辨率。
- 视频生成默认走 `media-jimeng`（即梦画布 CLI，Seedance 2.5 同画质，服务端报价并以报价为积分上限）；reAPI 用于音乐、兜底和即梦没有的能力。分辨率默认 480p 生成→本地放大到 720p 交付；720p 及以上生成须用户明确要求或确认（原生 720p 特写明显更清晰）。
- 选型先看用途：真人出镜/生活场景的带货投放主片用旗舰档（Seedance 2.5，`video_options(use_case="ad_people")`），不用 Mini；Mini 只用于空镜、草稿和已验证的固定 IP。
- 有产品的视频按「产品零出镜分镜」设计：AI 主片只拍人物与生活（提示词禁止出现产品/包装），产品画面全部用真实像素（`motion_clip` 动画、`image_composite`+`still_to_video` 特写），用 `broll_insert` 按台词时间覆盖、保留口播；不要让模型重画标签，也不要用模糊/裁切补救。
- 画面不要字、视频不要 BGM；字幕/花字/价格卡/BGM 由 `video_compose` 后期完成，成片必须通过 `qa_check`。
- 用 cos_upload 外发含个人信息、密钥或合同的文件前先征得用户同意。不读取、不打印 `~/.config/media-mcp/.env`。
{RULES_END}"""

RULE_FILES = {
    "claude-code": HOME / ".claude/CLAUDE.md",
    "codex": HOME / ".codex/AGENTS.md",
    "hermes": HOME / ".hermes/AGENTS.md",
    "kimi-code": HOME / ".kimi-code/AGENTS.md",
    "antigravity(AGENTS)": HOME / ".gemini/config/AGENTS.md",
    "antigravity(GEMINI)": HOME / ".gemini/config/GEMINI.md",
    "zcode": HOME / ".zcode/AGENTS.md",
    **{a["name"]: Path(a["rules_file"]).expanduser() for a in EXTRA if a.get("rules_file")},
}


def sync_rules() -> list[str]:
    log = []
    for agent, f in RULE_FILES.items():
        if not f.parent.exists():
            log.append(f"skip {agent}: {f.parent} 不存在")
            continue
        txt = f.read_text() if f.exists() else ""
        if RULES_BEGIN in txt:
            new = re.sub(re.escape(RULES_BEGIN) + r".*?" + re.escape(RULES_END), RULES, txt, flags=re.S)
        else:
            new = txt.rstrip("\n") + ("\n\n" if txt.strip() else "") + RULES + "\n"
        if new == txt:
            log.append(f"ok   {agent}: 规则已是最新")
            continue
        backup(f)
        write(f, new)
        log.append(f"sync {agent}: {f}")
    return log


def print_json() -> None:
    """给只能在界面里粘贴配置的 Agent：输出标准 mcpServers JSON。"""
    print(json.dumps({"mcpServers": {n: _entry("mcpServers", a) for n, a in SERVERS.items()}}, ensure_ascii=False, indent=2))


def main() -> None:
    if "--print-json" in sys.argv:
        print_json()
        return
    print("== Skills")
    for l in link_skills():
        print(" ", l)
    print("== 全局规则")
    for l in sync_rules():
        print(" ", l)
    print("== MCP")
    for name, args in [*SERVERS.items(), *((n, None) for n in RETIRED)]:
        for l in (claude_code(name, args), codex(name, args), hermes(name, args),
                  *(json_mcp(agent, path, name, args, fmt) for agent, path, fmt in JSON_AGENTS)):
            print(" ", l)


if __name__ == "__main__":
    main()
