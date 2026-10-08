# 放大

## 交付 720p / 1080p 的默认做法（2026-10）

- 即梦 480p 主片：`jimeng_status` 自动 Lanczos 放大出 `_720p.mp4`；`video_compose` 再放到 1080×1920 成片。远景、空镜够用。
- 近景/特写多的投放主片：原生 720p 生成（须用户确认），`video_compose` 自动放到 1080p，≈原生。
- 不要对含文字的画面做 AI 超分（会臆造字形）；产品画面本来就是真实像素，不存在这个问题。
- seedvr2 放大到 1080p 会占满 64GB 内存，不用；realesrgan 30 秒约 25 分钟且皮肤偏涂抹，一般不值得。

## 决策（480p 要到 1080p 时）

| 素材 | 做法 | 费用 |
|---|---|---|
| 720p | 不用单独处理，`video_compose` 自动 Lanczos+锐化 | 免费、瞬时 |
| 480p（无文字特写） | `cloud_upscale_quote(video, job)`（enhance professional）→ 用户确认 → `submit_quote` | ≈$0.042/s |
| 480p 且有产品标签特写 | **不要超分**，改用原生 720p 重新生成（AI 超分会臆造字形） | — |
| 480p 且用户不愿付费 | `local_upscale(video, job, method="realesrgan")` | 免费，约 25 分钟/30s |

## 实测记录（2026-10，M1 Ultra 64GB，同一条 480p 草稿，人脸与产品标签 100% 裁切盲比）

| 方案 | 结果 |
|---|---|
| enhance professional | 最接近原生 1080p：皮肤纹理真实、睫毛清晰；但标签小字被“补”成假字（净含量 16.8L → 麋含量 188L），产品片不可用 |
| enhance standard | 略好于插值，偏软 |
| Topaz 2x | 偏软偏平，只到 960×1708 |
| Real-ESRGAN x4plus | 边缘锐但皮肤“磨皮”塑料感；720p 输入要 ~100 分钟/30s，不值得 |
| Lanczos+锐化（720p 源） | ≈原生，仅略软 |
| SeedVR2 3B（MPS） | 不可用：13 帧 VAE 编码 >7 分钟，内存打满 |
| Apple VTSuperResolution | 需 macOS 26+；26.x 实测高质量模型下载卡 0%、低延迟版限 960×960，不可用 |

## 注意

- 云端处理 reAPI 自己的产物时自动使用其 CDN 原链接；北京 COS 对 enhance 专业档 / Topaz 的海外拉取曾失败（报“无法读取源视频”，不扣费）。
- 输出尺寸可能差 1–2 像素（如 1080×1922），`video_compose` 会裁到 1080×1920。
