---
name: douyin-video
description: Produces Douyin / WeChat Channels (视频号) / TikTok-style vertical ad videos (≤30s, 9:16) end to end — hook-first script, cost quotes confirmed by the user before any paid generation, product-zero storyboard (AI footage shows only people; every product shot is real pixels via motion_clip / product-reveal overlays, so labels never garble), Seedance 2.5 via 即梦画布 CLI (credits) or reAPI (USD), per-video visual styling (fonts, stickers, calc board, end card), aligned captions, Suno BGM, cover image, publish copy and automated QA. Use when the user asks for 抖音/视频号/短视频/带货视频/广告片/口播视频/系列视频, mentions Seedance, 即梦, 画布, reAPI, 字幕, 花字, 封面, 价格卡, 算账板, 产品揭幕, 超分, or wants to turn a product + script into a finished video. Not for long-form editing, live streaming, or pure image tasks.
---

# 抖音 / 视频号 AI 短视频制作

把「一个产品 + 一组卖点」变成可投放的 30 秒竖屏短视频：成片（字幕、花字、价格动画、BGM）+ 封面 + 发布文案。
方法论来自一个 10 条桶装水团购系列的实战（详见仓库 `docs/case-study.md`），核心只有一句话：

> **AI 只拍人和生活，产品画面全部用真实像素。** 视频模型一定会把标签重画成乱码、把“10 桶”画成 20 桶；与其事后裁切、打码补救，不如在分镜阶段就把产品从 AI 画面里拿掉。

## 工具（media 系列 MCP，按能力拆分）

| 服务 | 本技能用到的工具 | 费用 |
|---|---|---|
| **media-jimeng**（即梦画布 CLI，视频默认通道） | `doctor` / `jimeng_quote`（服务端报价）/ `jimeng_submit` / `jimeng_status` / `jimeng_credit` / `jimeng_spend` | 即梦会员积分 |
| **media-reapi** | `music_quote`（Suno BGM）/ `video_options` / `video_quote`（兜底）/ `submit_quote` / `task_status` | 美元 |
| **media-image** | `image_generate`（人物参考、背景、花字、标题，透明底）/ `image_edit` / `image_composite` | 订阅额度 |
| **media-speech** | `subtitles_align`（按脚本对齐字幕）/ `transcribe` / `tts` | 免费 |
| **media-edit** | `motion_clip`（真实产品动画，可透明）/ `broll_insert`（只换画面保留口播）/ `video_compose` / `qa_check` / `contact_sheet` / `still_to_video` / `local_upscale` / `local_job_status` | 免费 |

Claude Code 中工具名为 `mcp__<服务>__<工具>`。没有安装这些 MCP 时，方法论同样适用：用剪映 / ffmpeg / AE 按下文同样的分镜和规则手工完成。

**项目文件夹**：每个项目一个 `<PROJECTS_ROOT>/<项目名>/`（默认 `~/Desktop/Self-media`，见 `settings.json` 的 `projects_root`），含 `使用说明.md`、`素材/`、`文案/`、`产出/`。工具的 `job` 参数填项目名即写入 `<项目>/产出/{images,raw,final}`。开工先读项目的 `使用说明.md`；模板见 [templates/](templates/)。
密钥在 `~/.config/media-mcp/.env`——**不要读取或打印**。

## 铁律

1. **花钱前必须用户确认**。`jimeng_submit`（积分）和 `submit_quote`（美元）会扣费：先把报价单的 `summary` 与 `alternatives` 给用户，得到明确同意后才提交。用户事先授权的预算以内可直接执行，但仍要“尽量少”，事后报告用量。用户说“先不生成 / 只准备”时不调用任何付费工具。
2. **先执行用户的方案，不擅自升级**模型、分辨率或时长；有更优方案时说明差价与理由，由用户选。
3. **AI 画面里不要产品、不要字、不要 BGM**：提示词禁止出现产品/包装/任何文字；“只生成人声和音效，不要背景音乐”。产品、价格、字幕、花字、BGM 一律后期。
4. **数量、价格、品牌名必须真实像素 + 与口播对齐**：说“10 桶”就真的落下 10 桶，说到“一百六”时 160 出现并被划掉。
5. **成片必须过 `qa_check`**（fail 不交付，warn 要看图确认）；交付前自己先看审片图。
6. **一条一条做**：系列视频上一条用户确认后再做下一条；每条的教训写回项目 `使用说明.md`。
7. **片内不放标题美术字、不放“AI 生成”角标**：标题只做封面（片内会盖住人物）；AI 标识与「情景演绎」在平台发布时勾选。

## 工作流（复制此清单逐项勾选）

```
- [ ] 1 开工：doctor（media-jimeng / media-image / media-edit / media-speech / media-reapi）；读项目 使用说明.md；产品事实只取客户资料
- [ ] 2 脚本：前 3 秒钩子 + 痛点/场景 + 价格锚点 + 行动号召，≤30 秒、口播 ≤150 字；系列先写全部选题与脚本给用户审
        → reference/script-and-hooks.md；模板 templates/脚本模板.md
- [ ] 3 分镜：产品零出镜——标出哪几句用「算账板」「产品揭幕」「片尾定版」盖画面 → reference/storyboard-product-zero.md
- [ ] 4 视觉风格：给这条定一套风格（字幕字体/配色、花字、算账板、揭幕效果、片尾）→ reference/visual-styles.md
- [ ] 5 画面素材（image_generate，逐张看图）：每个角色 1 张带情绪的正面半身参考图；算账板/片尾背景；揭幕光效；
        价格花字 3 张 + 亮点花字 1 张 + 封面标题 1 张（透明底，逐字核对，检查是否真透明）
- [ ] 6 提示词：Seedance 结构（素材说明/整体/道具禁忌/声音/画面文字/分镜时间轴/全局约束）→ reference/prompting.md
- [ ] 7 报价：jimeng_quote（默认 480p；原生 720p 须用户确认）+ music_quote（BGM）→ 给用户 summary → 同意后 submit
- [ ] 8 审片：jimeng_status 下载 → contact_sheet 看人物/手指/多余物体 → subtitles_align 核对台词（matched_lines 必须全中）
- [ ] 9 真实像素片段：motion_clip 算账板（按对齐后的口播时间卡点）、motion_clip(transparent=True) 产品揭幕、片尾定版
- [ ] 10 broll_insert：算账板 replace、揭幕 overlay 到对应台词时段（口播不断）
- [ ] 11 成片：video_compose（captions、cards 花字、caption_font/color/stroke、bgm_path，接片尾）→ local_job_status
- [ ] 12 质检：qa_check → 看 review_image（UI 禁区、挡脸、阅读速度、响度）；fail 回到 11
- [ ] 13 交付：封面（scripts/make_cover.py）+ 发布文案（templates/发布文案模板.md）+ 成片工程.json + 使用说明.md 历史行
```

## 产品零出镜分镜（核心方法）

| 模块 | 用在哪句台词 | 做法 |
|---|---|---|
| **算账板**（replace） | “原价 16 一桶，十桶一百六，现在十桶九十九” | `motion_clip`：风格背景 + 文字层逐行弹出 + 划线原价 + 10 个真实产品图逐个落下 + 价格花字弹出；整画面替换 |
| **产品揭幕**（overlay） | “16.8 升，大品牌，喝着放心” | 提示词让人物站画面一侧、掌心向上做展示手势、另一侧留空；`motion_clip(transparent=True)` 做光效 + 真实产品弹出漂浮，叠加在人物镜头上（人物照常动） |
| **片尾定版**（拼接） | 结尾 2–3 秒 | `motion_clip`：产品 + 价格花字 + 行动号召 + 合规小字（如「适用区域以团购页为准」） |

时间点一律取 `subtitles_align` 返回的实际口播时间，而不是脚本预估。详见 [reference/storyboard-product-zero.md](reference/storyboard-product-zero.md)。

**不要做**：在 AI 镜头里给产品特写；用模糊色块、裁切、贴片遮乱码（廉价感）；把产品 PNG 平铺在实拍地板上冒充实景（透视不对，一眼假）。

## 渠道与分辨率（2026-10 实测）

- **视频默认走即梦画布 CLI**（`media-jimeng`）：与 reAPI 同模型同画质，原生 720p 一条 26 秒 520 积分（≈¥22），约为 reAPI 720p 的 44%；服务端报价并以报价为积分上限运行。细节与踩坑见 [reference/jimeng-canvas.md](reference/jimeng-canvas.md)。
- **分辨率**：默认 480p 生成 → 本地放大到 720p；近景/特写多的投放主片建议**原生 720p**（480p 放大后皮肤发蜡、发丝糊），须用户确认。1080p 生成性价比低（64 积分/秒）。
- **模型**：真人出镜带货主片用 Seedance 2.5（一次生成 ≤30 秒，全片声音一致、对口型好）；不用 Mini 系列做真人主片（人物偏假、偏糊）。见 [reference/models-and-pricing.md](reference/models-and-pricing.md)。
- **BGM**：每条一次 Suno（reAPI `music_quote`，$0.066 出 2 首，选 1）。

## 交付标准

| 交付物 | 要求 |
|---|---|
| 成片 `产出/final/<品牌>_<篇名>.mp4` | 1080×1920 或 720×1280（`output_size`）/30fps，约 30 秒（含片尾），-14 LUFS，字幕全中，`qa_check` 无 fail |
| 封面 `…_抖音封面.png` | 表情最夸张的一帧 + 本条标题美术字，放在 3:4 安全区下部，不挡眼睛和嘴 |
| 发布文案 `文案/<篇名>/发布文案.md` | 视频介绍（1–2 句 + emoji + 行动号召）+ 4–6 个话题；发布时勾选 AI 生成、标注情景演绎、挂链接 |
| 工程记录 `文案/<篇名>/成片工程.json` | 渠道、积分/美元、插入时段、字幕样式、QA 结论，便于复刻和重剪 |

## 何时读参考资料

| 场景 | 文件 |
|---|---|
| 写脚本、钩子、系列选题、合规用语、BGM 风格 | [reference/script-and-hooks.md](reference/script-and-hooks.md) |
| 分镜、算账板、产品揭幕、broll_insert 时间 | [reference/storyboard-product-zero.md](reference/storyboard-product-zero.md) |
| 写 Seedance 提示词、人物参考图 | [reference/prompting.md](reference/prompting.md) |
| 每条单独设计风格、花字/贴纸提示词、字体 | [reference/visual-styles.md](reference/visual-styles.md) |
| 字幕、花字位置、UI 禁区、QA 阈值 | [reference/captions-and-layout.md](reference/captions-and-layout.md) |
| 封面与发布文案 | [reference/cover-and-publish.md](reference/cover-and-publish.md) |
| 即梦画布 CLI：安装、报价、积分、坑 | [reference/jimeng-canvas.md](reference/jimeng-canvas.md) |
| 模型、价格、reAPI 路线 | [reference/models-and-pricing.md](reference/models-and-pricing.md) |
| 放大方案 | [reference/upscaling.md](reference/upscaling.md) |
| 出错、结果异常、返工 | [reference/lessons-learned.md](reference/lessons-learned.md) |
| 豆包网页端额度评估 | [reference/doubao-web.md](reference/doubao-web.md) |
| 每日早安/节气等固定 IP 系列 | 改用 `daily-health-video` 技能（如已安装） |

## 最小示例（一条 26 秒原生 720p 带货片）

```
# 1 素材（media-image，逐张看图）
image_generate("竖版写实人像…约35岁中国女性…表情：惊喜地瞪大眼睛…画面中没有任何文字", job="品牌X", name="篇A_妈妈参考", size="1024x1536")
image_generate("真正透明背景…手账风贴纸，只包含文字「10桶99元」…", job="品牌X", name="篇A_花字_10桶99元", size="1536x1024", background="transparent")
# 2 主片（media-jimeng）
jimeng_quote(prompt=<templates/seedance_prompt模板.txt 填好>, job="品牌X", images=[妈妈参考, 爸爸参考], duration=26,
             resolution="720p", confirm_high_res=True, name="篇A_主片")      → 给用户 summary → 同意 → jimeng_submit(quote_id)
music_quote(style="warm acoustic guitar, light claps, 110 bpm, family ad bgm", job="品牌X", title="篇A_bgm", duration=40) → 同意 → submit_quote
jimeng_status(submit_id, job="品牌X", name="篇A_主片")                       # 约 5 分钟
# 3 对齐 + 真实像素（media-speech / media-edit）
subtitles_align(video_path=raw/篇A_主片.mp4, lines=["以前16一桶|以前十六一桶", "现在【10桶99】！|现在十桶九十九", …])
motion_clip(job, name="篇A_算账板", duration=4.6, background=算账板背景, layers=[文字层…, 10×产品图 drop…, 花字 pop…])
motion_clip(job, name="篇A_产品揭幕", duration=2.6, transparent=True, layers=[光效 fade, 产品 pop+bob])
broll_insert(base=raw/篇A_主片.mp4, inserts=[{video:算账板, start:12.9}, {video:产品揭幕.mov, start:22.3}], job, name="篇A_主片_插入")
# 4 成片 + 质检 + 封面
video_compose([主片_插入, 片尾], job, output_name="品牌X_篇A", captions=cues, cards=[花字…],
              caption_font="快乐体", caption_color="#FFFFFF", caption_stroke="#7A4A2A", bgm_path=raw/篇A_bgm_1.mp3)
qa_check(output)  →  python scripts/make_cover.py --video … --t 1.6 --title 篇A_封面标题.png --out …_抖音封面.png
```

## 发布前提醒用户

平台勾选“内容由 AI 生成”；情景剧标注「情景演绎」；划线原价须有真实依据；不写“最、第一、治病、绝对”等极限/功效用语；行动号召改为实际转化方式（团购链接 / 私信 / 到店）。
