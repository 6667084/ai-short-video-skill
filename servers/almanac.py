"""almanac MCP：日历与天气——公历/农历、节气与物候、传统节日、当地天气与生活提醒（免费）。

所有 media 系列 MCP 服务共用 ms/ 代码与 ~/.config/media-mcp/ 配置；启动：uv --directory ~/media-studio-mcp run python -m servers.almanac
"""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from ms import morning as _morning
from ms import qa as _qa
from ms.mcp_util import READ_NET, inline_refs, make_tool
from ms.mcp_util import opt as _opt

mcp = MCPServer(
    "almanac",
    instructions=(
        "日历与天气：morning_context(date, city) 返回公历、农历、节气（当天/下一个及天数）、物候、传统节日、当地天气与穿衣/出行提醒。"
        "适用于早安/节气内容选题、日程文案、活动排期等。"
    ),
)
tool = make_tool(mcp)


@tool(READ_NET)
def doctor(network: bool = True) -> dict:
    """日历/天气自检：代理可达。返回 healthy 与每项 fix 提示。"""
    return _qa.doctor(network, scope="almanac")


@tool(READ_NET)
def morning_context(date: str = "", city: str = "") -> dict:
    """早安视频的素材上下文：公历/星期/农历/节日、当前节气与物候、距下个节气天数、节气知识（气候、生活提醒、应季食物、
    画面元素），以及该城市当天天气（Open-Meteo：天气、最高最低温、温差、降水概率、紫外线、风、湿度、PM2.5）与自动提醒 weather_alerts。
    date 为 YYYY-MM-DD，空=自动（15 点后默认明天）；city 空=用 settings.json 的 morning.city。返回 writing_guide 与 IP 形象设置 mascot。"""
    return _morning.context(_opt(date), _opt(city))


inline_refs(mcp)

if __name__ == "__main__":
    mcp.run()
