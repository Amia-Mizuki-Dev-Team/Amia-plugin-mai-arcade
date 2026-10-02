"""
人数管理模块
"""

import re
import json
import math
import datetime
import http.client

from nonebot import on_regex, get_plugin_config, on_endswith
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Event
from nonebot.params import T_State

from ..config import Config, data_json, re_write_json, block_group
from ..messaging import (
    command_button,
    escape_markdown,
    reply_spec,
    safe_link_button,
)
config = get_plugin_config(Config)


def _nearcade_shop_path(shop_id: str) -> str:
    """Return the current Nearcade shop API route.

    Nearcade no longer serves the old ``/shops/bemanicn/<id>`` API route for
    shop details and attendance.  The public API is keyed by the numeric shop
    id directly, while the ``bemanicn`` segment is only part of legacy data
    URLs.  Keep the route in one place so count reporting and querying cannot
    drift apart again.
    """

    return f"/api/shops/{shop_id}"


def _nearcade_attendance_path(shop_id: str) -> str:
    """Return the current Nearcade attendance API route."""

    return f"{_nearcade_shop_path(shop_id)}/attendance"


_COUNT_INPUT_REGEX = r"^([^\s]+?)\s*(==\d+|={1}\d+|\+\+\d+|--\d+|\+\+|--|[+-]?\d+)?$"
_COUNT_INPUT_PATTERN = re.compile(r"^([^\s]+?)([+\-=]{0,2})(\d*)$")


# This broad expression also matches the standalone ``机厅人数`` command.
# Let lower-priority full-match handlers receive inputs that are not count
# updates; a successful count handler still terminates the event via finish().
sv_arcade = on_regex(_COUNT_INPUT_REGEX, priority=100, block=False)
sv_arcade_on_fullmatch = on_endswith(("几", "几人", "j"), ignorecase=False, priority=100)


def _count_reply(message: str, arcade_name: str, map_url: str | None = None):
    """Add safe, reusable count/queue actions to a rendered count result."""

    safe_name = escape_markdown(arcade_name)
    map_button = (
        safe_link_button(
            "查看地图",
            map_url,
            button_id="arcade_nearcade_link",
            style=0,
        )
        if map_url
        else None
    )
    return reply_spec(
        f"# {safe_name}\n\n{escape_markdown(message)}",
        fallback_text=message,
        rows=(
        (
                command_button(
                    "人数 +1",
                    f"{arcade_name}++",
                    button_id="arcade_count_plus_1",
                    style=4,
                ),
                command_button(
                    "人数 -1",
                    f"{arcade_name}--",
                    button_id="arcade_count_minus_1",
                    style=0,
                ),
            ),
            (
                command_button(
                    "刷新人数",
                    f"{arcade_name}几",
                    button_id="arcade_count_refresh",
                    style=1,
                ),
                command_button(
                    "加入排卡",
                    f"排卡 {arcade_name}",
                    button_id="arcade_queue_join",
                    style=0,
                ),
            ),
            (
                command_button(
                    "排卡现状",
                    f"排卡现状 {arcade_name}",
                    button_id="arcade_queue_status",
                    style=1,
                ),
                *([map_button] if map_button is not None else []),
            ),
        ),
    )


@sv_arcade.handle()
async def handle_sv_arcade(bot: Bot, event: GroupMessageEvent, state: T_State):
    global data_json

    input_str = event.raw_message.strip()
    group_id = str(event.group_id)
    current_time = datetime.datetime.now().strftime("%H:%M")

    match = _COUNT_INPUT_PATTERN.match(input_str)
    if not match:
        return

    name, op, num_str = match.groups()
    num = int(num_str) if num_str else None

    if (not op) and (num is None):
        return

    if group_id not in data_json:
        return

    found = False
    if name in data_json[group_id]:
        found = True
    else:
        for arcade_name, arcade_info in data_json[group_id].items():
            if "alias_list" in arcade_info and name in arcade_info["alias_list"]:
                name = arcade_name
                found = True
                break

    if not found:
        return

    arcade_data = data_json[group_id][name]
    num_list = arcade_data.setdefault("num", [])
    current_num = sum(num_list) if num_list else 0

    if op in ("++", "+"):
        delta = num if num else 1
        if abs(delta) > 50:
            await sv_arcade.finish("检测到非法数值，拒绝更新")
        new_num = current_num + delta
        if new_num < 0 or new_num > 100:
            await sv_arcade.finish("检测到非法数值，拒绝更新")
    elif op in ("--", "-"):
        delta = -(num if num else 1)
        if abs(delta) > 50:
            await sv_arcade.finish("检测到非法数值，拒绝更新")
        new_num = current_num + delta
        if new_num < 0 or new_num > 100:
            await sv_arcade.finish("检测到非法数值，拒绝更新")
    elif op in ("==", "=") or (op == "" and num is not None):
        new_num = num
        if new_num < 0 or new_num > 100:
            await sv_arcade.finish("检测到非法数值，拒绝更新")
        delta = 0
        num_list.clear()
        num_list.append(new_num)
    else:
        return

    arcade_data["last_updated_by"] = event.sender.nickname
    arcade_data["last_updated_at"] = current_time
    arcade_data.pop("previous_update_by", None)
    arcade_data.pop("previous_update_at", None)

    try:
        shop_id = re.search(r'/(\d+)/?$', arcade_data['map'][0]).group(1)
    except KeyError:
        num_list.clear()
        num_list.append(new_num)
        await re_write_json()
        await sv_arcade.finish(
            _count_reply(
                f"[{name}] 当前人数更新为 {new_num}\n由 {event.sender.nickname} 于 {current_time} 更新",
                name,
                (arcade_data.get("map") or [None])[0],
            )
        )

    shop_id = re.search(r'/(\d+)/?$', arcade_data['map'][0]).group(1)
    conn = http.client.HTTPSConnection("nearcade.cn")
    conn.request("GET", _nearcade_attendance_path(shop_id))
    res = conn.getresponse()
    if res.status != 200:
        await sv_arcade.finish(f"获取 shop {shop_id} 云端出勤人数失败: {res.status}")
    else:
        raw_data = res.read().decode("utf-8")
        data = json.loads(raw_data)
        regnum = data["total"]
        if regnum == current_num:
            if group_id in block_group:
                return
        elif op in ("++", "+", "--", "-"):
            # A delta command means "change the current Nearcade count".
            # When the local cache is stale, apply the delta to the cloud
            # value exactly once.  The previous code added the cloud delta to
            # an already adjusted local value, so a local 1 -> ``--`` against
            # a cloud 0 could persist -1.
            # Cloud state is authoritative when the local cache is stale.
            # Clamp a delta at the supported [0, 100] range so ``--`` from a
            # stale local 1 against cloud 0 settles at 0 instead of either
            # persisting a negative value or leaving the stale cache behind.
            new_num = max(0, min(100, regnum + delta))
        # Persist one canonical absolute value after cloud reconciliation.
        # Delta operations intentionally do not mutate ``num_list`` before
        # this point, so a rejected stale-cloud update cannot leave a partial
        # value such as [1, -1] in memory or on disk.
        num_list.clear()
        num_list.append(new_num)
    conn = http.client.HTTPSConnection("nearcade.cn")
    conn.request("GET", _nearcade_shop_path(shop_id))
    res = conn.getresponse()
    if res.status != 200:
        await sv_arcade.finish(f"获取 shop {shop_id} 信息失败: {res.status}")
    raw_data = res.read().decode("utf-8")
    data = json.loads(raw_data)
    game_id = data["shop"]["games"][0]["gameId"]
    coutnum = 0
    for game in data["shop"]["games"]:
        if game["name"] == "maimai DX":
            coutnum = game.get("quantity", 1)
    arcade_data["coutnum"] = coutnum
    await re_write_json()

    per_round_minutes = 16
    players_per_round = max(int(coutnum), 1) * 2  # 每轮最多游玩人数（至少按1台计算）
    queue_num = max(int(new_num) - players_per_round, 0)  # 等待人数（不包含正在玩的这一轮）

    if queue_num > 0:
        expected_rounds = queue_num / players_per_round  # 平均轮数（允许小数）
        min_rounds = queue_num // players_per_round  # 乐观整数轮（可能为0）
        max_rounds = math.ceil(queue_num / players_per_round)  # 保守整数轮

        wait_time_avg = round(expected_rounds * per_round_minutes)
        wait_time_min = int(min_rounds * per_round_minutes)
        wait_time_max = int(max_rounds * per_round_minutes)

        smart_tip = config.count_smart_tips[-1].tip
        for rule in config.count_smart_tips:
            if wait_time_avg <= rule.max_minutes:
                smart_tip = rule.tip
                break

        msg = (
            f"📍 {name}  人数已更新为 {new_num}\n"
            f"🕹️ 机台数量：{coutnum} 台（每轮 {players_per_round} 人）\n\n"
            f"⌛ 预计等待：约 {wait_time_avg} 分钟\n"
            f"   ↳ 范围：{wait_time_min}~{wait_time_max} 分钟（{min_rounds}~{max_rounds} 轮）\n\n"
            f"💡 {smart_tip}"
        )
    else:
        smart_tip = config.count_smart_tips[0].tip
        msg = (
            f"📍 {name}  人数已更新为 {new_num}\n"
            f"🕹️ 机台数量：{coutnum} 台（每轮 {players_per_round} 人）\n\n"
            f"{smart_tip}"
        )

    payload = json.dumps({
        "games": [
            {"id": game_id, "currentAttendances": new_num}
        ]
    })
    headers = {
        f'Authorization': f'Bearer {config.nearcade_api_token}',
        'Content-Type': 'application/json'
    }

    try:
        conn = http.client.HTTPSConnection("nearcade.cn", timeout=10)
        conn.request("POST", _nearcade_attendance_path(shop_id), payload, headers)
        res = conn.getresponse()
        raw_data = res.read().decode("utf-8")
    except Exception as e:
        raw_data = str(e)
        res = None

    if res is not None and 200 <= res.status < 300:
        if group_id in block_group:
            return
        else:
            await sv_arcade.finish(
                _count_reply(
                    f"感谢使用，机厅人数已上传 Nearcade\n{msg}",
                    name,
                    (arcade_data.get("map") or [None])[0],
                )
            )
    elif res is not None and res.status == 400:
        if group_id in block_group:
            return
        else:
            await sv_arcade.finish(
                _count_reply(
                    f"似乎在Nearcade上这家店关门了😴\n{msg}",
                    name,
                    (arcade_data.get("map") or [None])[0],
                )
            )
    else:
        if group_id in block_group:
            return
        status_text = res.status if res is not None else "请求失败"
        await sv_arcade.finish(
            _count_reply(
                f"上传失败: {status_text}\n返回信息: {raw_data}\n\n{msg}",
                name,
                (arcade_data.get("map") or [None])[0],
            )
        )


@sv_arcade_on_fullmatch.handle()
async def handle_sv_arcade_on_fullmatch(bot: Bot, event: Event, state: T_State):
    global data_json

    input_str = event.raw_message.strip()
    group_id = str(event.group_id)

    # 使用on_endswith后，直接提取机厅名称部分
    if input_str.endswith("几人"):
        name_part = input_str[:-2].strip()
    elif input_str.endswith("几"):
        name_part = input_str[:-1].strip()
    elif input_str.endswith("j"):
        name_part = input_str[:-1].strip()
    else:
        return

    if group_id in data_json:
        found_arcade = None
        if name_part in data_json[group_id]:
            found_arcade = name_part
        else:
            for arcade_name, arcade_info in data_json[group_id].items():
                alias_list = arcade_info.get("alias_list", [])
                if name_part in alias_list:
                    found_arcade = arcade_name
                    break

        if found_arcade:
            arcade_info = data_json[group_id][found_arcade]
            num_list = arcade_info.setdefault("num", [])
            try:
                shop_id = re.search(r'/(\d+)/?$', arcade_info['map'][0]).group(1)
                conn = http.client.HTTPSConnection("nearcade.cn")
                conn.request("GET", _nearcade_attendance_path(shop_id))
                res = conn.getresponse()
                if res.status != 200:
                    await sv_arcade_on_fullmatch.finish(f"获取 shop {shop_id} 云端出勤人数失败: {res.status}")
                raw_data = res.read().decode("utf-8")
                data = json.loads(raw_data)
                regnum = data["total"]
                num_list = num_list
                current_num = sum(num_list)
                if regnum == current_num:
                    if group_id in block_group:
                        return
                    last_updated_by = arcade_info.get("last_updated_by")
                    last_updated_at = arcade_info.get("last_updated_at")
                else:
                    cha = current_num - regnum
                    num_list.clear()
                    num_list.append(regnum)
                    current_num = sum(num_list)
                    if group_id in block_group:
                        if arcade_info.get("alias_list"):
                            jtname = arcade_info["alias_list"][0]
                        else:
                            jtname = found_arcade
                        await sv_arcade_on_fullmatch.finish(f"{jtname}+{cha}")
                    else:
                        last_updated_by = "Nearcade"
                        last_updated_at = "None"
                if not num_list:
                    await sv_arcade_on_fullmatch.finish(
                        f"[{found_arcade}] 今日人数尚未更新\n你可以爽霸机了\n快去出勤吧！")
                else:
                    coutnum = arcade_info.get("coutnum", 1)
                    per_round_minutes = 16
                    players_per_round = max(int(coutnum), 1) * 2  # 每轮最多游玩人数（至少按1台计算）
                    queue_num = max(int(current_num) - players_per_round, 0)  # 等待人数（不包含正在玩的这一轮）

                    if queue_num > 0:
                        expected_rounds = queue_num / players_per_round
                        min_rounds = queue_num // players_per_round
                        max_rounds = math.ceil(queue_num / players_per_round)

                        wait_time_avg = round(expected_rounds * per_round_minutes)
                        wait_time_min = int(min_rounds * per_round_minutes)
                        wait_time_max = int(max_rounds * per_round_minutes)

                        smart_tip = config.count_smart_tips[-1].tip
                        for rule in config.count_smart_tips:
                            if wait_time_avg <= rule.max_minutes:
                                smart_tip = rule.tip
                                break

                        msg = (
                            f"📍 {found_arcade}  人数为 {current_num}\n"
                            f"🕹️ 机台数量：{coutnum} 台（每轮 {players_per_round} 人）\n\n"
                            f"⌛ 预计等待：约 {wait_time_avg} 分钟\n"
                            f"   ↳ 范围：{wait_time_min}~{wait_time_max} 分钟（{min_rounds}~{max_rounds} 轮）\n\n"
                            f"💡 {smart_tip}"
                        )
                    else:
                        smart_tip = config.count_smart_tips[0].tip
                        msg = (
                            f"📍 {found_arcade}  人数为 {current_num}\n"
                            f"🕹️ 机台数量：{coutnum} 台（每轮 {players_per_round} 人）\n\n"
                            f"{smart_tip}"
                        )

                    if last_updated_at and last_updated_by:
                        msg += f"\n（{last_updated_by} · {last_updated_at}）"

                    await sv_arcade_on_fullmatch.finish(
                        _count_reply(
                            msg,
                            found_arcade,
                            (arcade_info.get("map") or [None])[0],
                        )
                    )
            except KeyError:
                if not num_list:
                    await sv_arcade_on_fullmatch.finish(
                        f"[{found_arcade}] 今日人数尚未更新\n你可以爽霸机了\n快去出勤吧！")
                else:
                    current_num = sum(num_list)
                    last_updated_by = arcade_info.get("last_updated_by")
                    last_updated_at = arcade_info.get("last_updated_at")
                    await re_write_json()
                    coutnum = arcade_info.get("coutnum", 1)
                    per_round_minutes = 16
                    players_per_round = max(int(coutnum), 1) * 2  # 每轮最多游玩人数（至少按1台计算）
                    queue_num = max(int(current_num) - players_per_round, 0)  # 等待人数（不包含正在玩的这一轮）

                    if queue_num > 0:
                        expected_rounds = queue_num / players_per_round
                        min_rounds = queue_num // players_per_round
                        max_rounds = math.ceil(queue_num / players_per_round)

                        wait_time_avg = round(expected_rounds * per_round_minutes)
                        wait_time_min = int(min_rounds * per_round_minutes)
                        wait_time_max = int(max_rounds * per_round_minutes)

                        smart_tip = config.count_smart_tips[-1].tip
                        for rule in config.count_smart_tips:
                            if wait_time_avg <= rule.max_minutes:
                                smart_tip = rule.tip
                                break

                        msg = (
                            f"📍 {found_arcade}  人数为 {current_num}\n"
                            f"🕹️ 机台数量：{coutnum} 台（每轮 {players_per_round} 人）\n\n"
                            f"⌛ 预计等待：约 {wait_time_avg} 分钟\n"
                            f"   ↳ 范围：{wait_time_min}~{wait_time_max} 分钟（{min_rounds}~{max_rounds} 轮）\n\n"
                            f"💡 {smart_tip}"
                        )
                    else:
                        smart_tip = config.count_smart_tips[0].tip
                        msg = (
                            f"📍 {found_arcade}  人数为 {current_num}\n"
                            f"🕹️ 机台数量：{coutnum} 台（每轮 {players_per_round} 人）\n\n"
                            f"{smart_tip}"
                        )

                    if last_updated_at and last_updated_by:
                        msg += f"\n（{last_updated_by} · {last_updated_at}）"

                    await sv_arcade_on_fullmatch.finish(
                        _count_reply(
                            msg,
                            found_arcade,
                            (arcade_info.get("map") or [None])[0],
                        )
                    )
        else:
            # await sv_arcade_on_fullmatch.finish(f"群聊 '{group_id}' 中不存在机厅或机厅别名 '{name_part}'")
            return
    else:
        # await sv_arcade_on_fullmatch.finish(f"群聊 '{group_id}' 中不存在任何机厅")
        return
