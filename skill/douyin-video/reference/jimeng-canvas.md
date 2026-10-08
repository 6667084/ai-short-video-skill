# 即梦画布 CLI（media-jimeng）

2026-10 实测后作为视频默认通道：同模型（Seedance 2.5）画质与 reAPI 一致，原生 720p 价格约为 reAPI 的 44%，并且服务端报价、以报价为积分上限运行，不会超扣。

## 安装与登录

- 需要即梦**会员**（免费账号只能用网页“样片模式”的少量积分，CLI 不可用）。
- 安装：按即梦官网 `jimeng.jianying.com/ai-tool/install` 安装 `dreamina-canvas`（默认在 `~/.local/bin`），然后在终端执行 `dreamina-canvas auth login` 扫码登录。
- **必须直连**：即梦是国内服务，走代理会报 `ret=10002 login fail`。`ms/jimeng.py` 调用时自动去掉 `http(s)_proxy / ALL_PROXY` 环境变量。
- 每个模型在本账号**首次使用前需在网页端用该模型生成一次**，否则报 `AigcComplianceConfirmationRequired`。
- 画布 CLI 没有余额接口：`jimeng_credit` 借用旧版 `dreamina` CLI 查询；登录画布后旧 CLI 会话可能失效（`user_id=0`），只影响余额显示，需要时 `dreamina login` 重新登录。

## 流程（工具封装了以下 CLI 步骤）

| 工具 | 底层 | 说明 |
|---|---|---|
| `jimeng_quote` | 建/复用项目画布 → 上传参考图为节点（本地缓存，不重复上传）→ 建视频节点（提示词里的 `图片N` 换成 `{{node:id}}`）→ `node quote` | 返回 `quote_id`、`summary`（积分、≈人民币、余额）、`canvas_url`；同 job+name 的待确认报价复用原节点 |
| 用户确认 | — | 把 summary 原样给用户 |
| `jimeng_submit` | `node confirm --credit-ceiling <报价>` 签发凭证 → `node run --credit-token --submit-id` | submitId 先落盘，失败重试复用，**不会重复扣费**；最新服务端价超过上限会拒绝 |
| `jimeng_status` | `operation status` → `resource download` | 下载到 `<job>/raw/<name>.mp4`；480p 自动本地放大出 `_720p.mp4` |
| `jimeng_spend` | 读 `tasks.jsonl` | 汇总项目实际积分 |

- 每个项目一块网页画布，节点、产物都能在浏览器里看到，方便人工复查和手动再生成。
- `dry_run=True` 只做本地校验（模型、分辨率、时长、参考数量按画布实时模型目录校验），不连服务器。
- 已提交的任务只用 `jimeng_status` 查询，**不要重新提交**。

## 积分（Seedance 2.5，2026-10）

| 分辨率 | 积分/秒 | 26 秒一条 | ≈ 人民币（高级会员 ¥1168/27720 积分 ≈ ¥0.042/积分） |
|---|---|---|---|
| 480p | 12 | 312 | ≈ ¥13 |
| 720p | 20 | 520 | ≈ ¥22 |
| 1080p | 64 | 1664 | ≈ ¥70 |

- 网页端的“样片模式”480p 只要 9 积分/秒，但 CLI 没有样片模式，也没有 seed 参数。
- Seed Music 30 秒约 6 积分（可替代 Suno，风格较少）。
- 实测出片时间：24–30 秒的片 4–11 分钟；6 秒补拍 3.5 分钟。CLI 返回的“约 1 小时”预估不准，**不要转述给用户**。

## 选择分辨率

- 480p → 本地 Lanczos 放大到 720p：远景、空镜、草稿够用。
- **原生 720p**：近景/特写多、要投放的主片。480p 放大后皮肤发蜡、睫毛和发丝糊（`docs/images/02_480p_upscale_vs_native_720p.jpg`）。每 26 秒多 208 积分（≈¥8.7）。
- 必须由用户明确要求或确认后才传 `confirm_high_res=True`。

## 坑

| 症状 | 处理 |
|---|---|
| `ret=10002 login fail` | 走了代理；确认调用环境无代理变量 |
| `AigcComplianceConfirmationRequired` | 网页端先用该模型生成一次 |
| 视频 60fps 封装、实际 24fps | 逐帧超分前用 `-fps_mode passthrough` 拆帧，否则时长被拉长、音画不同步（`tools/upscale.sh` 已处理） |
| 同提示词两次生成构图完全不同 | 无 seed；满意的结果立刻下载保存，不要指望复现 |
| 漏说最后一句 | 提示词强调“最后一句必须完整说完”，结尾镜头 ≥3 秒 |
