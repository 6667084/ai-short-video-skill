"""media 系列 MCP 服务（servers/ 下 6 个）共用：工具注册（异常转 ToolError）、注解常量、参数辅助、schema 内联。"""
from __future__ import annotations

import functools
import logging
import subprocess

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

for _n in ("qcloud_cos", "httpx", "httpcore", "urllib3"):
    logging.getLogger(_n).setLevel(logging.WARNING)

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
READ_NET = ToolAnnotations(read_only_hint=True, open_world_hint=True)
LOCAL_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
NET_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True)
PAID = ToolAnnotations(title="【会扣费】提交报价单", read_only_hint=False, destructive_hint=True, idempotent_hint=False,
                       open_world_hint=True)


def make_tool(mcp):
    """返回绑定到 mcp 服务的 @tool(annotations) 装饰器：把内部异常转成 ToolError——
    MCP 默认会隐藏普通异常的文本，Agent 将看不到可操作的错误提示。"""
    def tool(annotations: ToolAnnotations):
        def deco(fn):
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                try:
                    return fn(*args, **kwargs)
                except ToolError:
                    raise
                except subprocess.CalledProcessError as e:
                    err = (e.stderr or b"")
                    err = err.decode(errors="ignore") if isinstance(err, bytes) else err
                    raise ToolError(f"外部命令失败（{' '.join(map(str, e.cmd[:3]))} …）：{err[-600:]}") from e
                except Exception as e:  # noqa: BLE001 — 统一转为对 Agent 可见的错误
                    raise ToolError(f"{type(e).__name__}: {e}") from e
            return mcp.tool(annotations=annotations)(wrapper)
        return deco
    return tool


def opt(v):
    """空字符串 / 空列表 → None（参数用简单类型，内部再转成可选值）。"""
    return None if v in ("", None, []) else v


def inline_refs(mcp) -> None:
    """把参数 schema 中的 $ref/$defs 展开为内联对象：部分客户端（Gemini 系）不支持 $ref。"""
    def resolve(node, defs):
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(defs[node["$ref"].split("/")[-1]], defs)
            return {k: resolve(v, defs) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [resolve(x, defs) for x in node]
        return node

    for t in mcp._tool_manager.list_tools():
        defs = t.parameters.get("$defs")
        if defs:
            t.parameters = resolve(t.parameters, defs)
