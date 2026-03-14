# Binance Grid Trading - Optimized Parameters

Generated: 2026-03-14
Capital: $100 per symbol
Backtest Period: 180 days (15m timeframe)

---

## Quick Copy-Paste Guide

### OP/USDT (Best Performer)
- **Grid Spacing**: 0.15%
- **Number of Grids**: 35 (17 up + 18 down)
- **Leverage**: 7x
- **Order Quantity**: $100 per grid
- **Net Profit**: +58.06 USDT (58%)
- **Win Rate**: 74.68%
- **PF**: 4.62

### NEAR/USDT
- **Grid Spacing**: 0.4%
- **Number of Grids**: 50 (25 up + 25 down)
- **Leverage**: 3x
- **Order Quantity**: $100 per grid
- **Net Profit**: +22.30 USDT (22%)
- **Win Rate**: 65.00%
- **PF**: 1.61

### BTC/USDT
- **Grid Spacing**: 0.15%
- **Number of Grids**: 100 (50 up + 50 down)
- **Leverage**: 10x
- **Order Quantity**: $100 per grid
- **Net Profit**: +19.59 USDT (20%)
- **Win Rate**: 76.60%
- **PF**: 5.33

### APT/USDT
- **Grid Spacing**: 0.2%
- **Number of Grids**: 100 (50 up + 50 down)
- **Leverage**: 10x
- **Order Quantity**: $100 per grid
- **Net Profit**: +19.48 USDT (19%)
- **Win Rate**: 59.68%
- **PF**: 2.21

### ETH/USDT
- **Grid Spacing**: 0.4%
- **Number of Grids**: 70 (35 up + 35 down)
- **Leverage**: 10x
- **Order Quantity**: $100 per grid
- **Net Profit**: +15.18 USDT (15%)
- **Win Rate**: 73.42%
- **PF**: 1.86

### ADA/USDT
- **Grid Spacing**: 0.4%
- **Number of Grids**: 100 (50 up + 50 down)
- **Leverage**: 7x
- **Order Quantity**: $100 per grid
- **Net Profit**: +13.73 USDT (14%)
- **Win Rate**: 71.62%
- **PF**: 1.81

### BCH/USDT
- **Grid Spacing**: 0.3%
- **Number of Grids**: 100 (50 up + 50 down)
- **Leverage**: 5x
- **Order Quantity**: $100 per grid
- **Net Profit**: +12.92 USDT (13%)
- **Win Rate**: 74.60%
- **PF**: 2.14

---

## Market Regime Filter (Manual)

The backtest used ADX + Bollinger Bands to filter trending markets. On Binance:

1. **Check ADX**: Only enable grid when ADX < 40
2. **Check BB Width**: Only enable grid when BB width < 0.03

**Quick ADX Check**: https://www.tradingview.com/symbols/USDTOP/

---

## Summary Table

| Symbol | Spacing | Grids | Leverage | Profit | WR% |
|--------|---------|-------|----------|--------|-----|
| OP | 0.15% | 35 | 7x | +58% | 75% |
| NEAR | 0.4% | 50 | 3x | +22% | 65% |
| BTC | 0.15% | 100 | 10x | +20% | 77% |
| APT | 0.2% | 100 | 10x | +19% | 60% |
| ETH | 0.4% | 70 | 10x | +15% | 73% |
| ADA | 0.4% | 100 | 7x | +14% | 72% |
| BCH | 0.3% | 100 | 5x | +13% | 75% |

**Total Backtest Profit: +161 USDT (161%)**
