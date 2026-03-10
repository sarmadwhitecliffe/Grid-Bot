"""
Tests for the bot_v2 webhook server.

Tests HTTP endpoints, bot lifecycle management, signal processing, and error handling.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def mock_bot():
    """Create a mock TradingBot instance."""
    bot = MagicMock()
    bot.is_running = True
    bot.run = AsyncMock()
    bot.handle_webhook_signal = AsyncMock()
    bot.get_status_message = AsyncMock(
        return_value="Bot Status: All systems operational"
    )
    return bot


@pytest.fixture
def mock_strategy_config():
    """Create a mock StrategyConfig."""
    config = MagicMock()
    config.exchange = "binance"
    config.symbols = ["BTCUSDT", "ETHUSDT"]
    return config


class TestHealthEndpoint:
    """Test the /health endpoint."""

    def test_health_check_bot_running(self, mock_bot):
        """Test health check when bot is running."""
        with patch("webhook_server.BOT_IMPORTED_SUCCESSFULLY", True):
            # Import after patching
            from webhook_server import app

            # Mock the app state
            app.state.bot = mock_bot
            app.state.trading_enabled = True

            client = TestClient(app)
            response = client.get("/health")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
            assert data["bot_module_loaded"] is True
            assert data["bot_is_running"] is True
            assert data["trading_is_enabled"] is True
            assert "timestamp" in data

    def test_health_check_bot_not_running(self):
        """Test health check when bot is not running."""
        with patch("webhook_server.BOT_IMPORTED_SUCCESSFULLY", True):
            from webhook_server import app

            app.state.bot = None
            app.state.trading_enabled = False

            client = TestClient(app)
            response = client.get("/health")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
            assert data["bot_module_loaded"] is True
            assert data["bot_is_running"] is False
            assert data["trading_is_enabled"] is False

    def test_health_check_bot_import_failed(self):
        """Test health check when bot module failed to import."""
        with patch("webhook_server.BOT_IMPORTED_SUCCESSFULLY", False):
            from webhook_server import app

            app.state.bot = None
            app.state.trading_enabled = False

            client = TestClient(app)
            response = client.get("/health")

            assert response.status_code == 200
            data = response.json()
            assert data["bot_module_loaded"] is False


class TestStatusEndpoint:
    """Test the /status endpoint."""

    def test_status_bot_running(self, mock_bot):
        """Test status endpoint when bot is running."""
        with patch("webhook_server.BOT_IMPORTED_SUCCESSFULLY", True):
            from webhook_server import app

            app.state.bot = mock_bot

            client = TestClient(app)
            response = client.get("/status")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
            assert "Bot Status" in data["message"]
            mock_bot.get_status_message.assert_called_once()

    def test_status_bot_not_running(self):
        """Test status endpoint when bot is not running."""
        with patch("webhook_server.BOT_IMPORTED_SUCCESSFULLY", True):
            from webhook_server import app

            mock_bot = MagicMock()
            mock_bot.is_running = False
            app.state.bot = mock_bot

            client = TestClient(app)
            response = client.get("/status")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "error"
            assert "not running" in data["message"]

    def test_status_no_bot_instance(self):
        """Test status endpoint when bot instance is None."""
        with patch("webhook_server.BOT_IMPORTED_SUCCESSFULLY", True):
            from webhook_server import app

            app.state.bot = None

            client = TestClient(app)
            response = client.get("/status")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "error"

    def test_status_bot_error(self, mock_bot):
        """Test status endpoint when bot raises an error."""
        with patch("webhook_server.BOT_IMPORTED_SUCCESSFULLY", True):
            from webhook_server import app

            mock_bot.get_status_message = AsyncMock(
                side_effect=Exception("Status error")
            )
            app.state.bot = mock_bot

            client = TestClient(app)
            response = client.get("/status")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "error"
            assert "Failed to get status" in data["message"]


class TestWebhookEndpoint:
    """Test the /webhook endpoint."""

    def test_webhook_start_command(self):
        """Test webhook with 'start' command."""
        with patch("webhook_server.BOT_IMPORTED_SUCCESSFULLY", True):
            from webhook_server import app

            app.state.trading_enabled = False

            client = TestClient(app)
            response = client.post("/webhook", json={"action": "start"})

            assert response.status_code == 202
            data = response.json()
            assert "enabled" in data["status"].lower()
            assert app.state.trading_enabled is True

    def test_webhook_stop_command(self, mock_bot):
        """Test webhook with 'stop' command."""
        with patch("webhook_server.BOT_IMPORTED_SUCCESSFULLY", True):
            from webhook_server import app

            app.state.bot = mock_bot
            app.state.trading_enabled = True

            client = TestClient(app)
            response = client.post("/webhook", json={"action": "stop"})

            assert response.status_code == 202
            data = response.json()
            assert "disabled" in data["status"].lower()
            assert app.state.trading_enabled is False

    def test_webhook_buy_signal(self, mock_bot):
        """Test webhook with 'buy' signal."""
        with patch("webhook_server.BOT_IMPORTED_SUCCESSFULLY", True):
            from webhook_server import app

            app.state.bot = mock_bot
            app.state.trading_enabled = True

            client = TestClient(app)
            response = client.post(
                "/webhook", json={"action": "buy", "symbol": "TEST/USDT"}
            )

            assert response.status_code == 202
            data = response.json()
            assert "accepted" in data["status"].lower()
            mock_bot.handle_webhook_signal.assert_called_once()

    def test_webhook_sell_signal(self, mock_bot):
        """Test webhook with 'sell' signal."""
        with patch("webhook_server.BOT_IMPORTED_SUCCESSFULLY", True):
            from webhook_server import app

            app.state.bot = mock_bot
            app.state.trading_enabled = True

            client = TestClient(app)
            response = client.post(
                "/webhook", json={"action": "sell", "symbol": "FAKE/USDT"}
            )

            assert response.status_code == 202
            mock_bot.handle_webhook_signal.assert_called_once()

    def test_webhook_exit_signal(self, mock_bot):
        """Test webhook with 'exit' signal."""
        with patch("webhook_server.BOT_IMPORTED_SUCCESSFULLY", True):
            from webhook_server import app

            app.state.bot = mock_bot
            app.state.trading_enabled = True

            client = TestClient(app)
            response = client.post(
                "/webhook", json={"action": "exit", "symbol": "TEST/USDT"}
            )

            assert response.status_code == 202
            mock_bot.handle_webhook_signal.assert_called_once()

    def test_webhook_with_metadata(self, mock_bot):
        """Test webhook with metadata payload."""
        with patch("webhook_server.BOT_IMPORTED_SUCCESSFULLY", True):
            from webhook_server import app

            app.state.bot = mock_bot
            app.state.trading_enabled = True

            client = TestClient(app)
            response = client.post(
                "/webhook",
                json={
                    "action": "buy",
                    "symbol": "TEST/USDT",
                    "metadata": {
                        "gate_message": "All gates passed",
                        "confidence": 0.85,
                    },
                },
            )

            assert response.status_code == 202
            # Verify metadata was passed through
            call_args = mock_bot.handle_webhook_signal.call_args[0][0]
            assert call_args["metadata"]["confidence"] == 0.85

    def test_webhook_trading_disabled(self, mock_bot):
        """Test webhook when trading is disabled."""
        with patch("webhook_server.BOT_IMPORTED_SUCCESSFULLY", True):
            from webhook_server import app

            app.state.bot = mock_bot
            app.state.trading_enabled = False

            client = TestClient(app)
            response = client.post(
                "/webhook", json={"action": "buy", "symbol": "TEST/USDT"}
            )

            assert response.status_code == 202
            data = response.json()
            assert "ignored" in data["status"].lower()
            mock_bot.handle_webhook_signal.assert_not_called()

    def test_webhook_bot_not_running(self):
        """Test webhook when bot is not running."""
        with patch("webhook_server.BOT_IMPORTED_SUCCESSFULLY", True):
            from webhook_server import app

            mock_bot = MagicMock()
            mock_bot.is_running = False
            app.state.bot = mock_bot
            app.state.trading_enabled = True

            client = TestClient(app)
            response = client.post(
                "/webhook", json={"action": "buy", "symbol": "TEST/USDT"}
            )

            assert response.status_code == 503
            assert "not running" in response.json()["detail"]

    def test_webhook_missing_symbol(self, mock_bot):
        """Test webhook without required symbol."""
        with patch("webhook_server.BOT_IMPORTED_SUCCESSFULLY", True):
            from webhook_server import app

            app.state.bot = mock_bot
            app.state.trading_enabled = True

            client = TestClient(app)
            response = client.post("/webhook", json={"action": "buy"})

            assert response.status_code == 422
            assert "symbol" in response.json()["detail"].lower()

    def test_webhook_invalid_action(self, mock_bot):
        """Test webhook with invalid action."""
        with patch("webhook_server.BOT_IMPORTED_SUCCESSFULLY", True):
            from webhook_server import app

            app.state.bot = mock_bot
            app.state.trading_enabled = True

            client = TestClient(app)
            response = client.post(
                "/webhook", json={"action": "invalid_action", "symbol": "TEST/USDT"}
            )

            assert response.status_code == 422
            assert "unrecognized" in response.json()["detail"].lower()

    def test_webhook_bot_error(self, mock_bot):
        """Test webhook when bot raises an error."""
        with patch("webhook_server.BOT_IMPORTED_SUCCESSFULLY", True):
            from webhook_server import app

            mock_bot.handle_webhook_signal = AsyncMock(
                side_effect=Exception("Bot error")
            )
            app.state.bot = mock_bot
            app.state.trading_enabled = True

            client = TestClient(app)
            response = client.post(
                "/webhook", json={"action": "buy", "symbol": "TEST/USDT"}
            )

            assert response.status_code == 500
            assert "internal server error" in response.json()["detail"].lower()

    def test_webhook_case_insensitive_actions(self, mock_bot):
        """Test that webhook actions are case-insensitive."""
        with patch("webhook_server.BOT_IMPORTED_SUCCESSFULLY", True):
            from webhook_server import app

            app.state.bot = mock_bot
            app.state.trading_enabled = True

            client = TestClient(app)

            # Test uppercase
            response = client.post(
                "/webhook", json={"action": "BUY", "symbol": "TEST/USDT"}
            )
            assert response.status_code == 202

            # Test mixed case
            response = client.post(
                "/webhook", json={"action": "SeLL", "symbol": "FAKE/USDT"}
            )
            assert response.status_code == 202




class TestPydanticModels:
    """Test Pydantic model validation."""

    def test_webhook_payload_valid(self):
        """Test valid webhook payload."""
        from webhook_server import WebhookPayload

        payload = WebhookPayload(
            action="buy", symbol="BTCUSDT", metadata={"key": "value"}
        )

        assert payload.action == "buy"
        assert payload.symbol == "BTCUSDT"
        assert payload.metadata["key"] == "value"

    def test_webhook_payload_minimal(self):
        """Test minimal webhook payload (action only)."""
        from webhook_server import WebhookPayload

        payload = WebhookPayload(action="start")

        assert payload.action == "start"
        assert payload.symbol is None
        assert payload.metadata is None

    def test_health_status_model(self):
        """Test HealthStatus model."""
        from webhook_server import HealthStatus

        now = datetime.now(timezone.utc)
        status = HealthStatus(
            status="ok",
            bot_module_loaded=True,
            bot_is_running=True,
            trading_is_enabled=True,
            timestamp=now,
        )

        assert status.status == "ok"
        assert status.bot_module_loaded is True
        assert status.bot_is_running is True
        assert status.trading_is_enabled is True
        assert status.timestamp == now


class TestServerLifecycle:
    """Test server startup and shutdown lifecycle."""

    @pytest.mark.asyncio
    async def test_lifespan_startup_success(self, mock_bot, mock_strategy_config):
        """Test successful server startup with bot initialization."""
        with patch("webhook_server.BOT_IMPORTED_SUCCESSFULLY", True), patch(
            "webhook_server.TradingBot", return_value=mock_bot
        ), patch("webhook_server.StrategyConfig") as mock_config_cls:
            mock_config_cls.from_file.return_value = mock_strategy_config
            # Create fresh app instance for testing
            from fastapi import FastAPI

            from webhook_server import lifespan

            test_app = FastAPI()
            async with lifespan(test_app):
                assert test_app.state.trading_enabled is True
                assert test_app.state.bot is mock_bot
                mock_bot.run.assert_called_once()

    @pytest.mark.asyncio
    async def test_lifespan_bot_not_imported(self):
        """Test server startup when bot module not imported."""
        with patch("webhook_server.BOT_IMPORTED_SUCCESSFULLY", False):
            from fastapi import FastAPI

            from webhook_server import lifespan

            test_app = FastAPI()

            async with lifespan(test_app):
                assert test_app.state.trading_enabled is True
                # Bot should not be set when import failed
                bot_attr = getattr(test_app.state, "bot", None)
                assert bot_attr is None

    @pytest.mark.asyncio
    async def test_lifespan_shutdown_graceful(self, mock_bot):
        """Test graceful bot shutdown."""
        with patch("webhook_server.BOT_IMPORTED_SUCCESSFULLY", True), patch(
            "webhook_server.TradingBot", return_value=mock_bot
        ), patch("webhook_server.StrategyConfig") as mock_config_cls:
            mock_config_cls.from_file.return_value = MagicMock()
            from fastapi import FastAPI

            from webhook_server import lifespan

            test_app = FastAPI()
            # Mock bot task that's not done
            mock_task = AsyncMock()
            mock_task.done = MagicMock(return_value=False)
            async with lifespan(test_app):
                test_app.state.bot_task = mock_task
                # Exit context to trigger shutdown
            # Verify bot was signaled to stop
            assert mock_bot.is_running is False
