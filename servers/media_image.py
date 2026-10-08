"""media-image MCP：GPT-image 生图/改图 + 产品真实像素合成（海报、封面、产品图、贴纸、视频参考图与首帧）。

所有 media 系列 MCP 服务共用 ms/ 代码与 ~/.config/media-mcp/ 配置；启动：uv --directory ~/media-studio-mcp run python -m servers.media_image
需要公网链接时用 cos-storage 服务的 cos_upload。
"""
from __future__ import annotations

from typing import Literal

from mcp.server.mcpserver import MCPServer

from ms import images as _images
from ms import qa as _qa
from ms.mcp_util import LOCAL_WRITE, NET_WRITE, READ_NET, inline_refs, make_tool, opt

mcp = MCPServer(
    "media-image",
    instructions=(
        "GPT-image 生图/改图（走本地订阅代理，不计 reAPI 费用）与产品真实像素合成（image_composite，防标签乱码）。"
        "job 填项目名（产物在 ~/Desktop/Self-media/<项目>/产出/images/）或绝对路径。"
        "生成后必须看图确认；画面中需要准确的文字（价格、卖点）一律后期叠加，不让模型画。"
    ),
)
tool = make_tool(mcp)


@tool(READ_NET)
def doctor(network: bool = True) -> dict:
    """生图自检：密钥配置、代理、GPT-image 接口。返回 healthy 与每项 fix 提示。"""
    return _qa.doctor(network, scope="image")


@tool(NET_WRITE)
def image_generate(prompt: str, job: str = "misc", name: str = "image", size: str = "1024x1536", n: int = 1,
                   quality: str = "", model: str = "", background: Literal["", "transparent", "opaque"] = "") -> dict:
    """GPT-image 文生图（默认 gpt-image-2.5，走本地订阅代理，不计 reAPI 费用），保存到 <job>/images/。
    job：项目名（→ ~/Desktop/Self-media/<项目>/产出/）或绝对路径。
    size: 1024x1536（竖版）| 1536x1024 | 1024x1024 | auto。quality: low/medium/high（空=默认）。单张 30–90 秒，并发 ≤2。
    人物参考图请生成【单人正面半身照】，三视图在 Seedance 中容易生成“双胞胎”。
    background="transparent" 输出透明底 PNG（封面标题美术字、花字贴纸，可直接用于 video_compose 的 cover_image / cards.image）。"""
    return _images.generate(prompt, job, name, size, n, opt(quality), opt(model), opt(background))


@tool(NET_WRITE)
def image_edit(prompt: str, image_paths: list[str], job: str = "misc", name: str = "edit", size: str = "1024x1536",
               quality: str = "", model: str = "", background: Literal["", "transparent", "opaque"] = "") -> dict:
    """GPT-image 参考图生图/改图：传定妆照生成同一人物的新姿势/新场景，传产品图做摆放正确的场景首帧，或做局部修改。
    产品标签小字会被重画，若要做视频首帧，生成后必须放大核对标签。background="transparent" 输出透明底。"""
    return _images.edit(prompt, image_paths, job, name, size, opt(quality), opt(model), opt(background))


@tool(LOCAL_WRITE)
def image_composite(background: str, overlay: str, job: str = "misc", name: str = "composite", height: float = 0.45,
                    x: float = 0.5, bottom: float = 0.9, rotate: int = 0, shadow: bool = True,
                    harmonize: float = 0.0) -> dict:
    """【防标签乱码】把透明底产品图的真实像素贴进场景图（本地、免费、不经 AI 重画），用作图生视频首帧（video_quote 的 first_frame）
    或封面底图。height=产品高度占画面比例（标签特写 0.6–0.8）；x=中心横向位置；bottom=底边位置（0–1）；
    rotate=顺时针角度（180 = 桶装水倒扣在饮水机上，标签随之倒置，符合实物）；shadow=加接地阴影；
    harmonize=0–1 把产品色温/亮度向场景靠拢（暖光生活场景建议 0.15–0.3；过高会让蓝色产品发灰；影棚场景 0），只调色、不重画标签。
    背景图建议先用 image_generate 生成“没有产品的空场景”（同样竖版 1024x1536），产品摆放位置在提示词里预留。"""
    return _images.composite(background, overlay, job, name, height, x, bottom, rotate, shadow, harmonize)


inline_refs(mcp)

if __name__ == "__main__":
    mcp.run()
