# Amia-plugin-mai-arcade

NoneBot2 舞萌 DX 机厅助手插件，为 Amia 的 OneBot/Gensokyo 部署提供机厅人数、Nearcade 云同步、机厅管理、地图、别名和排卡功能。

插件源码直接位于仓库根目录，仓库目录名和 NoneBot 发现到的插件名均为 `Amia-plugin-mai-arcade`，不再套额外的内层插件目录。

## 功能

- 机厅人数上报、增加、减少、重置和查询。
- 从 Nearcade 搜索并添加机厅，按位置查询附近机厅。
- 人数更新同步到 Nearcade，并按机台数量估算等待时间。
- 机厅别名、地图 URL 和序号索引管理。
- 排卡、上机、退勤、延后、排卡现状和闭店。
- 群聊启用/删除以及静默监听模式。
- 官方 QQ Bot Markdown + Keyboard 消息适配：指令按钮、URL 按钮、用户绑定按钮和纯文本回退。

Markdown 适配只改变消息渲染通道，不改变原有命令、权限、Nearcade 请求语义或本地数据结构。管理员鉴权始终由服务端 handler 执行，不能只依赖 QQ 客户端按钮权限。

## 运行要求

- Python 3.8 或更高版本。
- NoneBot2 `>=2.2.0`。
- OneBot V11 适配器 `>=2.4.4`；Amia 当前通过 Gensokyo/NapCat 链路接入。
- `nonebot-plugin-localstore`、`nonebot-plugin-apscheduler` 和 `httpx`。

## 安装

在 NoneBot 项目的 `src/plugins` 下克隆仓库：

```powershell
git clone https://github.com/Amia-Mizuki-Dev-Team/Amia-plugin-mai-arcade.git src/plugins/Amia-plugin-mai-arcade
python -m pip install -r src/plugins/Amia-plugin-mai-arcade/requirements.txt
```

Amia 项目的 `plugin_dirs` 已包含 `src/plugins`，克隆后可以直接由 NoneBot 扫描。仓库根目录的 `__init__.py` 就是插件入口，不需要再进入或复制其他同名插件目录。若使用独立 NoneBot 项目，也可以在 `pyproject.toml` 中启用：

```toml
[tool.nonebot]
plugins = ["Amia-plugin-mai-arcade"]
```

这是一个直接放入 NoneBot `plugin_dirs` 的源码插件；仓库根目录就是完整插件目录，
不需要再复制或安装第二层同名目录。

## 配置

在 NoneBot 项目的 `.env` 中填写。`.env` 不应提交到 Git，也不要把 token 写进源码、README、日志或截图。

```dotenv
# Nearcade API token。人数写入 Nearcade 时需要；查询公开数据不依赖它。
NEARCADE_API_TOKEN=your_nearcade_token_here

# 可选：按预计等待时间自定义提示，必须是 JSON 数组。
COUNT_SMART_TIPS='[{"max_minutes":0,"tip":"✅ 无需等待，快去出勤吧！"},{"max_minutes":20,"tip":"✅ 舞萌启动！"},{"max_minutes":40,"tip":"🕰️ 小排队还能忍"},{"max_minutes":90,"tip":"💀 DBD，纯折磨，建议换店"},{"max_minutes":9999,"tip":"🪦 建议回家（或者明天再来）"}]'

# 官方 QQ Bot Markdown/Keyboard 渲染模式：auto、on、off。
MAI_ARCADE_MARKDOWN_MODE=auto

# auto 模式下允许走 Markdown 通道的 bot.self_id，JSON 数组格式。
MAI_ARCADE_OFFICIAL_BOT_IDS='["1234567890"]'
```

渲染模式含义：

- `auto`：只有 `str(bot.self_id)` 命中 `MAI_ARCADE_OFFICIAL_BOT_IDS` 时发送 Markdown；白名单为空时安全地使用纯文本。
- `on`：强制构建 Gensokyo Markdown 消息段，适合开发或单实例部署。
- `off`：始终发送纯文本。

## 官方 QQ Bot Markdown 和按钮

适配层构建 Gensokyo 的嵌套 OneBot 消息段，由 Gensokyo 解包为官方 QQ Bot `msg_type=2` 请求。核心结构如下：

```python
MessageSegment(
    type="markdown",
    data={
        "data": {
            "markdown": {"content": "# 舞萌机厅助手\n\n请选择操作。"},
            "keyboard": {
                "content": {
                    "rows": [
                        {
                            "buttons": [
                                {
                                    "id": "arcade_help_list",
                                    "render_data": {
                                        "label": "机厅列表",
                                        "visited_label": "机厅列表",
                                        "style": 1,
                                    },
                                    "action": {
                                        "type": 2,
                                        "permission": {"type": 2},
                                        "data": "机厅列表",
                                        "enter": False,
                                        "reply": False,
                                        "unsupport_tips": "请手动发送：机厅列表",
                                    },
                                }
                            ]
                        }
                    ]
                }
            },
        }
    },
)
```

协议约定：

- `action.type=2`：复用现有 NoneBot 指令的指令按钮。
- `action.type=0`：URL 按钮，只接受 `http://` 或 `https://`。
- 不使用回调按钮 `action.type=1`，因此没有新增 callback matcher 或业务协议。
- 权限类型 `0/1/2` 分别表示指定用户、群管理员、所有人。
- 样式只使用官方值 `0/1/3/4`，不使用旧示例中的 `style=2`。
- 项目侧限制每行最多 2 个按钮、每条消息最多 5 行，按钮 ID 在同一键盘内必须唯一，按钮标签不超过 10 个字符。
- 默认 `enter=false`，点击后由用户确认输入框内容再发送，避免人数、删除和闭店操作误触。
- Markdown、Keyboard 或消息长度错误会进行有界降级：优先去掉键盘，再回退纯文本；不会无限重试，也不会把认证或限流错误伪装成成功。

协议参考：[QQ 机器人 API v2](https://bot.q.qq.com/wiki/develop/api-v2/)、[官方 Markdown 消息文档](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/type/markdown.html) 和 [Gensokyo Markdown 消息转换说明](https://github.com/Te-River/Gensokyo-NewQQ/blob/main/docs/%E6%96%87%E6%A1%A3-markdown%E6%B6%88%E6%81%AF.md)。

## Gensokyo Release015 兼容

插件按 Gensokyo Release015 的 OneBot V11 事件字段工作：群消息中的 `sender.role` 使用 `owner`、`admin`、`member` 三个值，管理员鉴权由 `GROUP_ADMIN | GROUP_OWNER` 和服务端权限检查完成。按钮里的 `permission.type=1` 只负责客户端侧的管理员可点击提示，不能替代服务端鉴权。

Markdown 消息继续使用 Release015 支持的 `data.data.markdown` 与 `data.data.keyboard.content.rows` 双层结构；不会依赖 Release015 之后新增的 CQ 码。使用 Release015 配置时保持 `cq_parse_mode: legacy`，本插件的指令、URL、Markdown 和键盘消息均按该模式的兼容路径发送。

## Gensokyo 位置消息

Gensokyo 的官方 QQ Bot 位置卡片目前可能只转发地点名称和地址，不保证携带 `lat/lon`。插件不会用 `0,0` 或猜测坐标调用 Nearcade；收到这种卡片时会明确回复“缺少经纬度”，并提示可发送的文本格式。

如果要查询附近机厅，请按 Gensokyo 位置文档的文本兼容格式发送：

```text
位置：栖霞区迈皋桥壹城 (32.112606, 118.834837)
```

也接受标准 CQ 位置段（`[CQ:location,lat=...,lon=...,title=...,content=...]`）、带有 `latitude/longitude`、`lat/lng` 或 `经纬度` 标记的文本，以及 OneBot 原生 `location` 段。收到有效坐标后，插件才会调用 Nearcade 附近发现接口；没有坐标时只发送缺少坐标的说明，不触发 Nearcade 查询。

注意：标准 CQ 位置段适用于能够转发 OneBot 位置段的来源；Gensokyo 官方 QQ Bot 的入站位置卡片目前仍可能只转换成上面的 `[卡片消息] 位置卡片` 文本，因此不能从这段文本反推出真实经纬度。

参考：[Gensokyo 标准 CQ 位置说明](https://github.com/Te-River/Gensokyo-NewQQ/blob/main/docs/cq%E7%A0%81/%E6%A0%87%E5%87%86CQ%E7%A0%81/%E6%A0%87%E5%87%86cq%E7%A0%81-cq-location.md)。

## 常用命令

### 人数

| 命令 | 权限 | 说明 |
| --- | --- | --- |
| `<机厅名>++` / `<机厅名>--` | 群员 | 人数加一或减一，并尝试同步 Nearcade |
| `<机厅名>+num` / `<机厅名>-num` | 群员 | 按指定数值调整 |
| `<机厅名>=num` | 群员 | 重置当前人数 |
| `<机厅名>几` / `<机厅名>几人` / `<机厅名>j` | 群员 | 查询人数、机台和预计等待 |
| `mai` / `机厅人数` | 群员 | 查看今日已更新的机厅 |

别名也可以直接用于人数命令，例如：

```text
wxha几
```

### 机厅、别名和地图

| 命令 | 权限 | 说明 |
| --- | --- | --- |
| `添加群聊` / `删除群聊` | 管理员 | 启用或移除当前群的机厅功能 |
| `添加机厅` / `删除机厅` | 管理员 | 管理本群机厅；添加时可搜索 Nearcade |
| `机厅列表` | 群员 | 查看本群机厅和序号 |
| `添加机厅别名` / `删除机厅别名` | 管理员 | 管理别名 |
| `机厅别名` | 群员 | 查看别名 |
| `添加机厅地图` / `删除机厅地图` | 按 handler 权限 | 管理地图 URL |
| `机厅地图` | 群员 | 查看地图 URL |

机厅名、别名和地图 URL 支持使用列表序号。示例：

```text
添加机厅别名 南京城北万象汇天空之城 wxha
wxha几
删除机厅别名 南京城北万象汇天空之城 wxha
```

### 排卡

| 命令 | 权限 | 说明 |
| --- | --- | --- |
| `排卡 <机厅名>` | 群员 | 加入队列 |
| `排卡现状 <机厅名>` | 群员 | 查看队列 |
| `上机` | 群员 | 当前第一位上机并轮转队列 |
| `延后` | 群员 | 将自己延后一位 |
| `退勤` | 群员 | 退出队列 |
| `闭店 <机厅名>` | 管理员 | 清空该机厅队列 |

完整帮助可在群内发送：

```text
机厅帮助
```

## 数据和隐私

- 使用 `nonebot-plugin-localstore` 保存群聊机厅数据，默认文件为 `arcade_data.json`。
- 数据按群聊隔离，包含人数、别名、地图、排卡和最近更新信息。
- Nearcade token 只从运行环境读取，不写入仓库，也不应出现在日志和公开 issue 中。
- 外部 URL 按协议校验后才会生成 URL 按钮；不允许 `javascript:` 等危险协议。
- 删除或迁移仓库前，请先备份 localstore 数据目录。

## 开发和测试

在仓库目录执行：

```powershell
python -m pytest -q
python -m compileall -q .
git diff --check
```

测试覆盖 Markdown/Keyboard 的嵌套结构、按钮权限和样式、用户绑定按钮、URL 校验、白名单模式、降级重试和消息拆分。测试使用 mock，不会调用线上 Nearcade，也不会读取生产群数据。

本地测试通过不等同于 QQ 客户端实机显示通过；实际部署还需要分别验证 Gensokyo 转换、QQ API 返回码以及客户端按钮点击行为。

## 许可证和致谢

本项目采用 [MIT License](LICENSE)。Nearcade 数据与服务归 Nearcade 平台所有；使用 Nearcade API 时请遵守其服务条款和接口限制。

本仓库是 Amia 对舞萌机厅插件的独立维护版本，保留原插件的功能思路和许可证要求，并在此基础上增加 Amia 的运行时兼容、官方 QQ Bot Markdown/Keyboard 适配和测试覆盖。

项目地址：[Amia-Mizuki-Dev-Team/Amia-plugin-mai-arcade](https://github.com/Amia-Mizuki-Dev-Team/Amia-plugin-mai-arcade)
