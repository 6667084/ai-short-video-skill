"""media-edit MCP：本地视频后期（免费）——成片合成（字幕/花字/封面/模板/BGM 混音）、质检、审片图、本地放大、静态图转视频。

所有 media 系列 MCP 服务共用 ms/ 代码与 ~/.config/media-mcp/ 配置；启动：uv --directory ~/media-studio-mcp run python -m servers.media_edit
"""
from __future__ import annotations

from typing import Literal

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from ms import compose as _compose
from ms import jobs as _jobs
from ms import morning as _morning
from ms import motion as _motion
from ms import qa as _qa
from ms import upscale as _upscale
from ms.mcp_util import LOCAL_WRITE, READ, READ_NET, inline_refs, make_tool
from ms.mcp_util import opt as _opt
from ms.media import contact_sheet as _contact_sheet
from ms.media import probe as _probe

mcp = MCPServer(
    "media-edit",
    instructions=(
        "本地视频后期（全部免费、本机运行）：video_compose 成片（多段拼接、人脸感知字幕/花字/价格卡、封面美术字、品牌模板、BGM 闪避混音、-14 LUFS）→ qa_check 质检；"
        "contact_sheet 审片图；local_upscale 本地放大；still_to_video 静态图转运镜视频；motion_clip 代码生成动画片段（真实产品图/文字图层+入场动效）；"
        "broll_insert 把插入镜头覆盖到主视频时段（只换画面、保留口播）。也适用于纯代码/素材拼接生成的视频。"
        "长任务异步，用 local_job_status 轮询。job 填项目名（产物在 ~/Desktop/Self-media/<项目>/产出/）或绝对路径。成片必须通过 qa_check。"
    ),
)
tool = make_tool(mcp)


class Caption(BaseModel):
    start: float = Field(description="开始秒")
    end: float = Field(description="结束秒")
    text: str = Field(description="字幕文字（建议 ≤12 字）；数字价格自动黄色高亮，【关键词】手动高亮（括号不显示）")


class Card(BaseModel):
    text: str = Field("", description="花字/价格卡文字；\\n 换行后的第二行自动缩小为副标题（图片花字可留空）")
    start: float
    end: float
    y: float = Field(0.16, description="中心高度比例 0–1（会在附近自动避开人脸与抖音 UI 区）")
    size: int = Field(0, description="字号 px（0=默认 84 或模板值）；正文 65–95，标题 130–170")
    color: str = Field("", description="文字颜色（空=默认 #FFE14D 或模板值）")
    stroke: str = Field("", description="描边颜色（空=默认深色或模板值）")
    bg: str = Field("", description="底框颜色，如 #E8202A 或 rgba(0,0,0,0.6)；空=无底框")
    strike: bool = Field(False, description="红色删除线（划线原价）")
    anim: Literal["pop", "slide", "none"] = "pop"
    fixed: bool = Field(False, description="锁定 y，不自动避让")
    font: str = Field("", description="文字花字字体：黑体（默认）/快乐体/黄油体/毛笔/手写/宋体")
    image: str = Field("", description="图片花字：透明底 PNG 路径（如 image_generate(background='transparent') 生成的爆款花字/贴纸）；"
                                       "设置后 size 表示宽度 px（0=640），text/color/bg 忽略，同样自动避让人脸与 UI 区")


@tool(READ_NET)
def doctor(network: bool = True) -> dict:
    """后期自检：ffmpeg、Remotion 与浏览器、字体、人脸检测、Real-ESRGAN、磁盘。返回 healthy 与每项 fix 提示。"""
    return _qa.doctor(network, scope="edit")


@tool(LOCAL_WRITE)
def video_compose(video_paths: list[str], job: str, output_name: str = "final", captions: list[Caption] = [],
                  srt_path: str = "", cards: list[Card] = [], hook_text: str = "", hook_seconds: float = 3.0,
                  bgm_path: str = "", bgm_volume: float = 0.35, bgm_start: float = 0.0, duck: bool = True,
                  keep_original_audio: bool = True, caption_size: int = 72, avoid_faces: bool = True,
                  template: str = "", cover_date: str = "", cover_title: str = "", cover_image: str = "",
                  output_size: Literal["", "1080p", "720p"] = "",
                  caption_font: Literal["", "黑体", "快乐体", "黄油体", "毛笔", "手写", "宋体"] = "", caption_color: str = "",
                  caption_highlight: str = "", caption_stroke: str = "") -> dict:
    """抖音成片（后台任务，返回 local_job_id）：多段拼接 → 1080x1920/30fps（720p 素材自动 Lanczos+锐化放大）→
    人脸感知排版（字幕/花字自动避开五官与抖音 UI 区：顶部 10%、底部 20%、右侧按钮栏）→ Remotion 渲染
    （思源黑体 Heavy、描边投影、数字价格黄色高亮、弹入动画）→ BGM 人声闪避混音（短于成片自动循环）→ -14 LUFS → H.264。
    captions 通常直接用 subtitles_align 返回的 cues；也可给 srt_path。hook_text 为开头大标题（显示 hook_seconds 秒）。
    cards 示例：划线原价 {"text":"10桶 160元","start":14.2,"end":15.8,"size":104,"color":"#FFFFFF","bg":"rgba(0,0,0,0.62)","strike":true}；
    现价 {"text":"10桶 99元\\n平均每桶9.9元","start":15.8,"end":20,"size":150,"bg":"#E8202A"}。
    template：品牌固定版式（templates/<名>.json，如 "douyin-ad"；可自建机构版式：角标 + 底部机构条 + 开场封面 + 固定字幕样式/位置）；
    cover_date（YYYY-MM-DD，自动生成“公历/农历/星期”日期块）与 cover_title（竖排标题，| 分隔列，从右到左，如 "白露耗血润目|桑葚陈皮养血"）。
    cover_image：封面标题美术字 PNG（透明底，如 media-image 的 image_generate(background="transparent") 生成），会自动放到不压人物的位置，日期块同理。
    图片花字：cards 里给 {"image": "<透明底PNG>", "start":..,"end":.., "size": 宽度px}，同样自动避让人脸与 UI 区。
    字幕关键词高亮：字幕文字里用【】包住关键词（如 "景田【大品牌】"），渲染时变高亮色且不显示括号。
    output_size：交付分辨率 1080p（默认）或 720p（模板可指定）。
    caption_font / caption_color / caption_highlight / caption_stroke：覆盖字幕样式，让每条视频有自己的视觉风格（如手账风用 手写 + 深棕字白描边）。bgm 会先统一响度再按 bgm_volume 混入（0.35≈-9dB，人声时轻闪避）。
    完成后必须调用 qa_check(output) 质检。"""
    cards_d = []
    for c in cards:
        d = c.model_dump()
        d["bg"] = d["bg"] or None
        if not d["fixed"]:
            d.pop("fixed")
        for k in ("size", "color", "stroke", "image", "font"):  # 未设置则交给模板/默认值
            if not d[k]:
                d.pop(k)
        cards_d.append(d)
    hook = {"text": hook_text, "seconds": hook_seconds} if hook_text else None
    return _compose.compose(video_paths, job, output_name, [c.model_dump() for c in captions] or None, _opt(srt_path),
                            cards_d, hook, _opt(bgm_path), bgm_volume, bgm_start, duck, keep_original_audio,
                            caption_size, avoid_faces, _opt(template), _opt(cover_date), _opt(cover_title),
                            _opt(cover_image), _opt(output_size),
                            {"font": caption_font, "color": caption_color, "highlight": caption_highlight, "stroke": caption_stroke}
                            if any((caption_font, caption_color, caption_highlight, caption_stroke)) else None)


@tool(LOCAL_WRITE)
def qa_check(video_path: str) -> dict:
    """成片质检（video_compose 之后必做）：规格、时长、响度 -14 LUFS、真峰值、字幕停留与阅读速度（≤9 字/秒）、
    字幕/花字是否压到五官、是否进入抖音 UI 区、黑场与长静音；并生成标出 UI 禁区（红色）的审片图 review_image。
    verdict=fail 时按各项 fix 修正后重新合成；warn 需人工看图确认。"""
    return _qa.qa_check(video_path)


@tool(LOCAL_WRITE)
def contact_sheet(video_path: str, count: int = 9, safe_zone: bool = False) -> dict:
    """均匀抽帧拼成联系表 JPG，用看图工具审片（人物一致性、产品标签、数量、手指、多余文字）。
    safe_zone=True 时叠加抖音 UI 禁区红色标注。"""
    sheet = _qa.safe_zone_sheet(video_path, count) if safe_zone else _contact_sheet(video_path, count)
    return {"contact_sheet": sheet, "duration": round(_probe(video_path)["duration"], 2)}


@tool(LOCAL_WRITE)
def local_upscale(video: str, job: str, method: Literal["auto", "lanczos", "anime", "realesrgan"] = "auto",
                  short_side: int = 1080, name: str = "") -> dict:
    """本地放大（免费，后台运行，返回 local_job_id，用 local_job_status 轮询），保留原音轨。
    auto：≤1.75 倍（720p→1080p）用 Lanczos+锐化，瞬时且≈原生；>1.75 倍（480p→1080p）会报错并建议 cloud_upscale_quote。
    anime：卡通/3D 动画专用（realesr-animevideov3 x2），速度快、线条干净，适合卡通主持人类视频 480p→720p/1080p。
    realesrgan：480p 输入约 25 分钟/30s，皮肤偏磨皮，仅作免费兜底。720p 素材通常不必调用（video_compose 会自动放大）。"""
    return _upscale.local_upscale(video, job, method, short_side, _opt(name))


@tool(READ)
def local_job_status(local_job_id: str, wait_s: int = 60) -> dict:
    """查询本地后台任务（放大 / 成片渲染）状态，最多等待 wait_s≤110 秒。"""
    return _jobs.status(local_job_id, wait_s)


@tool(LOCAL_WRITE)
def still_to_video(image_path: str, job: str, name: str = "still", audio_path: str = "", duration: float = 0,
                   motion: Literal["zoom_in", "zoom_out", "pan_up", "pan_down"] = "zoom_in", tail: float = 1.0) -> dict:
    """静态图 → 1080x1920/30fps 缓慢运镜视频（免费、本地）：自动裁成 9:16，并把 audio_path 作为音轨；
    duration=0 时按音频时长 + tail 秒。输出可直接作为 video_compose 的素材（再叠字幕、标题卡、BGM）。"""
    return _morning.still_to_video(image_path, job, name, _opt(audio_path), duration, motion, tail)


class MotionLayer(BaseModel):
    image: str = Field("", description="透明底 PNG 路径（真实产品图、GPT-image 贴纸/花字）；与 text 二选一")
    text: str = Field("", description="文字图层（\\n 换行）")
    x: float = Field(0.5, description="中心横向位置 0–1")
    y: float = Field(0.5, description="中心纵向位置 0–1")
    w: float = Field(0.4, description="图片宽度占画面宽比例")
    size: int = Field(96, description="文字字号 px")
    color: str = ""
    stroke: str = ""
    bg: str = Field("", description="文字底框颜色，空=无")
    strike: bool = Field(False, description="红色删除线（划线原价）")
    start: float = Field(0, description="出现时间（秒）")
    end: float = Field(0, description="消失时间（秒），0=持续到结尾")
    anim: Literal["pop", "drop", "slide_left", "slide_right", "slide_up", "fade", "none"] = "pop"
    bob: bool = Field(False, description="入场后轻微上下浮动")
    rotate: float = 0
    shadow: bool = True
    z: int = Field(0, description="叠放层级，大的在上；同层按列表顺序")
    font: str = Field("", description="文字层字体：黑体（默认）/快乐体/黄油体/毛笔/手写/宋体")


class Insert(BaseModel):
    video: str = Field(description="插入镜头路径（still_to_video / motion_clip 输出等）")
    start: float = Field(description="覆盖到主视频的开始秒")
    duration: float = Field(0, description="覆盖时长秒，0=插入镜头全长")


@tool(LOCAL_WRITE)
def motion_clip(job: str, name: str, duration: float, layers: list[MotionLayer], background: str = "",
                bg_color: str = "#FFFFFF",
                bg_motion: Literal["zoom_in", "zoom_out", "pan_up", "pan_down", "none"] = "zoom_in",
                transparent: bool = False) -> dict:
    """代码生成动画片段（免费、本地 Remotion，后台任务）：背景图缓慢运镜 + 图层逐个入场（pop 弹出 / drop 落下 / slide 滑入 / fade）。
    transparent=True：透明背景（输出 .mov），交给 broll_insert 叠加在主视频上——AI 镜头里人物做展示手势、画面一侧留空，
    真实产品 + 光效从空位弹出，人物照常在动（比整画面切到产品图更自然）。
    用途：产品必须准确的镜头——真实产品图做“10 桶逐个落下”的数量动画、算账公式、价格对比、结尾定版；
    背景可用 image_generate 生成的空场景（不含产品）。输出 1080x1920/30fps 带静音轨，可直接用于 video_compose 或 broll_insert。
    返回 local_job_id，用 local_job_status 轮询。"""
    return _motion.motion_clip(job, name, duration, [l.model_dump() for l in layers], _opt(background), bg_color, bg_motion,
                               transparent)


@tool(LOCAL_WRITE)
def broll_insert(base: str, inserts: list[Insert], job: str, name: str = "broll", freeze_tail: float = 0,
                 trim_end: float = 0) -> dict:
    """把插入镜头覆盖到主视频的指定时段：只换画面，保留主视频音轨（口播不断，J-cut）。
    用途：AI 主片里产品画错/标签乱码的时段，用真实像素的 still_to_video / motion_clip 片段盖住。
    插入片段带透明通道（motion_clip(transparent=True) 的 .mov）时改为叠加：主视频画面保留，只在透明区域外显示插入内容。
    freeze_tail=末帧定格延长秒数（音轨补静音，用于结尾定版）；trim_end=先把主视频裁到该秒（0=不裁）。"""
    return _motion.broll_insert(base, [i.model_dump() for i in inserts], job, name, freeze_tail, trim_end)


inline_refs(mcp)

if __name__ == "__main__":
    mcp.run()
