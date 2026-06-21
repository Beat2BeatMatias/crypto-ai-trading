"""Tests for notifications/telegram.py — event dispatch and message formatting."""
import pytest
from unittest.mock import patch, AsyncMock
from notifications.telegram import TelegramEvent, notify, _build_message, _is_configured


class TestIsConfigured:
    @patch("notifications.telegram._BOT_TOKEN", "")
    @patch("notifications.telegram._CHAT_ID", "")
    def test_not_configured_when_empty(self):
        assert _is_configured() is False

    @patch("notifications.telegram._BOT_TOKEN", "test_token")
    @patch("notifications.telegram._CHAT_ID", "")
    def test_not_configured_missing_chat(self):
        assert _is_configured() is False

    @patch("notifications.telegram._BOT_TOKEN", "")
    @patch("notifications.telegram._CHAT_ID", "test_chat")
    def test_not_configured_missing_token(self):
        assert _is_configured() is False

    @patch("notifications.telegram._BOT_TOKEN", "test_token")
    @patch("notifications.telegram._CHAT_ID", "test_chat")
    def test_configured_when_both_present(self):
        assert _is_configured() is True


class TestBuildMessage:
    def test_kill_switch_has_emoji_and_title(self):
        msg = _build_message(TelegramEvent.KILL_SWITCH, {"reason": "test"})
        assert "KILL SWITCH" in msg
        assert "test" in msg

    def test_includes_all_details(self):
        msg = _build_message(TelegramEvent.ENGINE_PAUSED, {
            "daily_pnl": "-3.2%", "limit": "-3.0%",
        })
        assert "daily_pnl" in msg
        assert "-3.2%" in msg
        assert "ENGINE PAUSED" in msg or "pausado" in msg


class TestNotify:
    @patch("notifications.telegram._is_configured", return_value=False)
    async def test_silent_when_not_configured(self, mock_config):
        result = await notify(TelegramEvent.KILL_SWITCH, {"reason": "test"})
        assert result is None

    @patch("notifications.telegram._is_configured", return_value=True)
    @patch("notifications.telegram.httpx.AsyncClient")
    async def test_sends_http_request(self, mock_client_class, mock_config):
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client
        mock_client.post.return_value.is_success = True
        mock_client.post.return_value.status_code = 200

        await notify(TelegramEvent.DAILY_STOP, {"pnl": "-5%"})
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args[1]
        assert "json" in call_args
        assert "chat_id" in call_args["json"]

    @patch("notifications.telegram._is_configured", return_value=True)
    @patch("notifications.telegram.httpx.AsyncClient")
    async def test_handles_http_error(self, mock_client_class, mock_config):
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client
        mock_client.post.side_effect = Exception("Connection error")

        await notify(TelegramEvent.ENGINE_PAUSED, {"reason": "test"})

    @patch("notifications.telegram._is_configured", return_value=True)
    @patch("notifications.telegram.httpx.AsyncClient")
    async def test_logs_non_success_status(self, mock_client_class, mock_config):
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client
        mock_client.post.return_value.is_success = False
        mock_client.post.return_value.status_code = 403
        mock_client.post.return_value.text = "Forbidden"

        await notify(TelegramEvent.DRAWDOWN_HIGH, {})

    @patch("notifications.telegram._is_configured", return_value=True)
    @patch("notifications.telegram.httpx.AsyncClient")
    async def test_sends_all_event_types(self, mock_client_class, mock_config):
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client
        mock_client.post.return_value.is_success = True

        for event in TelegramEvent:
            await notify(event, {"test": "data"})
        assert mock_client.post.call_count == len(TelegramEvent)


class TestTelegramEvent:
    def test_all_events_have_emoji(self):
        from notifications.telegram import _EMOJI
        for event in TelegramEvent:
            assert event in _EMOJI, f"{event} missing emoji"

    def test_all_events_have_title(self):
        from notifications.telegram import _TITLE
        for event in TelegramEvent:
            assert event in _TITLE, f"{event} missing title"

    def test_unique_values(self):
        values = [e.value for e in TelegramEvent]
        assert len(values) == len(set(values))
