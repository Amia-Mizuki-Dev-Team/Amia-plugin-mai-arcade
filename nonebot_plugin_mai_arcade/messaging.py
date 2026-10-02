"""Markdown and official QQ keyboard delivery for the arcade plugin.

The upstream plugin uses ordinary strings and ``MessageSegment.text`` values.
This module keeps those calls source-compatible while installing a small,
plugin-local transport adapter on each matcher class.  Gensokyo receives the
OneBot Markdown segment and unwraps it into the official QQ ``msg_type=2``
request; protocol-format failures retain the original plain text while
authentication, rate-limit, and network failures remain visible to NoneBot.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Sequence
from urllib.parse import urlparse

from loguru import logger
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.exception import AdapterException, ActionFailed, NetworkError
from nonebot.matcher import Matcher, current_bot

from .config import plugin_config


ButtonAction = Literal[0, 2]
ButtonPermission = Literal[0, 1, 2]
ButtonStyle = Literal[0, 1, 3, 4]

_DELIVERY_ERRORS = (ActionFailed, NetworkError, AdapterException)
_MARKDOWN_SPECIALS = re.compile(r"([\\`*_\[\]~])")
_MAX_KEYBOARD_ROWS = 5
_MAX_BUTTONS_PER_ROW = 2


@dataclass(frozen=True, slots=True)
class ButtonSpec:
    """A button using the official QQ Bot keyboard vocabulary."""

    label: str
    data: str
    action_type: ButtonAction = 2
    permission_type: ButtonPermission = 2
    specify_user_ids: tuple[str, ...] = ()
    style: ButtonStyle = 1
    button_id: str | None = None
    visited_label: str | None = None
    enter: bool = False
    reply: bool = False
    unsupport_tips: str = "请手动发送对应指令"

    def __post_init__(self) -> None:
        if self.action_type not in (0, 2):
            raise ValueError("mai_arcade 只支持官方 URL(type=0) 和指令(type=2)按钮")
        if self.permission_type not in (0, 1, 2):
            raise ValueError("按钮 permission.type 必须为 0、1 或 2")
        if self.style not in (0, 1, 3, 4):
            raise ValueError("按钮 render_data.style 必须为 0、1、3 或 4")
        if not self.label or len(self.label) > 10:
            raise ValueError("按钮 label 必须为 1 至 10 个字符")
        if self.visited_label is not None and len(self.visited_label) > 10:
            raise ValueError("按钮 visited_label 不得超过 10 个字符")
        if self.permission_type == 0 and not self.specify_user_ids:
            raise ValueError("permission.type=0 时必须指定 specify_user_ids")
        if self.action_type == 0:
            parsed = urlparse(self.data)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("URL 按钮只允许带主机名的 http/https 地址")


@dataclass(frozen=True, slots=True)
class CommandButton(ButtonSpec):
    """Typed view of a command button (official ``action.type=2``)."""

    action_type: ButtonAction = field(default=2, init=False)


@dataclass(frozen=True, slots=True)
class LinkButton(ButtonSpec):
    """Typed view of a URL button (official ``action.type=0``)."""

    action_type: ButtonAction = field(default=0, init=False)


@dataclass(frozen=True, slots=True)
class ReplySpec:
    """Markdown content, its plain-text fallback and optional keyboard rows."""

    markdown: str
    fallback_text: str | None = None
    rows: tuple[tuple[ButtonSpec, ...], ...] = field(default_factory=tuple)

    @property
    def fallback(self) -> str:
        return self.fallback_text if self.fallback_text is not None else self.markdown


def command_button(
    label: str,
    command: str,
    *,
    button_id: str | None = None,
    permission: ButtonPermission = 2,
    user_ids: Iterable[str] = (),
    style: ButtonStyle = 1,
    visited_label: str | None = None,
    unsupport_tips: str | None = None,
) -> CommandButton:
    """Create an official type-2 command button."""

    return CommandButton(
        label=label,
        data=command,
        permission_type=permission,
        specify_user_ids=tuple(str(item) for item in user_ids),
        style=style,
        button_id=button_id,
        visited_label=visited_label,
        unsupport_tips=unsupport_tips or f"请手动发送：{command}",
    )


def link_button(
    label: str,
    url: str,
    *,
    button_id: str | None = None,
    permission: ButtonPermission = 2,
    user_ids: Iterable[str] = (),
    style: ButtonStyle = 0,
    visited_label: str | None = None,
    unsupport_tips: str = "当前客户端无法打开该链接",
) -> LinkButton:
    """Create an official type-0 URL button."""

    return LinkButton(
        label=label,
        data=url,
        permission_type=permission,
        specify_user_ids=tuple(str(item) for item in user_ids),
        style=style,
        button_id=button_id,
        visited_label=visited_label,
        unsupport_tips=unsupport_tips,
    )


def safe_link_button(
    label: str,
    url: str,
    **kwargs: Any,
) -> LinkButton | None:
    """Return a URL button only when the value is safe for the QQ protocol.

    User-maintained map data and external service responses predate this
    adapter, so a malformed URL must not turn an otherwise valid text reply
    into an exception.  ``link_button`` remains strict for tests and callers
    that want validation errors; handlers use this bounded helper when a URL
    is optional UI decoration.
    """

    try:
        return link_button(label, url, **kwargs)
    except (TypeError, ValueError):
        logger.warning("mai_arcade 忽略不合法的地图/详情 URL 按钮")
        return None


def reply_spec(
    markdown: str,
    *,
    fallback_text: str | None = None,
    rows: Sequence[Sequence[ButtonSpec]] = (),
) -> ReplySpec:
    """Build a reply while keeping rows immutable for matcher reuse."""

    return ReplySpec(
        markdown=markdown,
        fallback_text=fallback_text,
        rows=tuple(tuple(row) for row in rows),
    )


def _escape_inline(text: str) -> str:
    return _MARKDOWN_SPECIALS.sub(r"\\\1", text)


def plain_to_markdown(text: str) -> str:
    """Escape dynamic inline Markdown without destroying line structure."""

    return "\n".join(_escape_inline(line) for line in text.splitlines())


def escape_markdown(text: Any) -> str:
    """Escape a dynamic value that is inserted into a hand-written template."""

    return _escape_inline(str(text))


def _text_from_message(message: Any) -> str | None:
    if isinstance(message, str):
        return message
    if isinstance(message, MessageSegment) and message.type == "text":
        return str((message.data or {}).get("text", ""))
    if isinstance(message, Message) and all(
        segment.type == "text" for segment in message
    ):
        return "".join(str((segment.data or {}).get("text", "")) for segment in message)
    return None


def _button_id(button: ButtonSpec, row_index: int, button_index: int) -> str:
    if button.button_id:
        return button.button_id
    digest = hashlib.sha1(
        f"{row_index}:{button_index}:{button.action_type}:{button.label}:{button.data}".encode(
            "utf-8"
        )
    ).hexdigest()[:12]
    return f"arcade_{digest}"


def _button_payload(
    button: ButtonSpec, row_index: int, button_index: int
) -> dict[str, Any]:
    action: dict[str, Any] = {
        "type": button.action_type,
        "permission": {"type": button.permission_type},
        "data": button.data,
        "unsupport_tips": button.unsupport_tips,
    }
    if button.permission_type == 0:
        action["permission"]["specify_user_ids"] = list(button.specify_user_ids)
    if button.action_type == 2:
        action.update({"enter": button.enter, "reply": button.reply})

    return {
        "id": _button_id(button, row_index, button_index),
        "render_data": {
            "label": button.label,
            "visited_label": button.visited_label or button.label,
            "style": button.style,
        },
        "action": action,
    }


def build_markdown_segment(spec: ReplySpec) -> MessageSegment:
    """Create the exact nested OneBot/Gensokyo Markdown segment."""

    payload: dict[str, Any] = {"markdown": {"content": spec.markdown}}
    if spec.rows:
        if len(spec.rows) > _MAX_KEYBOARD_ROWS:
            raise ValueError("keyboard rows 超过项目侧安全布局上限 5")
        seen: set[str] = set()
        rows: list[dict[str, Any]] = []
        for row_index, row in enumerate(spec.rows):
            if len(row) > _MAX_BUTTONS_PER_ROW:
                raise ValueError("keyboard 每行超过项目侧安全布局上限 2")
            buttons = []
            for button_index, button in enumerate(row):
                item = _button_payload(button, row_index, button_index)
                if item["id"] in seen:
                    raise ValueError(f"keyboard button id 重复: {item['id']}")
                seen.add(item["id"])
                buttons.append(item)
            if buttons:
                rows.append({"buttons": buttons})
        if rows:
            payload["keyboard"] = {"content": {"rows": rows}}
    return MessageSegment(type="markdown", data={"data": payload})


def markdown_enabled(bot: Any) -> bool:
    mode = getattr(plugin_config, "mai_arcade_markdown_mode", "auto")
    if mode == "off":
        return False
    if mode == "on":
        return True
    bot_id = getattr(bot, "self_id", None)
    configured = getattr(plugin_config, "mai_arcade_official_bot_ids", [])
    return bot_id is not None and str(bot_id) in {str(item) for item in configured}


def _coerce_spec(message: Any) -> ReplySpec | None:
    if isinstance(message, ReplySpec):
        return message
    text = _text_from_message(message)
    if text is None:
        return None
    return ReplySpec(markdown=plain_to_markdown(text), fallback_text=text)


def _context_bot() -> Any | None:
    try:
        return current_bot.get()
    except LookupError:
        return None


async def send_reply(matcher: type[Matcher], message: Any, **kwargs: Any) -> Any:
    """Send a text/``ReplySpec`` reply through the installed adapter."""

    return await matcher.send(message, **kwargs)


async def finish_reply(matcher: type[Matcher], message: Any, **kwargs: Any) -> Any:
    """Finish a matcher through the installed adapter."""

    return await matcher.finish(message, **kwargs)


async def pause_reply(matcher: type[Matcher], message: Any, **kwargs: Any) -> Any:
    """Pause a matcher through the installed adapter."""

    return await matcher.pause(message, **kwargs)


def _error_text(error: BaseException) -> str:
    info = getattr(error, "info", None)
    return f"{error!r} {info!r}".lower()


def _is_length_error(error: BaseException) -> bool:
    text = _error_text(error)
    return "40054007" in text or "message length" in text or "too long" in text


def _is_format_error(error: BaseException) -> bool:
    """Identify errors for which a bounded Markdown/Keyboard downgrade helps."""

    if isinstance(error, NetworkError):
        return False
    text = _error_text(error)
    if isinstance(error, AdapterException) and not isinstance(error, ActionFailed):
        return True
    return any(
        marker in text
        for marker in (
            "40034011",  # invalid custom Markdown
            "305007",  # invalid keyboard layout/style
            "40034029",  # invalid keyboard payload
            "40034108",  # command payload too long
            "markdown",
            "keyboard",
            "unsupported",
            "not support",
        )
    )


def _split_markdown(content: str, parts: int = 4) -> list[str]:
    lines = content.splitlines()
    if len(lines) < 2:
        return [content]
    chunk_size = max(1, (len(lines) + parts - 1) // parts)
    return [
        "\n".join(lines[start : start + chunk_size])
        for start in range(0, len(lines), chunk_size)
    ]


def _without_keyboard_spec(spec: ReplySpec) -> ReplySpec:
    """Keep Markdown usable when QQ rejects the optional keyboard payload."""

    commands: list[str] = []
    for row in spec.rows:
        for button in row:
            if button.action_type == 2 and button.data not in commands:
                commands.append(button.data)
    markdown = spec.markdown
    if commands:
        manual = "\n".join(f"手动发送：{command}" for command in commands)
        markdown = f"{markdown}\n\n{plain_to_markdown(manual)}"
    return ReplySpec(markdown=markdown, fallback_text=spec.fallback)


def install_markdown_matcher(matcher: type[Matcher]) -> None:
    """Install the adapter once on a plugin-local matcher class.

    ``Matcher.finish`` and ``Matcher.pause`` both call ``cls.send``. Wrapping
    only ``send`` therefore covers all existing upstream calls, including
    error paths and state prompts, without changing their control-flow
    exceptions.
    """

    if getattr(matcher, "_mai_arcade_markdown_installed", False):
        return

    original_send = matcher.send

    async def _send(cls: type[Matcher], message: Any, **kwargs: Any) -> Any:
        spec = _coerce_spec(message)
        if spec is None:
            return await original_send(message, **kwargs)

        bot = _context_bot()
        if bot is None or not markdown_enabled(bot):
            return await original_send(spec.fallback, **kwargs)

        try:
            payload = build_markdown_segment(spec)
        except (TypeError, ValueError) as exc:
            logger.warning("mai_arcade Markdown/Keyboard 构建失败，回退纯文本: {}", exc)
            return await original_send(spec.fallback, **kwargs)

        try:
            return await original_send(payload, **kwargs)
        except _DELIVERY_ERRORS as exc:
            if _is_length_error(exc) and len(spec.markdown.splitlines()) > 1:
                chunks = _split_markdown(spec.markdown)
                try:
                    for index, chunk in enumerate(chunks):
                        chunk_spec = ReplySpec(
                            markdown=chunk,
                            fallback_text=spec.fallback,
                            rows=spec.rows if index == len(chunks) - 1 else (),
                        )
                        await original_send(
                            build_markdown_segment(chunk_spec), **kwargs
                        )
                    return None
                except _DELIVERY_ERRORS as split_exc:
                    if not _is_format_error(split_exc) and not _is_length_error(
                        split_exc
                    ):
                        logger.error("mai_arcade Markdown 拆分发送失败: {}", split_exc)
                        raise
                    logger.warning("mai_arcade Markdown 拆分后仍发送失败，回退纯文本")
                    return await original_send(spec.fallback, **kwargs)

            if not _is_format_error(exc):
                logger.error("mai_arcade Markdown 发送失败，不进行伪装回退: {}", exc)
                raise

            # A valid Markdown body can still be rejected because the keyboard
            # is unsupported or contains a platform-specific field.  Retry
            # once without rows before falling back to the original text.
            logger.warning(
                "mai_arcade Markdown/Keyboard 发送失败，去掉 Keyboard 重试: {}", exc
            )
            try:
                return await original_send(
                    build_markdown_segment(_without_keyboard_spec(spec)),
                    **kwargs,
                )
            except _DELIVERY_ERRORS as markdown_exc:
                if not _is_format_error(markdown_exc):
                    logger.error(
                        "mai_arcade Markdown 重试失败，不进行伪装回退: {}", markdown_exc
                    )
                    raise
                logger.warning("mai_arcade Markdown 重试仍失败，回退纯文本")
                return await original_send(spec.fallback, **kwargs)

    matcher.send = classmethod(_send)
    matcher._mai_arcade_markdown_installed = True


def install_module_matchers(*modules: Any) -> None:
    """Install the adapter on every Matcher class exported by handler modules."""

    seen: set[type[Matcher]] = set()
    for module in modules:
        for value in vars(module).values():
            if (
                isinstance(value, type)
                and issubclass(value, Matcher)
                and value is not Matcher
                and value not in seen
            ):
                seen.add(value)
                install_markdown_matcher(value)
