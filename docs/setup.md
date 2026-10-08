# 安装与配置（详细）

> English readers: the steps mirror the Quick start in [README.en.md](../README.en.md).

## 1. 系统要求

- macOS（Apple Silicon 推荐；人脸检测用 Apple Vision）。Linux 可用但需自行替换 `tools/facebox`（返回人脸框 JSON 即可）。
- Python 3.12+（由 `uv` 管理）、Node 18+、ffmpeg、whisper.cpp。
- 磁盘 ≥20GB 空闲（Remotion 渲染与原片）。

## 2. 安装

```bash
brew install uv ffmpeg node whisper-cpp
git clone https://github.com/6667084/ai-short-video-skill.git ~/ai-short-video-skill
cd ~/ai-short-video-skill
uv sync                                    # Python 依赖
(cd compose && npm install)                # Remotion
swiftc -O tools/facebox.swift -o tools/facebox
```

whisper 模型（字幕对齐用，约 550MB）：

```bash
mkdir -p ~/.local/share/whisper
curl -L -o ~/.local/share/whisper/ggml-large-v3-turbo-q5_0.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin
# 国内网络：把 huggingface.co 换成 hf-mirror.com
```

## 3. 配置

| 文件 | 内容 | 说明 |
|---|---|---|
| `~/.config/media-mcp/.env` | 密钥（见 `.env.example`） | `chmod 600`；Agent 规则要求不读取、不打印 |
| `~/.config/media-mcp/settings.json` | 非密钥偏好（见 `settings.example.json`） | `projects_root`：项目根目录；`proxy`：海外请求代理；`jimeng_cny_per_credit`：积分折算 |

- 即梦画布 CLI 永远直连（工具自动去掉代理变量）；reAPI、COS、天气等海外请求走 `proxy`（为空则直连）。
- 图像接口需兼容 OpenAI `POST /v1/images/generations` 与 `/v1/images/edits`。

## 4. 即梦画布 CLI

1. 开通即梦会员（高级会员即可）。
2. 按 <https://jimeng.jianying.com/ai-tool/install> 安装 `dreamina-canvas`（默认 `~/.local/bin`）。
3. `dreamina-canvas auth login` 扫码登录。
4. 每个模型首次使用前，先在即梦网页端用该模型生成一次（合规确认）。
5. 可选：安装旧版 `dreamina` CLI 并登录，用于 `jimeng_credit` 查余额。

## 5. 注册到 Agent

```bash
uv run python sync_agents.py --dry-run   # 预览
uv run python sync_agents.py             # 写入（修改前自动备份）
uv run python sync_agents.py --print-json   # 只输出 mcpServers JSON，手动粘贴到任意 MCP 客户端
```

`sync_agents.py` 会：把 `skill/*` 软链接到各 Agent 的技能目录；在各 Agent 的全局规则文件里写入/更新一段「媒体与文件工具」规则（`<!-- media-studio:begin/end -->` 之间）；注册 7 个 MCP 服务。
其他 Agent 可在 `~/.config/media-mcp/agents.json` 登记：

```json
{"agents": [{"name": "my-agent", "skills_dir": "~/.my-agent/skills", "mcp_file": "~/.my-agent/mcp.json", "format": "mcpServers", "rules_file": "~/.my-agent/AGENTS.md"}]}
```

不想改全局规则时，只用 `--print-json` 手动配置 MCP，并把 `skill/douyin-video` 复制到技能目录即可。

## 6. 自检

在 Agent 里分别调用各服务的 `doctor`，或：

```bash
uv run python -c "from ms import qa; import json; print(json.dumps(qa.doctor(True), ensure_ascii=False, indent=1))"
```

每项失败都带 `fix` 提示。

## 7. 第一条视频

对 Agent 说：「用 douyin-video 给『XX 产品』项目做一条 30 秒内的抖音带货视频，先给脚本和报价」。
Agent 会在 `<projects_root>/XX 产品/` 下建立 `使用说明.md / 素材 / 文案 / 产出`，写脚本给你审，生成人物参考图给你看，报价后等你确认才扣费。
把官方产品图（**透明底抠图 PNG**）放进 `素材/`——它是所有产品画面的唯一来源。
