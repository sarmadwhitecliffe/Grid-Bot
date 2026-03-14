# Bot V2 - Modular Trading Bot

## Overview
`bot_v2` is the re-architected, modular version of the trading bot, designed for stability, testability, and scalability. It separates concerns into distinct modules for execution, risk management, signal processing, and position tracking.

## Key Components

### 1. Core (`bot.py`)
The central orchestrator (`TradingBot`) that integrates all components.
- **Responsibilities**: Signal processing loop, position monitoring, state persistence, and heartbeat management.
- **Configuration**: Supports both single-symbol and multi-symbol modes via `StrategyConfig`.

### 2. Risk Management (`risk/`)
- **Adaptive Risk Manager**: Tier-based position sizing based on performance metrics (Profit Factor, Win Rate).
- **Capital Manager**: Centralized capital tracking per symbol. **Note**: "Mode" (Live/Sim) is now strictly config-based and not stored in `CapitalManager`.
- **Integration**: `AdaptiveRiskIntegration` bridges the V2 bot with the risk logic.

### 3. Execution (`execution/`)
- **Order Manager**: Handles order creation with safety checks (Max Notional, Daily Limits).
- **Exchange Interfaces**: `LiveExchange` (CCXT) and `SimulatedExchange` for paper trading.
- **State Manager**: Tracks order lifecycles and reconciles with the exchange.

### 4. Signals (`signals/`)
- **Signal Processor**: Normalizes and routes incoming webhook signals.
- **Concurrency**: Uses `asyncio.Semaphore` to limit concurrent signal processing.

### 5. Position Management (`position/`)
- **Tracker**: Tracks active positions and calculates PnL.
- **Trailing Stop**: Advanced trailing stop logic (Breakeven, R-decay, Ratchet).

### 6. Grid Strategy (`grid/`)
- **Grid Orchestrator**: Manages high-frequency grid trading sessions.
- **Features**: Automatic re-centering, regime-gated execution, and scale-out counter-orders.
- **Safety**: Integrated with "Quick-Bank" guardrails (5% TP, 7% Max DD).

## Webhook Commands

The bot supports remote management via JSON webhooks (POST to `/webhook`):

| Command | Payload Example | Description |
| :--- | :--- | :--- |
| **`LONG SYMBOL`** | `{"action": "LONG HYPE", "symbol": "HYPE"}` or `{"action": "LONG", "symbol": "HYPEUSDT"}` | Enter long position. Symbol can be HYPE, HYPE/USDT, or HYPEUSDT. |
| **`SHORT SYMBOL`** | `{"action": "SHORT HYPE", "symbol": "HYPE"}` or `{"action": "SHORT", "symbol": "HYPEUSDT"}` | Enter short position. |
| **`EXIT SYMBOL`** | `{"action": "EXIT HYPE", "symbol": "HYPE"}` or `{"action": "EXIT", "symbol": "HYPEUSDT"}` | Exit all positions for symbol. |
| **`STATUS`** | `{"action": "STATUS"}` | Get current positions status. |
| **`SUMMARY`** | `{"action": "SUMMARY", "symbol": "24"}` or `{"action": "SUMMARY", "metadata": {"hours": 168}}` | Get 24h performance summary. Use symbol="168" or metadata.hours for 7 days. |
| **`START`** | `{"action": "START"}` | Enable trading (signals will be processed). |
| **`STOP`** | `{"action": "STOP"}` | Disable trading (signals ignored). |
| **`grid_start`** | `{"action": "grid_start", "symbol": "BTC/USDT"}` | Deploys a new grid for the specified symbol. |
| **`grid_stop`** | `{"action": "grid_stop", "symbol": "BTC/USDT"}` | Stops the active grid and cancels all pending levels. |
| **`buy`** | `{"action": "buy", "symbol": "ETH/USDT"}` | Executes a standard long entry signal (legacy). |
| **`sell`** | `{"action": "sell", "symbol": "ETH/USDT"}` | Executes a standard short entry signal (legacy). |
| **`exit`** | `{"action": "exit", "symbol": "ETH/USDT"}` | Closes all positions for the specified symbol (legacy). |

## Known Limitations & Runtime Considerations

### Daily Limits
- **Behavior**: Daily trade counts and max notional limits are tracked **in-memory**.
- **Risk**: Restarting the bot resets these counters. Do not rely solely on the bot for regulatory daily limits if frequent restarts are expected.

### Signal Processing
- **Throughput**: The bot processes all pending signals in the queue before monitoring positions.
- **Risk**: An extreme flood of signals could temporarily delay exit checks.

### Performance
- **Trade History**: Performance metrics are calculated by iterating through the trade history. For accounts with thousands of trades, this may impact performance.

## Testing
Run the full regression suite:
```bash
pytest bot_v2/tests/
```
All tests should pass (Green) before deployment.

## Configuration
The bot relies on `StrategyConfig` objects. Ensure `mode` is correctly set to `"live"` or `"local_sim"` in your configuration JSONs.

### Grid-Specific Parameters
| Key | Default | Description |
| :--- | :--- | :--- |
| `grid_enabled` | `false` | Enable/Disable grid strategy for the symbol. |
| `grid_spacing_pct` | `0.01` (1%) | Percentage distance between grid levels. |
| `grid_num_grids_up` | `25` | Number of sell levels above the centre price. |
| `grid_num_grids_down` | `25` | Number of buy levels below the centre price. |
| `grid_recentre_trigger` | `3` | Number of spacing levels drift before re-centering. |
| `grid_adx_threshold` | `30` | Max ADX value before pausing the grid (trend protection). |
| `grid_bb_width_threshold`| `0.04` | Max BB width before pausing (volatility protection). |
