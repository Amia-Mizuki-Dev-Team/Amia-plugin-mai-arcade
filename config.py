"""
配置和数据管理模块
"""
import json
from pathlib import Path
from typing import Literal
from pydantic import (
    AliasChoices,
    BaseModel,
    Field,
)
import nonebot
require = nonebot.require
require("nonebot_plugin_localstore")
import nonebot_plugin_localstore as store

# 配置
config = nonebot.get_driver().config
superusers = config.superusers
block_group = set(["12345678"])
search_sessions = {}  # 存储搜索会话状态

# 数据文件路径。正常由 localstore 按插件注册表提供；在测试或独立导入
# 场景下，如果 localstore 尚未建立插件上下文，则使用项目 data 根目录的
# Amia 插件专属回退目录，避免阻断整个 NoneBot 启动。
try:
    arcade_data_file: Path = store.get_plugin_data_file("arcade_data.json")
    arcade_marker_file: Path = store.get_plugin_data_file("arcade_cache_marker.json")
except RuntimeError:
    # Reuse the previous fallback directory when it already contains data;
    # otherwise new standalone runs use the Amia repository name.
    fallback_data_dir = Path("data") / "Amia-plugin-mai-arcade"
    legacy_data_dir = Path("data") / "nonebot_plugin_mai_arcade"
    if not fallback_data_dir.exists() and legacy_data_dir.exists():
        fallback_data_dir = legacy_data_dir
    fallback_data_dir.mkdir(parents=True, exist_ok=True)
    arcade_data_file = fallback_data_dir / "arcade_data.json"
    arcade_marker_file = fallback_data_dir / "arcade_cache_marker.json"

# 初始化数据文件
if not arcade_data_file.exists():
    arcade_data_file.write_text('{}', encoding='utf-8')

# 全局数据变量
data_json = {}

def load_data():
    """加载数据文件"""
    global data_json
    with open(arcade_data_file, 'r', encoding='utf-8') as f:
        data_json = json.load(f)

async def re_write_json():
    """保存数据到文件"""
    global data_json
    with open(arcade_data_file, 'w', encoding='utf-8') as f:
        json.dump(data_json, f, ensure_ascii=False, indent=2)

# 初始化加载数据
load_data()

class SmartTipRule(BaseModel):
    max_minutes: int
    tip: str


class Config(BaseModel):
    """插件配置类
    nearcade_api_token: Nearcade 开发者 API令牌
    count_smart_tips: 排队等待时间提示规则列表，按 max_minutes 升序匹配，max_minutes=0 表示无需等待
    """
    # Keep credentials outside the repository.  Read a real token from the
    # project's environment file when Nearcade write operations are enabled.
    nearcade_api_token: str = ""
    count_smart_tips: list[SmartTipRule] = [
        SmartTipRule(max_minutes=0, tip="✅ 无需等待，快去出勤吧！"),
        SmartTipRule(max_minutes=20, tip="✅ 舞萌启动！"),
        SmartTipRule(max_minutes=40, tip="🕰️ 小排队还能忍"),
        SmartTipRule(max_minutes=90, tip="💀 DBD，纯折磨，建议换店"),
        SmartTipRule(max_minutes=9999, tip="🪦 建议回家（或者明天再来）"),
    ]
    mai_arcade_markdown_mode: Literal["auto", "on", "off"] = Field(
        default="auto",
        validation_alias=AliasChoices(
            "mai_arcade_markdown_mode",
            "MAI_ARCADE_MARKDOWN_MODE",
            "markdown_mode",
            "MARKDOWN_MODE",
        ),
    )
    mai_arcade_official_bot_ids: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices(
            "mai_arcade_official_bot_ids",
            "MAI_ARCADE_OFFICIAL_BOT_IDS",
            "official_bot_ids",
            "OFFICIAL_BOT_IDS",
        ),
    )


# Keep one immutable configuration snapshot for the message adapter.  The
# upstream handlers also call ``get_plugin_config(Config)`` for their existing
# Nearcade settings, so the fields above remain compatible with that pattern.
plugin_config = nonebot.get_plugin_config(Config)
