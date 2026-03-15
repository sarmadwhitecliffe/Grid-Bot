# AGENTS.md — Grid Bot

Project-specific agent guidelines. See global `/Users/sarmads/.config/opencode/AGENTS.md` for general behavior.

---

## Build Commands

| Command | Description |
|---------|-------------|
| `make install` | Install dependencies |
| `make lint` | Run linting |
| `make format` | Format code |
| `make security` | Security scan |
| `make test` | Run full test suite |

### Run Bot (bot_v2)
```bash
# Via webhook server (recommended)
python webhook_server.py

# Direct run (legacy)
bash run_grid_bot.sh
python main.py
```

### Testing
```bash
# bot_v2 tests
pytest bot_v2/tests/ -v

# src/ component tests
pytest tests/test_grid_calculator.py -v
pytest tests/test_price_feed.py -v
```

---

## Architecture

### bot_v2 (Production)
```
webhook_server.py → bot_v2/bot.py → Grid Orchestrator + Position Tracker
```

**Key Components:**
- **webhook_server.py** - FastAPI server, receives signals, manages bot lifecycle
- **bot_v2/bot.py** - Main TradingBot orchestrator
- **bot_v2/grid/orchestrator.py** - Grid strategy (imports from `src/`)
- **bot_v2/execution/order_manager.py** - Order creation, precision quantization
- **bot_v2/execution/live_exchange.py** - CCXT live trading
- **bot_v2/execution/simulated_exchange.py** - Paper trading

### src/ (Shared Library)
```
src/strategy/grid_calculator.py - Stateless grid level generator
src/strategy/regime_detector.py - Market regime detection
src/oms/ - Order management utilities
src/persistence/ - State persistence
```

---

## Project Conventions

- All config in `config/` or `.env`
- Use `ccxt.async_support as ccxt`
- Grid spacing: `geometric` uses `GRID_SPACING_PCT`, `arithmetic` uses `GRID_SPACING_ABS`
- Prices/amounts quantized to exchange precision in OrderManager
- Test files mirror `src/` structure under `tests/`
- bot_v2 is the production system - fix bugs there, not in src/
