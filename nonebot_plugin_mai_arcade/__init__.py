"""
NoneBot2 舞萌DX机厅插件
提供机厅人数上报、附近机厅查找、线上排卡、Nearcade云同步等功能支持
"""

import datetime
import json
from pathlib import Path

from nonebot import require, get_driver, logger, on_command, on_message
from nonebot.plugin import PluginMetadata
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageEvent
from nonebot.params import EventMessage

from .config import data_json, re_write_json, arcade_marker_file
from .handlers import arcade, queue, alias, maps, admin, count
from .messaging import (
    command_button,
    escape_markdown,
    install_markdown_matcher,
    install_module_matchers,
    safe_link_button,
    reply_spec,
)
from .services import call_discover


__plugin_meta__ = PluginMetadata(
    name="nonebot_plugin_mai_arcade",
    description="NoneBot2插件 为舞萌玩家提供机厅人数上报、附近机厅查找、线上排卡、Nearcade云同步等功能支持",
    usage="使用 机厅help 指令获取使用说明",
    type="application",
    homepage="https://github.com/Amia-Mizuki-Dev-Team/Amia-plugin-mai-arcade",
    supported_adapters={"~onebot.v11"},
)

# 获取调度器和驱动器
scheduler = require('nonebot_plugin_apscheduler').scheduler
driver = get_driver()


# 数据清理函数
async def ensure_daily_clear():
    """On startup or first message after restart, clear stale data if daily reset hasn't run yet."""
    # Today's date in Asia/Shanghai
    today = datetime.datetime.now().date().isoformat()

    try:
        marker = json.loads(arcade_marker_file.read_text(encoding='utf-8'))
    except Exception:
        marker = {}

    if marker.get('cleared_date') == today:
        return  # already cleared today

    # Not cleared yet today -> perform clear
    await clear_data_daily()


@scheduler.scheduled_job('cron', hour=0, minute=0)
async def clear_data_daily():
    """Reset per-arcade counts once per day (Asia/Shanghai). Also persists a daily marker."""
    global data_json
    # Determine today's date in Asia/Shanghai; fall back to local if zoneinfo missing
    today = datetime.datetime.now().date().isoformat()

    # Clear counters
    for group_id, arcades in data_json.items():
        for arcade_name, info in arcades.items():
            if 'last_updated_by' in info:
                info['last_updated_by'] = None
            if 'last_updated_at' in info:
                info['last_updated_at'] = None
            if 'num' in info:
                info['num'] = []

    # Persist changes and write marker
    try:
        await re_write_json()
    except Exception:
        pass
    try:
        arcade_marker_file.write_text(json.dumps({'cleared_date': today}, ensure_ascii=False), encoding='utf-8')
    except Exception:
        pass
                
    logger.info("arcade缓存清理完成")


# 启动时事件处理
@driver.on_startup
async def _on_startup_clear():
    await ensure_daily_clear()


# 帮助命令处理器
arcade_help = on_command("机厅help", aliases={"机厅帮助", "arcade help"}, priority=100, block=True)


@arcade_help.handle()
async def handle_arcade_help(event: GroupMessageEvent, message: Message = EventMessage()):
    await arcade_help.send(
        reply_spec(
            "# 舞萌机厅助手\n\n"
            "## 人数查询\n"
            "- `<机厅名>++` / `<机厅名>--`：机厅人数加一或减一\n"
            "- `<机厅名>+num/-num`：调整指定人数\n"
            "- `<机厅名>=num`：重置当前人数\n"
            "- `<机厅名>几` / `几人` / `j`：查询人数和预计等待\n"
            "- `mai` / `机厅人数`：查看今日已更新机厅\n\n"
            "## 机厅管理\n"
            "- `添加群聊` / `删除群聊`（管理员）\n"
            "- `添加机厅` / `删除机厅`（管理员）\n"
            "- `机厅列表`：查看本群机厅\n"
            "- `添加机厅别名` / `删除机厅别名`（管理员）\n"
            "- `机厅别名`：查看别名\n"
            "- `添加机厅地图` / `删除机厅地图`（管理员）\n"
            "- `机厅地图`：查看地图网址\n\n"
            "## 排卡\n"
            "- `排卡 <机厅名>`：加入队列\n"
            "- `排卡现状 <机厅名>`：查看队列\n"
            "- `上机` / `延后` / `退勤`\n"
            "- `闭店 <机厅名>`（管理员）\n\n"
            "> 点击指令按钮后，请确认输入框中的指令并发送。",
            fallback_text=(
                "机厅人数:\n"
                "[<机厅名>++/--] 机厅的人数+1/-1\n"
                "[<机厅名>+num/-num] 机厅的人数+num/-num\n"
                "[<机厅名>=num/<机厅名>num] 机厅的人数重置为num\n"
                "[<机厅名>几/几人/j] 展示机厅当前的人数信息\n"
                "[mai/机厅人数] 展示当日已更新的所有机厅的人数列表\n"
                "群聊管理:\n"
                "[添加群聊] (管理)将群聊添加到JSON数据中\n"
                "[删除群聊] (管理)从JSON数据中删除指定的群聊\n"
                "机厅管理:\n"
                "[添加机厅] (管理)将机厅添加到群聊\n"
                "[删除机厅] (管理)从群聊中删除指定的机厅\n"
                "[机厅列表] 展示当前机厅列表\n"
                "[添加机厅别名 <机厅名> <别名>] (管理)为机厅添加别名\n"
                "[删除机厅别名 <机厅名> <别名/序号>] (管理)移除机厅的别名\n"
                "[机厅别名 <机厅名>] 展示机厅别名\n"
                "[添加机厅地图 <机厅名> <地图URL>] (管理)添加机厅地图信息\n"
                "[删除机厅地图 <机厅名> <地图URL/序号>] (管理)移除机厅地图信息\n"
                "[机厅地图 <机厅名>] 展示机厅音游地图\n"
                "排卡功能:\n"
                "[上机] 将当前第一位排队的移至最后\n"
                "[排卡] 加入排队队列\n"
                "[退勤] 从排队队列中退出\n"
                "[排卡现状] 展示当前排队队列的情况\n"
                "[延后] 将自己延后一位\n"
                "[闭店] (管理)清空排队队列\n"
                "索引支持:\n"
                "机厅名、别名、地图URL均可用序号代替 (使用 机厅列表 命令查看)\n"
                "示例：删除机厅别名 1 2 (删除第1个机厅的第2个别名)\n"
            ),
            rows=(
                (
                    command_button("机厅列表", "机厅列表", button_id="arcade_help_list", style=1),
                    command_button("今日人数", "机厅人数", button_id="arcade_help_count", style=4),
                ),
                (
                    command_button(
                        "添加机厅",
                        "添加机厅",
                        button_id="arcade_help_add",
                        permission=1,
                        style=0,
                    ),
                    command_button("排卡帮助", "排卡现状", button_id="arcade_help_queue", style=1),
                ),
            ),
        )
    )


# 位置监听器
location_listener = on_message(priority=100, block=False)


@location_listener.handle()
async def handle_location_listener(event: MessageEvent):
    """处理位置消息，自动发现附近机厅"""
    for seg in event.message:
        if seg.type == "json":
            try:
                # 解析 CQ:json 的 data
                cq_data = json.loads(seg.data["data"])
                location = cq_data.get("meta", {}).get("Location.Search", {})

                lat = float(location.get("lat", 0))
                lon = float(location.get("lng", 0))
                title = location.get("name", "未知位置")

                if not lat or not lon:
                    raise Exception("<UNK>")

                result, web_url = await call_discover(lat, lon, radius=10, name=title)

                shops = result.get("shops", [])
                if not shops:
                    detail_button = safe_link_button(
                        "打开详情", web_url, button_id="arcade_location_empty"
                    )
                    await location_listener.finish(
                        reply_spec(
                            "# 附近没有找到机厅\n\n详情页可查看附近搜索结果。",
                            fallback_text=f"附近没有找到机厅\n👉 详情可查看：{web_url}",
                            rows=((detail_button,),) if detail_button else (),
                        )
                    )
                    return

                reply_lines = []
                for shop in shops[:3]:  # 只展示 3 个，避免刷屏
                    name = shop.get("name", "未知机厅")
                    dist_val = shop.get("distance", 0)
                    dist_str = f"{dist_val * 1000:.0f}米" if isinstance(dist_val, (int, float)) else "未知距离"
                    shop_addr = shop.get("address", {}).get("detailed", "")
                    reply_lines.append(
                        f"🎮 {escape_markdown(name)}（{dist_str}）\n"
                        f"📍 {escape_markdown(shop_addr)}"
                    )

                reply = "\n\n".join(reply_lines) + f"\n\n👉 更多详情请点开：{web_url}"
                detail_button = safe_link_button(
                    "打开详情", web_url, button_id="arcade_location_more"
                )
                await location_listener.finish(
                    reply_spec(
                        "# 附近机厅\n\n" + "\n\n".join(reply_lines),
                        fallback_text=reply,
                        rows=((detail_button,),) if detail_button else (),
                    )
                )

            except Exception as e:
                raise


# ``Matcher.finish`` and ``Matcher.pause`` call the class' ``send`` method.
# Installing the adapter after all upstream matchers have been declared keeps
# the cloned handlers unchanged while covering every user-visible reply.
install_module_matchers(arcade, queue, alias, maps, admin, count)
install_markdown_matcher(arcade_help)
install_markdown_matcher(location_listener)
