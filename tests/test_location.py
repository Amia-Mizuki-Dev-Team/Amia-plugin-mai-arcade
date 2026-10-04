"""Location input regression tests for NapCat and official Gensokyo paths."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from nonebot.adapters.onebot.v11 import Message, MessageSegment


MODULE_PATH = Path(__file__).resolve().parents[1] / "location.py"
SPEC = importlib.util.spec_from_file_location("mai_arcade_location_under_test", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_napcat_json_location_keeps_existing_coordinates() -> None:
    segment = MessageSegment.json(
        '{"meta":{"Location.Search":{"lat":"32.112606","lng":"118.834837","name":"壹城"}}}'
    )

    location = MODULE.extract_location(Message(segment))

    assert location is not None
    assert location.source == "json"
    assert location.latitude == 32.112606
    assert location.longitude == 118.834837
    assert location.title == "壹城"


def test_gensokyo_text_card_is_detected_without_fabricating_coordinates() -> None:
    segment = MessageSegment.text(
        "[卡片消息] 位置卡片\n"
        "摘要: [位置]栖霞区迈皋桥壹城(东区)\n"
        "desc: 栖霞区迈皋桥壹城(东区)\n"
        "address: 江苏省南京市栖霞区万兴路辅路"
    )

    location = MODULE.extract_location(Message(segment))

    assert location is not None
    assert location.source == "gensokyo"
    assert not location.has_coordinates
    assert location.title == "栖霞区迈皋桥壹城(东区)"
    assert location.address == "江苏省南京市栖霞区万兴路辅路"


def test_gensokyo_card_accepts_explicit_coordinates_when_adapter_provides_them() -> None:
    segment = MessageSegment.text(
        "[卡片消息] 位置卡片\n"
        "desc: 测试位置\n"
        "address: 南京市\n"
        "latitude: 32.112606\n"
        "longitude: 118.834837"
    )

    location = MODULE.extract_location(Message(segment))

    assert location is not None
    assert location.has_coordinates
    assert location.latitude == 32.112606
    assert location.longitude == 118.834837


def test_gensokyo_documented_text_coordinate_fallback_is_supported() -> None:
    segment = MessageSegment.text(
        "位置：北京市天安门广场 (39.908823, 116.397470)"
    )

    location = MODULE.extract_location(Message(segment))

    assert location is not None
    assert location.source == "gensokyo-text"
    assert location.has_coordinates
    assert location.latitude == 39.908823
    assert location.longitude == 116.39747
    assert location.title == "北京市天安门广场"


def test_standard_cq_location_segment_is_supported() -> None:
    segment = MessageSegment.text(
        "[CQ:location,lat=32.112606,lon=118.834837,"
        "title=栖霞区迈皋桥壹城&#44;东区,"
        "content=江苏省南京市栖霞区万兴路辅路]"
    )

    location = MODULE.extract_location(Message(segment))

    assert location is not None
    assert location.source == "cq-location"
    assert location.has_coordinates
    assert location.latitude == 32.112606
    assert location.longitude == 118.834837
    assert location.title == "栖霞区迈皋桥壹城,东区"
    assert location.address == "江苏省南京市栖霞区万兴路辅路"


def test_unmarked_number_pair_is_not_treated_as_a_location() -> None:
    segment = MessageSegment.text("今日抽卡结果：39.908823, 116.397470")

    assert MODULE.extract_location(Message(segment)) is None


def test_ark_map_fields_are_supported_without_coordinates() -> None:
    segment = MessageSegment("ark", {
        "ark_name": "位置卡片",
        "ark_type": "map",
        "fields": {
            "desc": "测试位置",
            "address": "南京市",
        },
    })

    location = MODULE.extract_location(Message(segment))

    assert location is not None
    assert location.source == "gensokyo"
    assert not location.has_coordinates


def test_onebot_location_segment_is_supported() -> None:
    segment = MessageSegment.location(
        32.112606,
        118.834837,
        title="壹城",
        content="南京市栖霞区",
    )

    location = MODULE.extract_location(Message(segment))

    assert location is not None
    assert location.source == "location"
    assert location.latitude == 32.112606
    assert location.longitude == 118.834837
