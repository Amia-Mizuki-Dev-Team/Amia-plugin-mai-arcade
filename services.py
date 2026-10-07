"""
外部服务和API调用模块
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from threading import Lock
import urllib.parse

import httpx
from nonebot import logger
from .config import data_json, plugin_config, re_write_json
from .utils import format_shop_info, get_shop_url


TENCENT_GEOCODING_PATH = "/ws/geocoder/v1/"

_GEOCODER_NAMES = ("tencent",)
_geocoder_rotation_lock = Lock()
_geocoder_rotation_cursor = 0


@dataclass(frozen=True)
class GeocodeResult:
    """A resolved address and the coordinate conversion Nearcade needs."""

    latitude: float
    longitude: float
    provider: str
    convert_from: str


def _valid_coordinates(latitude: object, longitude: object) -> tuple[float, float] | None:
    try:
        latitude_value = float(latitude)
        longitude_value = float(longitude)
    except (TypeError, ValueError):
        return None
    if not -90 <= latitude_value <= 90 or not -180 <= longitude_value <= 180:
        return None
    return latitude_value, longitude_value


def _out_of_china(latitude: float, longitude: float) -> bool:
    return not (73.66 < longitude < 135.05 and 3.86 < latitude < 53.55)


def _transform_lat(x: float, y: float) -> float:
    ret = (
        -100.0
        + 2.0 * x
        + 3.0 * y
        + 0.2 * y * y
        + 0.1 * x * y
        + 0.2 * math.sqrt(abs(x))
    )
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lon(x: float, y: float) -> float:
    ret = (
        300.0
        + x
        + 2.0 * y
        + 0.1 * x * x
        + 0.1 * x * y
        + 0.1 * math.sqrt(abs(x))
    )
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def gcj02_to_wgs84(latitude: float, longitude: float) -> tuple[float, float]:
    """Convert Tencent GCJ-02 coordinates to WGS-84 for Nearcade.

    Nearcade's current discovery endpoint accepts ``gps`` (WGS-84). Tencent
    returns GCJ-02, so the conversion is performed locally and the caller
    sends ``convertFrom=gps``.
    """

    if _out_of_china(latitude, longitude):
        return latitude, longitude

    earth_radius = 6378245.0
    eccentricity = 0.00669342162296594323
    d_lat = _transform_lat(longitude - 105.0, latitude - 35.0)
    d_lon = _transform_lon(longitude - 105.0, latitude - 35.0)
    rad_lat = latitude / 180.0 * math.pi
    magic = 1 - eccentricity * math.sin(rad_lat) ** 2
    sqrt_magic = math.sqrt(magic)
    d_lat = (d_lat * 180.0) / ((earth_radius * (1 - eccentricity)) / (magic * sqrt_magic) * math.pi)
    d_lon = (d_lon * 180.0) / (earth_radius / sqrt_magic * math.cos(rad_lat) * math.pi)
    mg_lat = latitude + d_lat
    mg_lon = longitude + d_lon
    return latitude * 2 - mg_lat, longitude * 2 - mg_lon


def _configured_geocoder_order() -> tuple[str, ...]:
    raw = getattr(plugin_config, "geocoder_order", _GEOCODER_NAMES)
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            decoded = raw.split(",")
        raw = decoded
    if not isinstance(raw, (list, tuple)):
        raw = _GEOCODER_NAMES

    selected: list[str] = []
    for item in raw:
        name = str(item).strip().lower()
        if name in _GEOCODER_NAMES and name not in selected:
            selected.append(name)
    return tuple(selected)


def _next_geocoder_order() -> tuple[str, ...]:
    """Return a rotated provider order without sharing secrets or state."""

    global _geocoder_rotation_cursor
    providers = _configured_geocoder_order()
    if not providers:
        return ()
    with _geocoder_rotation_lock:
        start = _geocoder_rotation_cursor % len(providers)
        _geocoder_rotation_cursor = (_geocoder_rotation_cursor + 1) % len(providers)
    return providers[start:] + providers[:start]


def _tencent_sig(path: str, params: list[tuple[str, str]], secret: str) -> str:
    """Calculate Tencent WebService's optional GET signature."""

    canonical = "&".join(f"{key}={value}" for key, value in sorted(params))
    return hashlib.md5((path + "?" + canonical + secret).encode("utf-8")).hexdigest()


def _location_from_tencent_payload(payload: object) -> tuple[float, float] | None:
    if not isinstance(payload, dict) or payload.get("status") != 0:
        return None
    result = payload.get("result")
    location = result.get("location") if isinstance(result, dict) else None
    if not isinstance(location, dict):
        return None
    return _valid_coordinates(location.get("lat"), location.get("lng"))


async def _request_json(url: str, params: list[tuple[str, str]]) -> object:
    transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0")
    async with httpx.AsyncClient(transport=transport, timeout=20.0) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()


async def _geocode_address_tencent(address: str, city: str | None = None) -> GeocodeResult | None:
    key = str(getattr(plugin_config, "tencent_key", "") or "").strip()
    if not key:
        return None

    params: list[tuple[str, str]] = [("address", address), ("key", key)]
    if city:
        params.append(("region", city.strip()))
    secret = str(getattr(plugin_config, "tencent_sk", "") or "").strip()
    if secret:
        params.append(("sig", _tencent_sig(TENCENT_GEOCODING_PATH, params, secret)))
    try:
        payload = await _request_json("https://apis.map.qq.com" + TENCENT_GEOCODING_PATH, params)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(f"腾讯地理编码请求失败：{type(exc).__name__}")
        return None

    coordinates = _location_from_tencent_payload(payload)
    if coordinates is None:
        status = payload.get("status") if isinstance(payload, dict) else None
        message = payload.get("message") if isinstance(payload, dict) else ""
        logger.warning(f"腾讯地理编码未返回坐标：status={status} message={message}")
        return None
    latitude, longitude = gcj02_to_wgs84(*coordinates)
    return GeocodeResult(latitude, longitude, "tencent", "gps")


async def resolve_address(
    address: str,
    *,
    city: str | None = None,
) -> GeocodeResult | None:
    """Resolve an address using the configured rotating provider fallback.

    Each request starts at the next configured provider.  If that provider is
    unavailable, denied or returns malformed data, the remaining configured
    providers are attempted once in the same order.  Missing credentials are
    skipped without producing a secret-bearing error.
    """

    address = (address or "").strip()
    if not address:
        return None

    handlers = {"tencent": _geocode_address_tencent}
    for provider in _next_geocoder_order():
        handler = handlers[provider]
        try:
            result = await handler(address, city)
        except Exception as exc:  # provider isolation; do not break location cards
            logger.warning(f"{provider} 地理编码异常：{type(exc).__name__}")
            result = None
        if result is not None:
            return result
    return None


async def geocode_address(
    address: str,
    *,
    city: str | None = None,
) -> tuple[float, float] | None:
    """Backward-compatible coordinate-only wrapper around ``resolve_address``."""

    result = await resolve_address(address, city=city)
    if result is None:
        return None
    return result.latitude, result.longitude


async def search_nearcade_shops(keyword: str, page: int = 1, limit: int = 3) -> dict:
    """
    搜索nearcade机厅
    
    Args:
        keyword: 搜索关键词
        page: 页码
        limit: 返回结果数量限制
        
    Returns:
        包含shops和totalCount的字典
    """
    try:
        import urllib.parse
        
        encoded_query = urllib.parse.quote(keyword)
        url = f"https://nearcade.cn/api/shops?q={encoded_query}&page={page}&limit={limit}"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (compatible; NoneBot-Arcade-Plugin)',
            'Accept': 'application/json'
        }
        
        # Some Windows deployments resolve Nearcade to an unreachable IPv6
        # address first.  Bind the async transport to IPv4 so discovery does
        # not fail while curl/other clients can still reach the same endpoint.
        transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0")
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            return {
                'shops': data.get('shops', []),
                'totalCount': data.get('totalCount', 0)
            }
    except Exception as e:
        logger.error(f"搜索nearcade机厅失败: {e}")
        return {'shops': [], 'totalCount': 0}


async def auto_add_arcade_map(group_id: str, arcade_name: str, shop_url: str) -> bool:
    """
    自动添加机厅地图
    
    Args:
        group_id: 群组ID
        arcade_name: 机厅名称
        shop_url: 机厅URL
        
    Returns:
        是否成功添加地图
    """
    try:
        if not shop_url or group_id not in data_json or arcade_name not in data_json[group_id]:
            return False
        
        # 初始化map字段
        if 'map' not in data_json[group_id][arcade_name]:
            data_json[group_id][arcade_name]['map'] = []
        
        # 检查URL是否已存在
        if shop_url not in data_json[group_id][arcade_name]['map']:
            data_json[group_id][arcade_name]['map'].append(shop_url)
            await re_write_json()
            return True
        
        return False
    except Exception as e:
        logger.error(f"自动添加机厅地图失败: {e}")
        return False


async def call_discover(
    lat: float,
    lon: float,
    radius: int = 10,
    name: str = None,
    *,
    convert_from: str | None = None,
):
    """
    调用nearcade发现API，根据位置查找附近机厅
    
    Args:
        lat: 纬度
        lon: 经度
        radius: 搜索半径（公里）
        name: 位置名称（可选）
        convert_from: 坐标来源。腾讯地址解析转换后的 WGS-84 结果传
            ``gps``。
        
    Returns:
        tuple: (机厅数据, 网页URL)
    """
    try:
        import urllib.parse
        
        BASE_HOST = "nearcade.cn"
        params = {
            "latitude": str(lat),
            "longitude": str(lon),
            "radius": str(radius),
        }
        if name:
            params["name"] = name
        if convert_from:
            params["convertFrom"] = convert_from
        
        query = urllib.parse.urlencode(params, safe="")
        
        transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0")
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.get(f"https://{BASE_HOST}/api/discover?{query}")
            response.raise_for_status()
            data = response.json()
            web_url = f"https://{BASE_HOST}/discover?{query}"
            return data, web_url
    except Exception as e:
        logger.error(f"调用发现API失败：{type(e).__name__}: {e!r}")
        return {}, ""
