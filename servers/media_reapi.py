"""media-reapi MCP：reAPI 云端生成（视频 / 音乐 / 云超分）——唯一会扣费的服务，报价单 → 用户确认 → 提交。

所有 media 系列 MCP 服务共用 ms/ 代码与 ~/.config/media-mcp/ 配置；启动：uv --directory ~/media-studio-mcp run python -m servers.media_reapi
"""
from __future__ import annotations

from typing import Literal

from mcp.server.mcpserver import MCPServer

from ms import qa as _qa
from ms import reapi as _reapi
from ms.mcp_util import LOCAL_WRITE, NET_WRITE, PAID, READ, READ_NET, inline_refs, make_tool
from ms.mcp_util import opt as _opt

mcp = MCPServer(
    "media-reapi",
    instructions=(
        "reAPI 云端生成：视频（Seedance / Wan 等）、音乐（Suno）、云端超分。唯一会扣费的服务。"
        "流程：video_options(use_case=…) 按用途定画质档并比较成片总价 → 与用户确认方案 → video_quote / music_quote / cloud_upscale_quote 出报价单 → 把 summary 与 alternatives 给用户 → 用户明确同意后 submit_quote → task_status 轮询下载到 <job>/raw/。"
        "job 填项目名（产物在 ~/Desktop/Self-media/<项目>/产出/）或绝对路径；本地素材提交时自动经 COS 中转。"
        "铁律：未经用户对该报价单的明确同意，不得调用 submit_quote；不得擅自升级模型或分辨率。"
    ),
)
tool = make_tool(mcp)


@tool(READ_NET)
def doctor(network: bool = True) -> dict:
    """reAPI 自检：密钥、余额、价格目录新鲜度、COS 中转与代理。返回 healthy 与每项 fix 提示。"""
    return _qa.doctor(network, scope="reapi")


@tool(READ)
def video_options(duration: int, ref_video_seconds: float = 0, max_usd: float = 0,
                  use_case: Literal["", "ad_people", "talking_head", "scenery", "draft", "series"] = "") -> dict:
    """【选型第一步】列出已适配模型在该时长下各分辨率的费用，每行含 total_1080p_usd（生成 + 放大到 1080p 的总价）与路线：
    720p→本地免费放大（≈原生）；480p→云端专业超分。ref_video_seconds 为白模/参考视频秒数（计费，并检查 Wan 的 15s/30s 限制）。
    use_case 先定画质档（每行 fit=推荐/不推荐）：ad_people=真人出镜/生活场景带货投放主片（只用 Seedance 2.5，不用 Mini）；
    talking_head=非主片单人口播（≥Seedance 2.0/Wan）；scenery=空镜/动态背景；draft=草稿；series=已验证的固定 IP。
    规则：在推荐档内比价；720p 比 480p 贵出的部分 < 云超分费时选 720p。有产品标签/包装文字特写时排除 480p+云超分路线。
    给用户 2–3 档（均在推荐范围内）及差价，由用户选择。max_usd=0 表示不限。"""
    return _reapi.options(duration, ref_video_seconds, max_usd or None, use_case)


@tool(READ)
def video_models() -> dict:
    """reAPI 全部视频模型目录（每秒价格区间、时长范围、是否已适配 video_quote）。未适配模型可用 raw_quote。"""
    models = _reapi.models_summary()
    return {"count": len(models), "models": models}


@tool(NET_WRITE)
def refresh_catalog() -> dict:
    """从 reapi.ai 重新抓取模型目录与实时价格（约 30 秒）。doctor 提示目录超过 7 天或报价异常时执行。"""
    import catalog

    d = catalog.build()
    return {"updated": d["updated"], "models": len(d["models"])}


@tool(LOCAL_WRITE)
def video_quote(prompt: str, job: str,
                family: Literal["wan-3.0", "wan-3.0-prime", "seedance-2.5", "seedance-2.0", "seedance-2.0-mini"] = "wan-3.0",
                variant: Literal["standard", "eco", "fast"] = "standard", duration: int = 15, size: str = "9:16",
                resolution: str = "720P", images: list[str] = [], videos: list[str] = [], audios: list[str] = [],
                first_frame: str = "", last_frame: str = "", draft: bool = False, generate_audio: bool = True,
                seed: int = -1, extra: dict[str, str] = {}) -> dict:
    """生成视频报价单（不上传、不扣费），返回 quote_id、summary、alternatives（同模型其他分辨率的 1080p 成片总价）。
    把 summary 与 alternatives 一起给用户，用户确认后才能 submit_quote。
    variant：seedance-2.5 有 standard/eco；seedance-2.0 有 standard/fast/eco；seedance-2.0-mini 有 standard/eco；wan 只有 standard。
    resolution：seedance 用 480p/720p/1080p；wan 用 480P/720P/1080P/2K/4K。时长：seedance-2.5/wan 最长 30s，其余 15s。
    images/videos/audios：本地路径或 URL，prompt 里按顺序称 图片1/视频1/音频1（Wan 用 Image 1/Video 1）。
    白模 → videos；人物/产品/场景图 → images。台词用引号包裹。prompt 必须写明“画面中不要出现任何字幕或文字”“不要背景音乐”。
    seed=-1 表示随机。extra 只能放不影响计费的原生字段（如 {"omni_reference_task_type":"reference"}）。"""
    return _reapi.quote_video(prompt, job, family, variant, duration, size, resolution, images, videos, audios,
                              _opt(first_frame), _opt(last_frame), draft, generate_audio, None if seed < 0 else seed, extra)


@tool(LOCAL_WRITE)
def finalize_quote(draft_task_id: str, job: str) -> dict:
    """Seedance 2.5 草稿 → 1080p 成片的报价单。带参考视频的草稿升级会重复计费参考秒数，通常不如“720p+本地放大”。"""
    return _reapi.quote_finalize(draft_task_id, job)


@tool(LOCAL_WRITE)
def music_quote(style: str, job: str, title: str = "BGM", duration: int = 40, instrumental: bool = True,
                lyrics: str = "", negative_tags: str = "vocals, slow intro") -> dict:
    """Suno V6 背景音乐报价单（一次 2 条，约 $0.066）。style 用英文标签并含 BPM，例如
    "upbeat pop electronic, 128 bpm, punchy drums, claps, bright synth, e-commerce ad bgm"。duration 比成片长约 10s。"""
    return _reapi.quote_music(style, job, title, duration, instrumental, _opt(lyrics), _opt(negative_tags))


@tool(LOCAL_WRITE)
def cloud_upscale_quote(video: str, job: str, provider: Literal["enhance", "topaz"] = "enhance",
                        tier: str = "professional", resolution: str = "1080p") -> dict:
    """云端超分报价单（480p 素材用；720p 素材无需超分）。推荐 enhance + professional（30s≈$1.27，实测最接近原生 1080p）；
    enhance standard≈$0.13 偏软；topaz（tier=standard）偏软且只到 2x。"""
    return _reapi.quote_cloud_upscale(video, job, provider, tier, resolution)


@tool(LOCAL_WRITE)
def raw_quote(model_id: str, params: dict[str, str], job: str, estimate_usd: float = -1) -> dict:
    """未适配模型的原始请求报价单（POST /videos/generations）。params 为字段→值（数字/布尔也用字符串，内部自动转换；
    列表用 JSON 字符串）。*_url / *_urls 字段里的本地路径会自动上传。先用 video_models 查价并估算 estimate_usd（-1=未知，须告知用户）。"""
    import json as _json

    def conv(v: str):
        try:
            return _json.loads(v)
        except Exception:
            return v

    return _reapi.quote_raw(model_id, {k: conv(v) for k, v in params.items()}, job,
                            None if estimate_usd < 0 else estimate_usd)


@tool(READ)
def list_quotes(job: str = "", status: Literal["pending", "submitted", "error", "cancelled", "all"] = "pending") -> dict:
    """列出报价单（默认只看待确认的）。"""
    qs = _reapi.list_quotes(_opt(job), None if status == "all" else status)
    return {"count": len(qs), "quotes": qs}


@tool(PAID)
def submit_quote(quote_id: str, allow_resubmit: bool = False) -> dict:
    """【会扣费】执行报价单：自动上传本地素材 → 提交 reAPI → 返回 task_id。
    前置条件：用户已看到该报价单的 summary 与金额并明确同意。提交前会检查余额；同一报价单加锁防重复提交。"""
    return _reapi.submit(quote_id, allow_resubmit)


@tool(READ_NET)
def task_status(task_id: str, job: str, wait_s: int = 90, name: str = "") -> dict:
    """查询 reAPI 任务（最多等待 wait_s≤110 秒）；完成后自动下载产物到 <job>/raw/ 并返回实际花费。
    processing 时稍后再调；Seedance 30s 通常 3–8 分钟；失败任务自动退款。"""
    return _reapi.task_status(task_id, job, wait_s, _opt(name))


@tool(READ_NET)
def reapi_balance() -> dict:
    """reAPI 余额（1 credit = $0.001）。"""
    return _reapi.balance()


@tool(READ)
def spend_summary(job: str = "") -> dict:
    """按任务汇总 reAPI 实际花费（来自已完成任务的计费记录）。job 为空时汇总全部任务。"""
    return _reapi.spend_summary(_opt(job))


inline_refs(mcp)

if __name__ == "__main__":
    mcp.run()
