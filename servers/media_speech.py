"""media-speech MCP：语音（免费、本地为主）——whisper 转写、脚本对齐字幕、edge-tts 配音。

所有 media 系列 MCP 服务共用 ms/ 代码与 ~/.config/media-mcp/ 配置；启动：uv --directory ~/media-studio-mcp run python -m servers.media_speech
"""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from ms import morning as _morning
from ms import qa as _qa
from ms import subtitles as _subs
from ms.mcp_util import LOCAL_WRITE, NET_WRITE, READ, READ_NET, inline_refs, make_tool
from ms.mcp_util import opt as _opt

mcp = MCPServer(
    "media-speech",
    instructions=(
        "语音工具：transcribe（whisper 本地转写，视频/音频均可，会议录音、口播核对）；"
        "subtitles_align（按脚本逐字对齐生成字幕，文字 100% 来自脚本，可直接给 media-edit 的 video_compose）；"
        "tts（微软 edge-tts 神经语音配音，免费，自动生成对齐字幕）。"
    ),
)
tool = make_tool(mcp)


@tool(READ_NET)
def doctor(network: bool = True) -> dict:
    """语音自检：ffmpeg、whisper-cli 与模型。返回 healthy 与每项 fix 提示。"""
    return _qa.doctor(network, scope="speech")


@tool(READ)
def transcribe(video_path: str, hint: str = "") -> dict:
    """本地 whisper 识别口播（句级），用于审核生成视频是否念对台词。hint 填台词原文可提高准确率。"""
    return _subs.transcribe(video_path, _opt(hint))


@tool(LOCAL_WRITE)
def subtitles_align(video_path: str, lines: list[str]) -> dict:
    """【字幕首选】按脚本原文生成精准字幕：whisper 逐字时间戳与脚本序列对齐，文字 100% 来自脚本（无错别字）。
    lines 按口播顺序，每行 ≤12 字；显示与读法不同时写成 "显示文字|读法"，如 "10桶要160元|十桶要一百六"。
    返回 cues（可直接作为 video_compose 的 captions）与 .srt；matched_lines 应全部命中，否则检查台词是否被念错。"""
    parsed = [{"show": l.split("|", 1)[0].strip(), "say": l.split("|", 1)[1].strip()} if "|" in l else l.strip()
              for l in lines if l.strip()]
    return _subs.align(video_path, parsed)


@tool(NET_WRITE)
def tts(lines: list[str], job: str, name: str = "voice", voice: str = "", rate: str = "+0%", pitch: str = "+0Hz") -> dict:
    """免费中文配音（微软 edge-tts 神经语音）并直接生成对齐字幕：lines 每项即一条字幕（建议 ≤12 字），
    读法不同时用 "显示|读法"（如 "PM2.5 偏高|PM二点五偏高"）。返回 audio、duration 与 captions（可直接传给 video_compose）。
    voice 空=settings 默认（zh-CN-XiaoxiaoNeural 温暖女声）；可选 zh-CN-XiaoyiNeural（活泼女声）、zh-CN-YunxiaNeural（可爱童声男）、
    zh-CN-YunxiNeural（阳光男声）。rate 如 "+5%"，pitch 如 "+5Hz"。"""
    return _morning.tts(lines, job, name, _opt(voice), rate, pitch)


inline_refs(mcp)

if __name__ == "__main__":
    mcp.run()
