"""Location message extraction for OneBot and Gensokyo.

NapCat normally forwards a location as a CQ JSON payload containing
``meta.Location.Search``.  Gensokyo's official QQ Bot path currently exposes
the same map card as a textual ``[卡片消息] 位置卡片`` payload with an address
and description.  When users follow Gensokyo's documented text fallback,
``位置：地点 (纬度, 经度)`` is also accepted.  Keeping extraction here
separate from the matcher makes all forms testable and prevents the handler
from silently treating the official card as an ordinary chat message.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional


_GENSOKYO_LOCATION_RE = re.compile(
    r"^\s*\[卡片消息\]\s*位置卡片(?:\s|$)", re.MULTILINE
)
_CQ_LOCATION_RE = re.compile(
    r"\[CQ:location(?P<params>(?:,[^\]]*)?)\]", re.IGNORECASE
)
_CQ_PARAM_RE = re.compile(
    r"(?P<key>[A-Za-z][A-Za-z0-9_-]*)=(?P<value>"
    r"(?:[^,]|&#44;|&#91;|&#93;|&amp;)+)"
)
_FIELD_RE = re.compile(r"^\s*(?P<key>摘要|desc|address|地址|lat|latitude|lng|lon|longitude)\s*[:：]\s*(?P<value>.*?)\s*$", re.IGNORECASE | re.MULTILINE)
_COORD_RE = re.compile(
    r"(?P<key>lat(?:itude)?|lng|lon(?:gitude)?)\s*[=:：]\s*"
    r"(?P<value>[+-]?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_COORD_PAIR_RE = re.compile(
    r"(?<![\d.])"
    r"(?P<latitude>[+-]?(?:\d{1,2})(?:\.\d+)?)"
    r"\s*[,，]\s*"
    r"(?P<longitude>[+-]?(?:\d{1,3})(?:\.\d+)?)"
    r"(?![\d.])"
)
_COORD_HINT_RE = re.compile(
    r"(?:附近机厅|坐标|经纬(?:度)?|位置|latitude|longitude|lat|lng|lon)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LocationPayload:
    """Normalized location data extracted from an incoming message."""

    latitude: Optional[float]
    longitude: Optional[float]
    title: str = "未知位置"
    address: str = ""
    source: str = "unknown"

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None


def _unescape_cq(value: str) -> str:
    """Decode the entities used by Gensokyo/OneBot CQ parameters."""

    return (
        value.replace("&#44;", ",")
        .replace("&#91;", "[")
        .replace("&#93;", "]")
        .replace("&amp;", "&")
    )


def _as_mapping(value: Any) -> Optional[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return None
        return decoded if isinstance(decoded, Mapping) else None
    return None


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    # Coordinates outside these ranges are malformed and must not be sent to
    # Nearcade as if they were real user data.
    if not -90 <= number <= 90:
        return None
    return number


def _longitude(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not -180 <= number <= 180:
        return None
    return number


def _coordinate_pair(
    text: str,
) -> tuple[Optional[float], Optional[float], Optional[re.Match[str]]]:
    """Extract a documented ``latitude, longitude`` text pair safely.

    Gensokyo's location documentation recommends sending a place name and
    coordinates as ordinary text because the official QQ Bot API does not
    expose a native location message.  Require a nearby coordinate hint so a
    random ``number, number`` in ordinary chat is not treated as a location.
    """

    for match in _COORD_PAIR_RE.finditer(text):
        prefix = text[max(0, match.start() - 48) : match.start()]
        if not _COORD_HINT_RE.search(prefix):
            continue
        latitude = _number(match.group("latitude"))
        longitude = _longitude(match.group("longitude"))
        if latitude is not None and longitude is not None:
            return latitude, longitude, match
    return None, None, None


def _from_cq_location(text: str) -> Optional[LocationPayload]:
    """Extract a standard ``[CQ:location,...]`` segment.

    Gensokyo's current QQ Bot path does not emit this segment for an official
    map card, but other OneBot sources can still provide it.  Supporting the
    standard syntax here keeps the adapter compatible without treating an
    arbitrary ``lat/lon`` text pair as a location.
    """

    for segment in _CQ_LOCATION_RE.finditer(text):
        params = {
            match.group("key").lower(): _unescape_cq(match.group("value")).strip()
            for match in _CQ_PARAM_RE.finditer(segment.group("params"))
        }
        latitude = _number(params.get("lat") or params.get("latitude"))
        longitude = _longitude(
            params.get("lon") or params.get("lng") or params.get("longitude")
        )
        if latitude is None or longitude is None:
            continue
        title = params.get("title") or params.get("name") or "用户位置"
        address = params.get("content") or params.get("address") or ""
        return LocationPayload(
            latitude,
            longitude,
            title=title,
            address=address,
            source="cq-location",
        )
    return None


def _from_mapping(payload: Mapping[str, Any], source: str) -> Optional[LocationPayload]:
    """Extract a location from a CQ/ark-like mapping."""

    # Gensokyo/QQ may wrap the card under one of these keys when an adapter is
    # configured for segment-array messages.  Walk only known wrappers rather
    # than recursively searching arbitrary user data.
    for wrapper in ("data", "ark_data", "ark", "fields", "meta"):
        nested = _as_mapping(payload.get(wrapper))
        if nested is not None and nested is not payload:
            candidate = _from_mapping(nested, source)
            if candidate is not None:
                return candidate

    location_search = _as_mapping(payload.get("Location.Search"))
    if location_search is not None:
        payload = location_search

    latitude = _number(
        _first(payload, "lat", "latitude", "Lat", "Latitude")
    )
    longitude = _longitude(
        _first(payload, "lng", "lon", "longitude", "Lng", "Lon", "Longitude")
    )

    # Some payloads keep a coordinate pair in a string (for example a card
    # description).  Accept it only when both values are explicit.
    if latitude is None or longitude is None:
        text = " ".join(str(value) for value in payload.values() if isinstance(value, str))
        matches = {match.group("key").lower(): match.group("value") for match in _COORD_RE.finditer(text)}
        latitude = latitude if latitude is not None else _number(
            matches.get("lat") or matches.get("latitude")
        )
        longitude = longitude if longitude is not None else _longitude(
            matches.get("lng") or matches.get("lon") or matches.get("longitude")
        )

    if latitude is None or longitude is None:
        # A mapping is still useful to the caller if it is a Gensokyo map card:
        # it can produce an explicit “coordinates unavailable” reply.
        is_map_card = str(_first(payload, "ark_type", "type") or "").lower() == "map"
        if source == "gensokyo" and is_map_card:
            fields = _as_mapping(payload.get("fields")) or payload
            title = str(_first(fields, "desc", "name", "title") or "未知位置")
            address = str(_first(fields, "address", "addr", "content") or "")
            return LocationPayload(None, None, title=title, address=address, source=source)
        return None

    title = str(_first(payload, "name", "title", "desc", "prompt") or "未知位置")
    address = str(_first(payload, "address", "addr", "content") or "")
    return LocationPayload(latitude, longitude, title=title, address=address, source=source)


def _from_text(text: str) -> Optional[LocationPayload]:
    cq_location = _from_cq_location(text)
    if cq_location is not None:
        return cq_location

    is_gensokyo_card = _GENSOKYO_LOCATION_RE.search(text) is not None

    fields = {
        match.group("key").lower(): match.group("value")
        for match in _FIELD_RE.finditer(text)
    }
    title = fields.get("desc") or fields.get("摘要") or "未知位置"
    address = fields.get("address") or fields.get("地址") or ""
    latitude = _number(fields.get("lat") or fields.get("latitude"))
    longitude = _longitude(
        fields.get("lng") or fields.get("lon") or fields.get("longitude")
    )
    if latitude is None or longitude is None:
        matches = {match.group("key").lower(): match.group("value") for match in _COORD_RE.finditer(text)}
        latitude = latitude if latitude is not None else _number(
            matches.get("lat") or matches.get("latitude")
        )
        longitude = longitude if longitude is not None else _longitude(
            matches.get("lng") or matches.get("lon") or matches.get("longitude")
        )
    pair_match: Optional[re.Match[str]] = None
    if latitude is None or longitude is None:
        pair_latitude, pair_longitude, pair_match = _coordinate_pair(text)
        latitude = latitude if latitude is not None else pair_latitude
        longitude = longitude if longitude is not None else pair_longitude

    if is_gensokyo_card:
        return LocationPayload(
            latitude,
            longitude,
            title=title,
            address=address,
            source="gensokyo",
        )

    if latitude is None or longitude is None:
        return None

    # For the documented text fallback, keep the place name separate from the
    # coordinate pair so it can be passed to Nearcade as an optional hint.
    if pair_match is not None:
        prefix = text[: pair_match.start()].strip()
        prefix = re.sub(
            r"^(?:附近机厅|位置|地点|坐标|经纬(?:度)?)\s*[:：]?\s*",
            "",
            prefix,
            flags=re.IGNORECASE,
        ).strip(" \t\r\n:：,，()（）[]【】")
        if prefix:
            title = prefix.splitlines()[-1].strip() or title
            address = prefix

    return LocationPayload(
        latitude,
        longitude,
        title=title if title != "未知位置" else "用户位置",
        address=address,
        source="gensokyo-text",
    )


def extract_location(message: Iterable[Any]) -> Optional[LocationPayload]:
    """Extract the first location payload from a NoneBot message."""

    if isinstance(message, str):
        return _from_text(message)

    text_parts = []
    for segment in message:
        segment_type = getattr(segment, "type", "")
        data = getattr(segment, "data", {})
        if segment_type == "text":
            value = data.get("text", "") if isinstance(data, Mapping) else ""
            if value:
                text_parts.append(str(value))
            continue

        if not isinstance(data, Mapping):
            continue

        if segment_type == "json":
            payload = _as_mapping(data.get("data"))
            if payload is not None:
                location = _from_mapping(payload, "json")
                if location is not None:
                    return location
        elif segment_type in {"location", "ark", "card"}:
            location = _from_mapping(data, "gensokyo" if segment_type == "ark" else segment_type)
            if location is not None:
                return location

    if text_parts:
        return _from_text("".join(text_parts))
    return None
