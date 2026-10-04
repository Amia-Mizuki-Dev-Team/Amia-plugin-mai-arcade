"""
机厅管理模块
"""

import datetime
from nonebot import on_command, on_regex, on_fullmatch
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Event, Message
from nonebot.params import CommandArg, T_State, EventMessage
from nonebot.permission import SUPERUSER
from nonebot.adapters.onebot.v11.permission import GROUP_ADMIN, GROUP_OWNER

from ..config import data_json, re_write_json, search_sessions
from ..utils import resolve_arcade_name, format_shop_info, is_superuser_or_admin, get_shop_url
from ..services import search_nearcade_shops, auto_add_arcade_map
from ..messaging import command_button, escape_markdown, reply_spec, safe_link_button


add_arcade = on_command("添加机厅", priority=10, block=True)
delete_arcade = on_command("删除机厅", priority=10, block=True)
show_arcade = on_command("机厅列表", aliases={"群机厅"}, priority=10, block=True)
query_updated_arcades = on_fullmatch(("mai", "机厅人数", "jtj", "机厅几人"), ignorecase=False, priority=10, block=True)
search_choice = on_regex(r"^[1-6]$", priority=10, block=True)


def _search_reply_spec(
    shops: list[dict], query: str, total: int, user_id: str, page: int = 1
):
    """Render the existing 1-6 search flow as Markdown with user-bound buttons."""

    lines = ["# 搜索机厅", "", f"关键词：`{escape_markdown(query)}`", ""]
    for index, shop in enumerate(shops[:3], 1):
        name = escape_markdown(shop.get("name", "未知机厅"))
        address = escape_markdown(shop.get("address", {}).get("detailed", "地址未知"))
        lines.extend((f"{index}. **{name}**", f"   - 地址：{address}"))
    if page > 1:
        lines.insert(1, f"第 {page} 页")
    lines.extend(("", "> 请选择下方候选。按钮仅限本次查询的发起者使用。"))

    def choice(label: str, value: str, style: int, button_id: str):
        return command_button(
            label,
            value,
            button_id=button_id,
            permission=0,
            user_ids=(user_id,),
            style=style,
            visited_label=label,
        )

    rows = [
        tuple(
            choice(f"选择 {index}", str(index), 1, f"arcade_search_select_{index}")
            for index in range(1, min(len(shops), 2) + 1)
        )
    ]
    if len(shops) >= 3:
        rows.append((choice("选择 3", "3", 1, "arcade_search_select_3"),))
    if total > len(shops):
        rows.append((choice("更多结果", "4", 0, "arcade_search_more"),))
    rows.append((choice("使用原名", "5", 0, "arcade_search_original"),))
    rows.append((choice("取消", "6", 3, "arcade_search_cancel"),))

    fallback_lines = [f"🔍 找到 {len(shops)} 个相关机厅：", ""]
    for index, shop in enumerate(shops[:3], 1):
        fallback_lines.append(format_shop_info(shop, index))
        fallback_lines.append("")
    fallback_lines.extend(("请选择操作：", "1️⃣2️⃣3️⃣ 选择对应机厅"))
    if total > len(shops):
        fallback_lines.append("4️⃣ 查看更多结果")
    fallback_lines.extend(("5️⃣ 直接添加原名称", "6️⃣ 取消操作"))

    return reply_spec(
        "\n".join(lines),
        fallback_text="\n".join(fallback_lines),
        rows=tuple(rows),
    )


@add_arcade.handle()
async def handle_add_arcade(bot: Bot, event: GroupMessageEvent, state: T_State, name_: Message = CommandArg()):
    """添加机厅 - 支持搜索nearcade机厅或直接添加"""
    global data_json, search_sessions
    name = str(name_).strip()
    group_id = str(event.group_id)
    user_id = str(event.user_id)
    
    if group_id in data_json:
        if not await is_superuser_or_admin(bot, event):
            await add_arcade.finish(f"只有管理员能够添加机厅")
        if not name:
            state["waiting_for_name"] = True
            await add_arcade.pause(f"请输入机厅名称：")
        else:
            await process_add_arcade(name, group_id, user_id)
    else:
        await add_arcade.finish(f"本群尚未开通排卡功能,请联系群主或管理员添加群聊")


@add_arcade.got("waiting_for_name")
async def got_arcade_name(bot: Bot, event: GroupMessageEvent, state: T_State):
    """获取机厅名称输入"""
    name = str(event.get_message()).strip()
    group_id = str(event.group_id)
    user_id = str(event.user_id)
    
    if name in data_json[group_id]:
        await add_arcade.finish(f"机厅已在群聊中")
    
    await process_add_arcade(name, group_id, user_id)


async def process_add_arcade(name: str, group_id: str, user_id: str):
    """处理添加机厅的逻辑"""
    global data_json, search_sessions
    
    # 搜索nearcade机厅
    search_result = await search_nearcade_shops(name)
    shops = search_result.get('shops', [])
    
    if shops:
        # 存储搜索会话
        session_key = f"{group_id}_{user_id}"
        search_sessions[session_key] = {
            'shops': shops,
            'query': name,
            'page': 1,
            'total': search_result.get('totalCount', 0)
        }
        
        # 格式化搜索结果
        await add_arcade.finish(
            _search_reply_spec(
                shops,
                name,
                search_result.get("totalCount", 0),
                user_id,
            )
        )
    else:
        # 没有搜索结果，直接添加
        tmp = {"list": []}
        data_json[group_id][name] = tmp
        await re_write_json()
        await add_arcade.finish(f"未找到相关机厅，已直接添加 '{name}' 到群聊名单中")


@search_choice.handle()
async def handle_search_choice(bot: Bot, event: GroupMessageEvent):
    """处理搜索结果选择"""
    global data_json, search_sessions
    choice = str(event.get_message()).strip()
    group_id = str(event.group_id)
    user_id = str(event.user_id)
    session_key = f"{group_id}_{user_id}"
    
    if session_key not in search_sessions:
        return
    
    session = search_sessions[session_key]
    shops = session['shops']
    query = session['query']
    
    if choice == "5":
        # 直接添加原名称
        if query not in data_json[group_id]:
            tmp = {"list": []}
            data_json[group_id][query] = tmp
            await re_write_json()
            await search_choice.finish(f"✅ 已添加机厅：{query}")
        else:
            await search_choice.finish(f"机厅 '{query}' 已在群聊中")
        
        # 清除会话
        del search_sessions[session_key]
    elif choice == "6":
        # 取消操作
        del search_sessions[session_key]
        await search_choice.finish("❌ 已取消添加操作")
    elif choice in ["1", "2", "3"]:
        # 选择机厅
        idx = int(choice) - 1
        if idx < len(shops):
            shop = shops[idx]
            shop_name = shop.get('name', query)
            shop_url = get_shop_url(shop)
            
            # 检查机厅是否已存在
            if shop_name not in data_json[group_id]:
                # 添加机厅
                tmp = {"list": []}
                data_json[group_id][shop_name] = tmp
                await re_write_json()
                
                # 尝试自动添加地图
                map_added = await auto_add_arcade_map(group_id, shop_name, shop_url)
                
                result_msg = f"✅ 已添加机厅：{shop_name}"
                if shop_url:
                    result_msg += f"\n🔗 详情链接：{shop_url}"
                if map_added:
                    result_msg += f"\n🗺️ 已添加机厅地图"

                detail_button = safe_link_button(
                    "查看详情", shop_url, button_id="arcade_search_shop_link"
                ) if shop_url else None
                
                await search_choice.finish(
                    reply_spec(
                        f"# 已添加机厅\n\n`{escape_markdown(shop_name)}`",
                        fallback_text=result_msg,
                        rows=((detail_button,),) if detail_button else (),
                    )
                )
            else:
                await search_choice.finish(f"机厅 '{shop_name}' 已在群聊中")
            
            del search_sessions[session_key]
    elif choice == "4":
        # 查看更多结果
        if session['total'] > len(shops):
            next_page = session['page'] + 1
            search_result = await search_nearcade_shops(query, next_page)
            new_shops = search_result.get('shops', [])
            
            if new_shops:
                session['shops'] = new_shops
                session['page'] = next_page
                
                await search_choice.finish(
                    _search_reply_spec(
                        new_shops,
                        query,
                        search_result.get("totalCount", 0),
                        user_id,
                        page=next_page,
                    )
                )
            else:
                await search_choice.finish("没有更多结果了")
        else:
            await search_choice.finish("没有更多结果了")


@delete_arcade.handle()
async def handle_delete_arcade(bot: Bot, event: GroupMessageEvent, state: T_State, name_: Message = CommandArg()):
    """删除机厅 - 支持机厅名称或序号"""
    name = str(name_).strip()
    group_id = str(event.group_id)
    
    if group_id not in data_json:
        await delete_arcade.finish(f"本群尚未开通排卡功能")
    
    if not await is_superuser_or_admin(bot, event):
        await delete_arcade.finish(f"只有管理员能够删除机厅")
    
    if not name:
        state["waiting_for_name"] = True
        await delete_arcade.pause(f"请输入要删除的机厅名称/序号：")
    else:
        await process_delete_arcade(name, group_id)


@delete_arcade.got("waiting_for_name")
async def got_delete_arcade_name(bot: Bot, event: GroupMessageEvent, state: T_State):
    """获取要删除的机厅名称输入"""
    name = str(event.get_message()).strip()
    group_id = str(event.group_id)
    
    await process_delete_arcade(name, group_id)


async def process_delete_arcade(name: str, group_id: str):
    """处理删除机厅的逻辑"""
    # 解析机厅名称（支持序号）
    arcade_name = resolve_arcade_name(name, group_id)
    
    if arcade_name not in data_json[group_id]:
        await delete_arcade.finish(f"机厅不在群聊中或为机厅别名，请先添加该机厅或使用该机厅本名")
    
    del data_json[group_id][arcade_name]
    await re_write_json()
    await delete_arcade.finish(f"已从群聊名单中删除机厅：{arcade_name}")


@show_arcade.handle()
async def handle_show_arcade(bot: Bot, event: GroupMessageEvent):
    """显示机厅列表"""
    global data_json
    group_id = str(event.group_id)
    if group_id in data_json:
        msg = "机厅列表如下：\n"
        num = 0
        for n in data_json[group_id]:
            msg = msg + str(num + 1) + "：" + n + "\n"
            num = num + 1
        markdown = "# 机厅列表\n\n" + "\n".join(
            f"{index + 1}. **{escape_markdown(name)}**"
            for index, name in enumerate(data_json[group_id])
        )
        await show_arcade.finish(
            reply_spec(
                markdown,
                fallback_text=msg.rstrip("\n"),
                rows=(
                    (
                        command_button("添加机厅", "添加机厅", permission=1, style=0),
                        command_button("今日人数", "机厅人数", style=4),
                    ),
                ),
            )
        )
    else:
        await show_arcade.finish(f"本群尚未开通排卡功能,请联系群主或管理员添加群聊")


@query_updated_arcades.handle()
async def handle_query_updated_arcades(bot: Bot, event: GroupMessageEvent):
    """查询今日已更新的机厅人数"""
    global data_json
    group_id = str(event.group_id)
    
    if group_id not in data_json:
        fallback = "本群尚未开通排卡功能，请联系群主或管理员添加群聊"
        await query_updated_arcades.finish(
            reply_spec(
                "# 今日机厅人数\n\n" + escape_markdown(fallback),
                fallback_text=fallback,
            )
        )
        return
    
    updated_arcades = []
    for arcade_name, arcade_info in data_json[group_id].items():
        if 'num' in arcade_info and arcade_info['num']:
            current_num = sum(arcade_info['num'])
            last_updated_by = arcade_info.get("last_updated_by", "未知")
            last_updated_at = arcade_info.get("last_updated_at", "未知")
            updated_arcades.append({
                'name': arcade_name,
                'num': current_num,
                'updated_by': last_updated_by,
                'updated_at': last_updated_at
            })
    
    if not updated_arcades:
        fallback = "📋 今日机厅人数更新情况\n\n暂无更新记录\n您可以爽霸机了"
        await query_updated_arcades.finish(
            reply_spec(
                "# 今日机厅人数\n\n暂无更新记录\n您可以爽霸机了",
                fallback_text=fallback,
            )
        )
        return
    
    reply_messages = []
    for arcade in updated_arcades:
        line = f"[{arcade['name']}] {arcade['num']}人 \n（{arcade['updated_by']} · {arcade['updated_at']}）"
        reply_messages.append(line)
    
    header = "📋 今日机厅人数更新情况\n\n"
    markdown_lines = ["# 今日机厅人数", ""]
    for arcade in updated_arcades:
        markdown_lines.extend(
            (
                f"- **{escape_markdown(arcade['name'])}**：{arcade['num']} 人",
                f"  - 更新：{escape_markdown(arcade['updated_by'])} · "
                f"{escape_markdown(arcade['updated_at'])}",
            )
        )
    await query_updated_arcades.finish(
        reply_spec(
            "\n".join(markdown_lines),
            fallback_text=header + "\n".join(reply_messages),
        )
    )
