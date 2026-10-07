"""Focused tests for the official Markdown/Keyboard adapter."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import nonebot
import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.adapters.onebot.v11.event import Sender
from nonebot.exception import ActionFailed, FinishedException, NetworkError


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

from amia_plugin_mai_arcade import (  # noqa: E402
    arcade_help,
    handle_location_listener,
    location_listener,
)
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
from amia_plugin_mai_arcade.utils import is_superuser_or_admin  # noqa: E402
from amia_plugin_mai_arcade.messaging import (  # noqa: E402
    build_markdown_segment,
    command_button,
    escape_markdown,
    link_button,
    markdown_enabled,
    reply_spec,
    safe_link_button,
)
from amia_plugin_mai_arcade.services import (  # noqa: E402
    GeocodeResult,
    _location_from_tencent_payload,
    _tencent_sig,
    gcj02_to_wgs84,
    geocode_address,
    resolve_address,
)
import amia_plugin_mai_arcade.services as services_module  # noqa: E402
import amia_plugin_mai_arcade.plugin as plugin_module  # noqa: E402


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


def test_gensokyo_location_card_triggers_explanatory_reply_without_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Keep this regression test independent from developer-machine provider
    # credentials. This test specifically covers the no-coordinate
    # explanatory fallback.
    async def no_geocode(address: str, *, city: str | None = None):
        return None

    monkeypatch.setattr(plugin_module, "resolve_address", no_geocode)

    class FakeBot:
        self_id = "official-gensokyo-test"

        def __init__(self) -> None:
            self.messages = []

        async def send(self, **kwargs):
            self.messages.append(kwargs["message"])
            return "sent"

    bot = FakeBot()
    matcher = location_listener()
    event = SimpleNamespace(
        message=Message(
            MessageSegment.text(
                "[卡片消息] 位置卡片\n"
                "摘要: [位置]栖霞区迈皋桥壹城(东区)\n"
                "address: 江苏省南京市栖霞区万兴路辅路\n"
                "desc: 栖霞区迈皋桥壹城(东区)"
            )
        )
    )

    async def run() -> None:
        with matcher.ensure_context(bot, event):
            with pytest.raises(FinishedException):
                await handle_location_listener(event)

    asyncio.run(run())
    assert len(bot.messages) == 1
    message = bot.messages[0]
    assert isinstance(message, MessageSegment)
    assert message.type == "markdown"
    content = message.data["data"]["markdown"]["content"]
    assert "已收到位置卡片" in content
    assert "没有携带经纬度" in content
    assert "附近机厅 纬度, 经度" in content
    assert "北京市天安门广场" in content
    keyboard = message.data["data"]["keyboard"]["content"]["rows"]
    button = keyboard[0]["buttons"][0]
    assert button["id"] == "arcade_location_fill_coords"
    assert button["render_data"]["label"] == "填写坐标"
    assert button["action"]["type"] == 2
    assert button["action"]["data"] == "位置："
    assert button["action"]["permission"] == {"type": 2}


def test_location_listener_ignores_own_markdown_reply_echo() -> None:
    """A bot reply containing the coordinate example must not re-trigger search."""

    class FakeBot:
        self_id = "official-gensokyo-test"

        def __init__(self) -> None:
            self.messages = []

        async def send(self, **kwargs):
            self.messages.append(kwargs["message"])
            return "sent"

    bot = FakeBot()
    matcher = location_listener()
    event = SimpleNamespace(
        message=Message(
            MessageSegment.text(
                "# 已收到位置卡片\n\n"
                "当前 Gensokyo 位置卡片只有地点和地址，没有携带经纬度。\n"
                "示例：`位置：北京市天安门广场 (39.908823, 116.397470)`"
            )
        ),
        message_id=990001,
        self_id=bot.self_id,
    )

    async def run() -> None:
        with matcher.ensure_context(bot, event):
            await handle_location_listener(event)

    asyncio.run(run())
    assert bot.messages == []


def test_location_listener_claims_one_message_id_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Duplicate matcher registration must not send two nearby replies."""

    async def fake_discover(lat, lon, radius=10, name=None, *, convert_from=None):
        return ({"shops": []}, "https://nearcade.cn/discover")

    monkeypatch.setattr(plugin_module, "call_discover", fake_discover)

    class FakeBot:
        self_id = "official-gensokyo-test"

        def __init__(self) -> None:
            self.messages = []

        async def send(self, **kwargs):
            self.messages.append(kwargs["message"])
            return "sent"

    bot = FakeBot()
    matcher = location_listener()
    event = SimpleNamespace(
        message=Message(MessageSegment.text("附近机厅 32.112606, 118.834837")),
        message_id=990002,
        self_id=bot.self_id,
    )

    async def run() -> None:
        with matcher.ensure_context(bot, event):
            with pytest.raises(FinishedException):
                await handle_location_listener(event)
        with matcher.ensure_context(bot, event):
            await handle_location_listener(event)

    asyncio.run(run())
    assert len(bot.messages) == 1


def test_gensokyo_release015_sender_roles_match_plugin_admin_check() -> None:
    """Release015 maps QQ member_role to OneBot sender.role verbatim."""

    class FakeAdapter:
        @staticmethod
        def get_name() -> str:
            return "OneBot V11"

    bot = SimpleNamespace(
        adapter=FakeAdapter(),
        config=SimpleNamespace(superusers=set()),
    )

    def event_for(role: str) -> GroupMessageEvent:
        return GroupMessageEvent(
            time=1,
            self_id=1905525797,
            post_type="message",
            sub_type="normal",
            user_id=10001,
            message_type="group",
            message_id=20001,
            message=Message("添加机厅"),
            original_message="添加机厅",
            raw_message="添加机厅",
            font=0,
            sender=Sender(user_id=10001, nickname="Amia_测试", role=role),
            to_me=False,
            group_id=30001,
            anonymous=None,
        )

    async def run() -> None:
        owner = event_for("owner")
        admin = event_for("admin")
        member = event_for("member")

        assert await is_superuser_or_admin(bot, owner)
        assert await is_superuser_or_admin(bot, admin)
        assert not await is_superuser_or_admin(bot, member)

    asyncio.run(run())


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


def test_tencent_payload_signature_and_coordinate_conversion() -> None:
    assert _location_from_tencent_payload(
        {"status": 0, "result": {"location": {"lat": 32.112606, "lng": 118.834837}}}
    ) == (32.112606, 118.834837)
    assert _location_from_tencent_payload({"status": 347, "result": {}}) is None

    assert _tencent_sig(
        "/ws/geocoder/v1/",
        [("key", "demo-key"), ("address", "南京市栖霞区")],
        "demo-secret",
    ) == __import__("hashlib").md5(
        "/ws/geocoder/v1/?address=南京市栖霞区&key=demo-keydemo-secret".encode("utf-8")
    ).hexdigest()

    converted = gcj02_to_wgs84(32.112606, 118.834837)
    assert converted != (32.112606, 118.834837)
    assert -90 <= converted[0] <= 90 and -180 <= converted[1] <= 180
    assert gcj02_to_wgs84(60.0, 10.0) == (60.0, 10.0)


def test_tencent_geocode_returns_gps_marker_and_uses_optional_sig(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "status": 0,
                "message": "query ok",
                "result": {"location": {"lat": 32.112606, "lng": 118.834837}},
            }

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured["client_kwargs"] = kwargs

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, url: str, *, params: object) -> FakeResponse:
            captured["url"] = url
            captured["params"] = params
            return FakeResponse()

    monkeypatch.setattr(services_module.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(plugin_config, "tencent_key", "temporary-tencent-key")
    monkeypatch.setattr(plugin_config, "tencent_sk", "temporary-tencent-sk")

    result = asyncio.run(services_module._geocode_address_tencent("南京市栖霞区"))

    assert result is not None
    assert result.provider == "tencent"
    assert result.convert_from == "gps"
    assert captured["url"] == "https://apis.map.qq.com/ws/geocoder/v1/"
    sent_params = captured["params"]
    assert isinstance(sent_params, list)
    assert any(key == "sig" for key, _ in sent_params)
    assert all("temporary-tencent-sk" not in str(item) for item in sent_params)


def test_geocoder_uses_only_tencent_and_ignores_removed_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_tencent(address: str, city: str | None = None) -> GeocodeResult | None:
        calls.append("tencent")
        return GeocodeResult(32.0, 118.0, "tencent", "gps")

    monkeypatch.setattr(services_module, "_geocode_address_tencent", fake_tencent)
    monkeypatch.setattr(plugin_config, "geocoder_order", ["legacy", "tencent"])
    monkeypatch.setattr(services_module, "_geocoder_rotation_cursor", 0)

    first = asyncio.run(resolve_address("南京市栖霞区"))
    second = asyncio.run(resolve_address("南京市栖霞区"))

    assert first == GeocodeResult(32.0, 118.0, "tencent", "gps")
    assert second == GeocodeResult(32.0, 118.0, "tencent", "gps")
    assert calls == ["tencent", "tencent"]


def test_gensokyo_location_card_geocodes_before_nearcade(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeBot:
        self_id = "official-gensokyo-test"

        def __init__(self) -> None:
            self.messages = []

        async def send(self, **kwargs):
            self.messages.append(kwargs["message"])
            return "sent"

    async def fake_geocode(address: str, *, city=None):
        assert "万兴路辅路" in address
        return GeocodeResult(32.112606, 118.834837, "tencent", "gps")

    async def fake_discover(lat, lon, radius=10, name=None, *, convert_from=None):
        assert (lat, lon) == (32.112606, 118.834837)
        assert convert_from == "gps"
        return (
            {"shops": [{"name": "星际传奇", "distance": 0.8, "address": {"detailed": "紫东路2号"}}]},
            "https://nearcade.cn/discover?convertFrom=gps",
        )

    monkeypatch.setattr(plugin_module, "resolve_address", fake_geocode)
    monkeypatch.setattr(plugin_module, "call_discover", fake_discover)

    bot = FakeBot()
    matcher = location_listener()
    event = SimpleNamespace(
        message=Message(
            MessageSegment.text(
                "[卡片消息] 位置卡片\n"
                "摘要: [位置]栖霞区迈皋桥壹城(东区)\n"
                "address: 江苏省南京市栖霞区万兴路辅路\n"
                "desc: 栖霞区迈皋桥壹城(东区)"
            )
        )
    )

    async def run() -> None:
        with matcher.ensure_context(bot, event):
            with pytest.raises(FinishedException):
                await handle_location_listener(event)

    asyncio.run(run())
    assert len(bot.messages) == 1
    assert "附近机厅" in bot.messages[0].data["data"]["markdown"]["content"]
