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

### Run Bot
```bash
bash run_grid_bot.sh
# or
python main.py
```

### Testing
```bash
pytest tests/test_grid_calculator.py -v
pytest tests/test_price_feed.py -v
```

---

## Architecture

```
Config → Data → Strategy → OMS → Risk → Monitoring
```

- **Config**: `config/settings.py` + `config/optimization_space.yaml`
- **Data**: `src/data/price_feed.py`
- **Exchange**: `src/exchange/exchange_client.py`
- **Strategy**: `src/strategy/`
- **OMS**: `src/oms/`
- **Risk**: `src/risk/risk_manager.py`
- **Backtest**: `src/backtest/grid_backtester.py`

---

## Project Conventions

- All config in `config/` or `.env`
- Use `ccxt.async_support as ccxt`
- Grid spacing: `geometric` uses `GRID_SPACING_PCT`, `arithmetic` uses `GRID_SPACING_ABS`
- Prices quantized to exchange `price_step`
- Test files mirror `src/` structure under `tests/`
