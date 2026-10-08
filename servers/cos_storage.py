"""cos-storage MCP：腾讯云 COS 文件存储——本地文件 → 公网 URL（通用）。

用途：视频/图片模型的参考素材中转、文档/报告/网页/图片分享链接、代码生成的视频或附件外发等。
对象统一放在 Agent/<folder>/ 下，30 天自动清理；对象名带随机串防枚举。
所有 media 系列 MCP 服务共用 ms/ 代码与 ~/.config/media-mcp/ 配置；启动：uv --directory ~/media-studio-mcp run python -m servers.cos_storage
"""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from ms import cos as _cos
from ms import qa as _qa
from ms.mcp_util import NET_WRITE, READ_NET, inline_refs, make_tool

mcp = MCPServer(
    "cos-storage",
    instructions=(
        "COS 文件存储：cos_upload 把本地文件（任意类型：图片、视频、PDF/Word/Excel、HTML、压缩包）上传为公网 URL，"
        "用于给外部模型/服务传素材或给用户分享链接。链接任何人可访问、30 天后自动删除；"
        "敏感文件（含个人信息、密钥、合同）上传前先征得用户同意。cos_list 查看已上传，cos_delete 提前删除。"
    ),
)
tool = make_tool(mcp)
DELETE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True)


@tool(READ_NET)
def doctor(network: bool = True) -> dict:
    """COS 自检：密钥配置、代理、桶访问。返回 healthy 与每项 fix 提示。"""
    return _qa.doctor(network, scope="cos")


@tool(NET_WRITE)
def cos_upload(local_path: str, folder: str = "misc", job: str = "") -> dict:
    """上传本地文件到 COS，返回公网 URL（Agent/<folder>/<时间>-<随机串>.<扩展名>，30 天自动删除）。
    folder：分组名（只保留字母数字_-，如 "docs"、"report"）；job：视频项目名（填了则上传缓存记在项目里，同一文件 25 天内复用链接）。
    同一文件重复上传会直接复用已有链接（reused=true）。"""
    return _cos.upload(local_path, job or None, folder or None)


@tool(READ_NET)
def cos_list(folder: str = "", limit: int = 50) -> dict:
    """列出已上传的对象（新的在前），含 key、url、大小、修改时间。folder 空 = 全部。"""
    return _cos.list_objects(folder, limit)


@tool(DELETE)
def cos_delete(key: str) -> dict:
    """删除一个对象（key 取自 cos_upload / cos_list 的返回值，只能删 Agent/ 前缀下的）。用于提前撤回分享链接。"""
    return _cos.delete(key)


inline_refs(mcp)

if __name__ == "__main__":
    mcp.run()
