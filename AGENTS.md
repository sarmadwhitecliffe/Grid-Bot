# AGENTS.md — Grid Bot Agent Guide

This repository is an async Python grid trading bot with backtesting.
Follow these guidelines when editing code.

---

## Build, Lint, Test

### Install
```bash
make install
```

### Run the bot
```bash
bash run_grid_bot.sh
# or
python main.py
```

### Lint and format
```bash
make lint
make format
```

### Security scan
```bash
make security
```

### Full test suite
```bash
make test
# or
pytest tests/ -v --tb=short --cov=src --cov-report=term-missing --cov-report=html
```

### Single test file
```bash
pytest tests/test_grid_calculator.py -v
```

### Single test by name
```bash
pytest tests/test_grid_calculator.py::TestGeometricLevels::test_correct_number_of_levels -v
```

### Async test file
```bash
pytest tests/test_price_feed.py -v
```

---

## Configuration Rules

- All configurable values live in `config/optimization_space.yaml` or `.env`.
- Do not add new hardcoded constants in `src/` or `scripts/`.
- `config/settings.py` uses `Pydantic BaseSettings`; environment variables override defaults.
- Commit `.env.example`, never `.env`.

---

## Architecture Map (per Copilot rules)

```
Config → Data → Strategy → OMS → Risk → Monitoring
```

- **Config**: `config/settings.py` + `config/optimization_space.yaml`
- **Data**: `src/data/price_feed.py`
- **Exchange**: `src/exchange/exchange_client.py`
- **Strategy**: `src/strategy/`
- **OMS**: `src/oms/`
- **Risk**: `src/risk/risk_manager.py`
- **Persistence**: `src/persistence/state_store.py`
- **Monitoring**: `src/monitoring/alerting.py`
- **Backtest**: `src/backtest/grid_backtester.py`

---

## Code Style and Conventions

### Imports
- Order: stdlib → third-party → local (blank line between groups).
- Use `import ccxt.async_support as ccxt` for async CCXT.

```python
import asyncio
import logging
from typing import Dict, List, Optional

import ccxt.async_support as ccxt
from pydantic import Field

from config.settings import GridBotSettings
```

### Formatting
- Line length: 100.
- Indentation: 4 spaces, no tabs.
- Strings: double quotes.
- Use trailing commas in multi-line literals.

### Type hints and docstrings
- All functions must include type hints and docstrings.
- Use Google-style docstrings with Args/Returns/Raises.

```python
def calculate_levels(
    centre_price: float,
    spacing_pct: float,
    num_levels: int,
) -> List[float]:
    """Generate evenly spaced levels.

    Args:
        centre_price: Price at calculation time.
        spacing_pct: Percentage gap between levels.
        num_levels: Total levels to generate.

    Returns:
        Sorted list of price levels.
    """
    ...
```

### Naming
- Variables: `snake_case`.
- Constants: `UPPER_CASE`.
- Classes: `PascalCase`.
- Private methods: prefix `_`.
- Settings fields: `UPPER_CASE` in `GridBotSettings`.

### Error handling
- Catch specific exceptions; never use bare `except:`.
- Log context before raising.
- Retry only on `ccxt.NetworkError` and `ccxt.RequestTimeout`.

```python
try:
    await exchange.fetch_ticker(symbol)
except ccxt.NetworkError as exc:
    logger.warning("Network error: %s", exc)
    raise
```

---

## Async-First Rules

- All exchange I/O is `async`/`await`.
- Always set `enableRateLimit=True` on ccxt clients.
- Use exponential backoff (delays [1, 2, 5] seconds, max 3 attempts).

---

## Strategy Rules

- `src/strategy/regime_detector.py` and `src/strategy/grid_calculator.py` are pure.
- No shared state; inputs in, outputs out.
- Grid spacing:
  - `geometric`: uses `GRID_SPACING_PCT`.
  - `arithmetic`: uses `GRID_SPACING_ABS`.
- Prices must be quantized to exchange `price_step`.

---

## Testing Rules

- Use `pytest` + `pytest-asyncio`.
- Mock exchange calls; never hit live APIs in tests.
- Test files mirror `src/` structure under `tests/`.

---

## Reference Files

- Copilot rules: `.github/copilot-instructions.md`
- Workflow: `.github/AGENT_WORKFLOW.md`
- Python conventions: `.github/instructions/python.instructions.md`
- Phase plans: `plan/feature-grid-bot-phase[1-5]-1.md`
