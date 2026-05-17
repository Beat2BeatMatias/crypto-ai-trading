"""Notificaciones Telegram para eventos críticos del engine.

Configuración (variables de entorno opcionales):
    TELEGRAM_BOT_TOKEN  — token del bot (sin esto, las notificaciones se silencian)
    TELEGRAM_CHAT_ID    — chat/canal destino (sin esto, las notificaciones se silencian)

Si alguna variable falta, el módulo loguea un warning al inicializarse y
todas las llamadas son no-ops sin lanzar excepciones.
"""
from __future__ import annotations

import os
from enum import Enum
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()

_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
_BASE_URL = "https://api.telegram.org/bot{token}/sendMessage"
_TIMEOUT = 10.0


class TelegramEvent(str, Enum):
    KILL_SWITCH = "kill_switch"
    DAILY_STOP = "daily_stop"
    DRAWDOWN_HIGH = "drawdown_high"
    ENGINE_PAUSED = "engine_paused"
    ENGINE_RESUMED = "engine_resumed"
    SUPERVISOR_ROLLBACK = "supervisor_rollback"
    LLM_FAILURE_STREAK = "llm_failure_streak"
    EXCHANGE_FAILURE_STREAK = "exchange_failure_streak"


_EMOJI = {
    TelegramEvent.KILL_SWITCH: "🚨",
    TelegramEvent.DAILY_STOP: "🛑",
    TelegramEvent.DRAWDOWN_HIGH: "📉",
    TelegramEvent.ENGINE_PAUSED: "⏸️",
    TelegramEvent.ENGINE_RESUMED: "▶️",
    TelegramEvent.SUPERVISOR_ROLLBACK: "🔄",
    TelegramEvent.LLM_FAILURE_STREAK: "🤖❌",
    TelegramEvent.EXCHANGE_FAILURE_STREAK: "🔌❌",
}

_TITLE = {
    TelegramEvent.KILL_SWITCH: "KILL SWITCH activado",
    TelegramEvent.DAILY_STOP: "Daily stop alcanzado",
    TelegramEvent.DRAWDOWN_HIGH: "Drawdown elevado",
    TelegramEvent.ENGINE_PAUSED: "Engine pausado",
    TelegramEvent.ENGINE_RESUMED: "Engine reanudado (auto-reset)",
    TelegramEvent.SUPERVISOR_ROLLBACK: "Supervisor realizó rollback",
    TelegramEvent.LLM_FAILURE_STREAK: "Racha de fallas LLM",
    TelegramEvent.EXCHANGE_FAILURE_STREAK: "Racha de fallas exchange",
}


def _is_configured() -> bool:
    if not _BOT_TOKEN or not _CHAT_ID:
        logger.debug("telegram.not_configured", has_token=bool(_BOT_TOKEN), has_chat=bool(_CHAT_ID))
        return False
    return True


def _build_message(event: TelegramEvent, details: dict[str, Any]) -> str:
    emoji = _EMOJI.get(event, "⚠️")
    title = _TITLE.get(event, event.value)
    lines = [f"{emoji} *{title}*"]
    for key, value in details.items():
        lines.append(f"• {key}: `{value}`")
    return "\n".join(lines)


async def notify(event: TelegramEvent, details: dict[str, Any] | None = None) -> None:
    """Envía una notificación Telegram de forma no-bloqueante.

    Silencia cualquier excepción de red para no interrumpir el engine.
    """
    if not _is_configured():
        return

    text = _build_message(event, details or {})
    url = _BASE_URL.format(token=_BOT_TOKEN)
    payload = {"chat_id": _CHAT_ID, "text": text, "parse_mode": "Markdown"}

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, json=payload)
            if not resp.is_success:
                logger.warning(
                    "telegram.send_failed",
                    event=event,
                    status=resp.status_code,
                    body=resp.text[:200],
                )
    except Exception as exc:
        logger.warning("telegram.send_error", event=event, error=str(exc))
