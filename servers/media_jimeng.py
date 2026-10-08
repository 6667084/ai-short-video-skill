"""media-jimeng MCP：即梦视频生成（通道：即梦画布 CLI dreamina-canvas）——按会员积分扣费，服务端报价 → 用户确认 → 带积分上限运行。

启动：uv --directory ~/media-studio-mcp run python -m servers.media_jimeng
"""
from __future__ import annotations

from typing import Literal

from mcp.server.mcpserver import MCPServer

from ms import jimeng as _jm
from ms.mcp_util import LOCAL_WRITE, PAID, READ, READ_NET, inline_refs, make_tool
from ms.mcp_util import opt as _opt

mcp = MCPServer(
    "media-jimeng",
    instructions=(
        "即梦视频生成（Seedance 2.5 等，消耗即梦会员积分），通道为即梦画布 CLI：每个项目一块网页画布，节点与产物可在画布里查看。"
        "流程：doctor → jimeng_quote（在画布保存节点并取服务端权威报价）→ 把 summary 与 alternatives 给用户 → 用户明确同意后 jimeng_submit"
        "（以报价为积分上限运行，超出服务端拒绝）→ jimeng_status 轮询，完成后下载到 <job>/raw/，480p 自动本地放大出 _720p.mp4。"
        "分辨率铁律：默认 480p；720p/1080p 只有用户明确要求或确认后才可传 confirm_high_res=True。"
        "未经用户对该报价单的明确同意，不得调用 jimeng_submit；已提交的任务只用 jimeng_status 查询，不要重新提交。"
    ),
)
tool = make_tool(mcp)


@tool(READ_NET)
def doctor(network: bool = True) -> dict:
    """即梦自检：画布 CLI 是否安装、登录、会员等级；积分余额是否可查。未登录时请用户在终端执行 dreamina-canvas auth login。"""
    return _jm.doctor(network)


@tool(READ_NET)
def jimeng_credit() -> dict:
    """即梦积分余额与会员等级（画布 CLI 无余额接口，借用旧 dreamina CLI；未登录时 total_credit=None 并给出提示）。"""
    return _jm.credit()


@tool(LOCAL_WRITE)
def jimeng_quote(prompt: str, job: str, mode: Literal["m2v", "t2v", "first_last_frame"] = "m2v", model: str = "seedance_2.5",
                 duration: int = 15, ratio: str = "9:16", resolution: Literal["480p", "720p", "1080p"] = "480p",
                 images: list[str] = [], videos: list[str] = [], audios: list[str] = [], first: str = "", last: str = "",
                 confirm_high_res: bool = False, name: str = "", dry_run: bool = False) -> dict:
    """在项目画布上保存视频节点并取服务端权威报价（不扣积分），返回 quote_id、summary（积分、折合人民币、余额）、alternatives、canvas_url。
    mode：m2v=全能参考（images/videos/audios，prompt 里按顺序写 图片1/视频1/音频1，自动换成画布节点引用）；
    first_last_frame=首帧或首尾帧（first 与可选 last）；t2v=纯文本。model 用画布模型名或别名（seedance_2.5 / seedance2.5、
    seedance_2.0_vip、seedance_2.0_mini、wan_3.0 …），模式、分辨率、时长、参考数量按画布实时模型目录校验。
    resolution 默认 480p（完成后自动本地放大到 720p 交付）；720p/1080p 需用户明确要求或确认后传 confirm_high_res=True。
    参考单价（2.5）：480p 12、720p 20、1080p 64 积分/秒，以 summary 中的服务端报价为准。
    同一 job+name 的待确认报价会复用原画布节点（改参数重报价不留孤立节点）。dry_run=True 只做本地校验，不连画布。
    prompt 写明“画面中不要出现任何字幕或文字”“不要背景音乐”；台词用引号。name 为下载后的文件名（不含扩展名）。"""
    return _jm.quote(prompt, job, mode, model, duration, ratio, resolution, images, videos, audios,
                     _opt(first), _opt(last), confirm_high_res, name, dry_run)


@tool(READ)
def jimeng_list_quotes(job: str = "", status: Literal["pending", "submitted", "error", "all"] = "pending") -> dict:
    """列出即梦报价单（默认只看待确认的）。"""
    qs = _jm.list_quotes(_opt(job), None if status == "all" else status)
    return {"count": len(qs), "quotes": qs}


@tool(PAID)
def jimeng_submit(quote_id: str) -> dict:
    """【会扣即梦积分】执行报价单：以报价积分为上限签发确认凭证并运行画布节点，返回 submit_id。
    前置条件：用户已看到该报价单的积分并明确同意。最新服务端价超过上限时拒绝执行；submitId 先落盘，失败后重试复用、不会重复扣费。"""
    return _jm.submit(quote_id)


@tool(READ_NET)
def jimeng_status(submit_id: str, job: str, wait_s: int = 100, name: str = "") -> dict:
    """查询即梦画布任务（最多等 wait_s≤110 秒）；成功后下载到 <job>/raw/<name>.mp4，480p 自动生成 <name>_720p.mp4（deliver 字段即交付用文件）。
    实测 26 秒 720p 约 5 分钟。仍在运行时稍后再查，不要重新提交。"""
    return _jm.status(submit_id, job, wait_s, name)


@tool(READ)
def jimeng_spend(job: str) -> dict:
    """汇总某项目已完成即梦任务的实际积分消耗（来自 tasks.jsonl）。"""
    return _jm.spend(job)


inline_refs(mcp)

if __name__ == "__main__":
    mcp.run()
