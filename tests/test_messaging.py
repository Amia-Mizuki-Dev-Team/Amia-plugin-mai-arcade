"""Focused tests for the official Markdown/Keyboard adapter."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from pathlib import Path

import nonebot
import pytest
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.exception import ActionFailed, NetworkError


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MAI_ARCADE_MARKDOWN_MODE", "on")
nonebot.init(_env_file=None)

# The source checkout is intentionally a NoneBot plugin at the repository
# root, whose directory contains hyphens for Amia's naming convention. Load it
# under a test-only import alias so relative imports remain package-correct.
_spec = importlib.util.spec_from_file_location(
    "amia_plugin_mai_arcade",
    PLUGIN_ROOT / "__init__.py",
    submodule_search_locations=[str(PLUGIN_ROOT)],
)
assert _spec and _spec.loader
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)

from amia_plugin_mai_arcade import arcade_help  # noqa: E402
from amia_plugin_mai_arcade.config import plugin_config  # noqa: E402
from amia_plugin_mai_arcade.handlers.arcade import _search_reply_spec  # noqa: E402
from amia_plugin_mai_arcade.handlers.count import (  # noqa: E402
    _count_reply,
    _COUNT_INPUT_PATTERN,
    _nearcade_attendance_path,
    _nearcade_shop_path,
    sv_arcade,
)
from amia_plugin_mai_arcade.handlers.arcade import query_updated_arcades  # noqa: E402
from amia_plugin_mai_arcade.utils import get_shop_url  # noqa: E402
from amia_plugin_mai_arcade.messaging import (  # noqa: E402
    build_markdown_segment,
    command_button,
    escape_markdown,
    link_button,
    markdown_enabled,
    reply_spec,
    safe_link_button,
)


def test_official_nested_markdown_and_keyboard_shape() -> None:
    segment = build_markdown_segment(
        reply_spec(
            "# 舞萌机厅助手\n\n请选择操作。",
            rows=(
                (
                    command_button("机厅列表", "机厅列表", button_id="list", style=1),
                    link_button("官方地图", "https://example.com/map", button_id="map"),
                ),
            ),
        )
    )

    assert segment.type == "markdown"
    payload = segment.data["data"]
    assert payload["markdown"]["content"].startswith("# 舞萌机厅助手")
    button = payload["keyboard"]["content"]["rows"][0]["buttons"]
    assert button[0]["action"]["type"] == 2
    assert button[0]["action"]["permission"]["type"] == 2
    assert button[1]["action"]["type"] == 0
    assert button[1]["action"]["data"] == "https://example.com/map"
    assert all(item["render_data"]["style"] in {0, 1, 3, 4} for item in button)


def test_user_bound_search_button_permission() -> None:
    segment = build_markdown_segment(
        reply_spec(
            "# 搜索机厅",
            rows=(
                (
                    command_button(
                        "选择 1",
                        "1",
                        permission=0,
                        user_ids=("10001",),
                        button_id="select-1",
                    ),
                ),
            ),
        )
    )

    permission = segment.data["data"]["keyboard"]["content"]["rows"][0]["buttons"][0][
        "action"
    ]["permission"]
    assert permission == {"type": 0, "specify_user_ids": ["10001"]}


def test_search_and_count_payloads_keep_existing_flows() -> None:
    search = build_markdown_segment(
        _search_reply_spec(
            [
                {"name": "万达广场风云再起", "address": {"detailed": "上海市杨浦区"}},
                {"name": "万达影城游艺区", "address": {"detailed": "上海市宝山区"}},
                {"name": "万达宝贝王", "address": {"detailed": "上海市闵行区"}},
            ],
            "万达",
            total=5,
            user_id="10001",
        )
    )
    search_rows = search.data["data"]["keyboard"]["content"]["rows"]
    assert len(search_rows) == 5
    assert all(
        button["action"]["permission"] == {"type": 0, "specify_user_ids": ["10001"]}
        for row in search_rows
        for button in row["buttons"]
    )

    count = build_markdown_segment(
        _count_reply(
            "当前人数为 8",
            "星河机厅",
            "https://nearcade.cn/shops/12345",
        )
    )
    count_rows = count.data["data"]["keyboard"]["content"]["rows"]
    assert count_rows[0]["buttons"][0]["action"]["data"] == "星河机厅++"
    assert count_rows[0]["buttons"][1]["action"]["data"] == "星河机厅--"
    assert count_rows[0]["buttons"][0]["id"] == "arcade_count_plus_1"
    assert count_rows[0]["buttons"][1]["id"] == "arcade_count_minus_1"
    assert count_rows[1]["buttons"][0]["action"]["data"] == "星河机厅几"
    assert count_rows[1]["buttons"][0]["id"] == "arcade_count_refresh"
    assert count_rows[2]["buttons"][1]["action"]["type"] == 0
    assert count_rows[2]["buttons"][1]["id"] == "arcade_nearcade_link"
    assert (
        count_rows[2]["buttons"][1]["action"]["data"]
        == "https://nearcade.cn/shops/12345"
    )
    assert all(
        button["action"]["permission"]["type"] == 2
        for row in count_rows
        for button in row["buttons"]
    )


def test_nearcade_shop_url_uses_current_canonical_route() -> None:
    assert (
        get_shop_url({"source": "bemanicn", "id": 17490})
        == "https://nearcade.cn/shops/17490"
    )
    assert get_shop_url({"id": None}) == ""


def test_nearcade_api_paths_use_current_shop_id_routes() -> None:
    shop_id = "13030"
    assert _nearcade_shop_path(shop_id) == "/api/shops/13030"
    assert _nearcade_attendance_path(shop_id) == "/api/shops/13030/attendance"


def test_dynamic_markdown_values_escape_protocol_delimiters() -> None:
    assert escape_markdown("店名 [A]*_`~\\") == r"店名 \[A\]\*\_\`\~\\"


def test_count_command_accepts_nearcade_names_with_full_width_punctuation() -> None:
    match = _COUNT_INPUT_PATTERN.match("天空之城（南京栖霞城北万象汇店）++")
    assert match is not None
    assert match.groups() == ("天空之城（南京栖霞城北万象汇店）", "++", "")


def test_standalone_count_summary_is_not_blocked_by_broad_count_regex() -> None:
    # ``sv_arcade`` accepts a broad no-op match for ``机厅人数``.  It must not
    # block the dedicated lower-priority summary matcher in that case.
    assert sv_arcade.block is False
    assert query_updated_arcades.block is True


def test_button_validation_rejects_unsupported_values() -> None:
    with pytest.raises(ValueError, match="10 个字符"):
        command_button("按钮文本超过十个字符啦", "test")
    with pytest.raises(ValueError, match="0、1、3 或 4"):
        command_button("测试", "test", style=2)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="http/https"):
        link_button("链接", "javascript:alert(1)")
    assert safe_link_button("链接", "javascript:alert(1)") is None

    duplicate = command_button("重复", "重复", button_id="same")
    with pytest.raises(ValueError, match="button id 重复"):
        build_markdown_segment(reply_spec("# 测试", rows=((duplicate,), (duplicate,))))

    too_many_rows = tuple((command_button("按钮", str(index)),) for index in range(6))
    with pytest.raises(ValueError, match="安全布局上限 5"):
        build_markdown_segment(reply_spec("# 测试", rows=too_many_rows))


def test_matcher_adapter_sends_markdown_and_falls_back_once() -> None:
    class FakeBot:
        self_id = "test-bot"

        def __init__(self) -> None:
            self.messages = []

        async def send(self, **kwargs):
            self.messages.append(kwargs["message"])
            if len(self.messages) <= 2:
                raise ActionFailed("test", "40034029 keyboard rejected")
            return "fallback-ok"

    class FakeEvent:
        pass

    bot = FakeBot()
    matcher = arcade_help()

    async def run() -> None:
        with matcher.ensure_context(bot, FakeEvent()):
            result = await arcade_help.send("含有 [动态] 内容")
        assert result == "fallback-ok"

    asyncio.run(run())
    assert isinstance(bot.messages[0], MessageSegment)
    assert bot.messages[0].type == "markdown"
    assert isinstance(bot.messages[1], MessageSegment)
    assert "keyboard" not in bot.messages[1].data["data"]
    assert bot.messages[2] == "含有 [动态] 内容"


def test_markdown_mode_auto_uses_bot_whitelist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plugin_config, "mai_arcade_markdown_mode", "auto")
    monkeypatch.setattr(plugin_config, "mai_arcade_official_bot_ids", ["official-1"])

    class Bot:
        def __init__(self, self_id: str) -> None:
            self.self_id = self_id

    assert markdown_enabled(Bot("official-1"))
    assert not markdown_enabled(Bot("other"))

    monkeypatch.setattr(plugin_config, "mai_arcade_markdown_mode", "on")
    assert markdown_enabled(Bot("other"))
    monkeypatch.setattr(plugin_config, "mai_arcade_markdown_mode", "off")
    assert not markdown_enabled(Bot("official-1"))


def test_length_error_splits_once_and_keeps_keyboard_on_last_chunk() -> None:
    class FakeBot:
        self_id = "test-bot"

        def __init__(self) -> None:
            self.messages = []

        async def send(self, **kwargs):
            self.messages.append(kwargs["message"])
            if len(self.messages) == 1:
                raise ActionFailed("test", "40054007 message too long")
            return "split-ok"

    class FakeEvent:
        pass

    bot = FakeBot()
    matcher = arcade_help()
    spec = reply_spec(
        "\n".join(f"第 {index} 行" for index in range(1, 9)),
        rows=((command_button("刷新", "刷新", button_id="refresh"),),),
    )

    async def run() -> None:
        with matcher.ensure_context(bot, FakeEvent()):
            result = await arcade_help.send(spec)
        assert result is None

    asyncio.run(run())
    assert len(bot.messages) > 2
    assert all(isinstance(message, MessageSegment) for message in bot.messages)
    assert "keyboard" not in bot.messages[1].data["data"]
    assert "keyboard" in bot.messages[-1].data["data"]


def test_network_error_is_not_hidden_by_plain_text_retry() -> None:
    class FakeBot:
        self_id = "test-bot"

        def __init__(self) -> None:
            self.messages = []

        async def send(self, **kwargs):
            self.messages.append(kwargs["message"])
            raise NetworkError("test", "offline")

    class FakeEvent:
        pass

    bot = FakeBot()
    matcher = arcade_help()

    async def run() -> None:
        with matcher.ensure_context(bot, FakeEvent()):
            with pytest.raises(NetworkError):
                await arcade_help.send("网络错误不应被伪装")

    asyncio.run(run())
    assert len(bot.messages) == 1
