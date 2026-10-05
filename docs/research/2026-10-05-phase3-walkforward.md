# Phase 3 research: walk-forward of four long-only strategies (2026-10-05)

**Verdict: no candidate has an out-of-sample edge. None goes to paper trading.**

## Setup

- Data: Binance spot BTCUSDT and ETHUSDT, 2022-01-01 to 2026-10-01 (bear, recovery and bull).
- Walk-forward: 37 folds. Each fold has about 5.5 months of training (1h: 4000 bars, 4h: 1000) and about 6 weeks of test (1h: 1000, 4h: 250), so both intervals cover the same calendar windows.
- Parameters are chosen by in-sample Sharpe on each training window and judged only on the next unseen window.
- Costs: next-bar-open fills, 10 bps taker fee and 5 bps slippage per side, full position size. Buy and hold pays the same costs over the same test windows.
- Gate (set before the runs): at least 30 OOS trades, and the OOS result beats buy and hold after costs.

Reproduce with, for example:

```
trading-bot walkforward --symbol BTCUSDT --interval 4h --start 2022-01-01 --end 2026-10-01 \
  --train-bars 1000 --test-bars 250 --strategy momentum
```

## Strategies

| Name | Rule | Grid |
| --- | --- | --- |
| `ema_rsi` | Uptrend (fast EMA > slow EMA) and RSI recovering from oversold; exit when RSI is overbought | 54 sets |
| `donchian` | Breakout above the N-bar high (optional SMA trend filter); exit below the M-bar low | 14 sets |
| `momentum` | Long while the N-bar return is above +band; exit below -band | 12 sets |
| `mean_reversion` | RSI(2–3) dip while above the trend SMA; exit when RSI is overbought or the trend breaks | 36 sets |

## Results

Sharpe values are means across folds. Returns are compounded across the test windows.

| Symbol | Interval | Strategy | IS Sharpe | OOS Sharpe | B&H Sharpe | OOS return | B&H return | OOS trades | Exposure |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| BTCUSDT | 1h | donchian | 0.81 | -0.70 | 1.01 | -35.2% | +238.2% | 183 | 37% |
| BTCUSDT | 1h | ema_rsi | 2.01 | 0.42 | 1.01 | +13.0% | +238.2% | 16 | 7% |
| BTCUSDT | 1h | mean_reversion | 0.23 | -0.87 | 1.01 | -26.2% | +238.2% | 169 | 4% |
| BTCUSDT | 1h | momentum | 1.23 | 0.07 | 1.01 | +31.3% | +238.2% | 82 | 53% |
| BTCUSDT | 4h | donchian | 1.61 | -0.37 | 1.01 | +9.3% | +238.5% | 60 | 34% |
| BTCUSDT | 4h | ema_rsi | 1.15 | 0.37 | 1.01 | +34.5% | +238.5% | 8 | 10% |
| BTCUSDT | 4h | mean_reversion | 1.15 | -1.17 | 1.01 | -32.3% | +238.5% | 106 | 6% |
| BTCUSDT | 4h | momentum | 1.51 | 0.37 | 1.01 | +52.2% | +238.5% | 67 | 59% |
| ETHUSDT | 1h | donchian | 1.05 | -0.09 | 0.72 | +7.1% | +97.3% | 190 | 34% |
| ETHUSDT | 1h | ema_rsi | 1.39 | -0.12 | 0.72 | -36.1% | +97.3% | 18 | 10% |
| ETHUSDT | 1h | mean_reversion | 0.47 | -1.56 | 0.72 | -53.1% | +97.3% | 112 | 3% |
| ETHUSDT | 1h | momentum | 1.02 | -0.56 | 0.72 | -47.0% | +97.3% | 149 | 51% |
| ETHUSDT | 4h | donchian | 1.30 | -0.20 | 0.68 | +22.1% | +97.3% | 59 | 32% |
| ETHUSDT | 4h | ema_rsi | 1.23 | 0.07 | 0.68 | +5.7% | +97.3% | 4 | 3% |
| ETHUSDT | 4h | mean_reversion | 1.35 | -0.76 | 0.68 | -42.1% | +97.3% | 64 | 5% |
| ETHUSDT | 4h | momentum | 1.43 | -0.07 | 0.68 | +21.0% | +97.3% | 53 | 54% |

## Reading

- **In-sample Sharpe collapses out of sample everywhere** (typically from 1.0–2.0 to between -1.5 and 0.4). The grid search finds parameters that fit each training window, and the fit doesn't carry forward. That is the overfitting signature walk-forward exists to expose.
- **Not even risk-adjusted:** the best OOS Sharpe (0.42 for BTC 1h ema_rsi, on just 16 trades) is below buy and hold's 1.01. A lower return alone could be forgiven if the strategy took less risk; that is not what happened here.
- **Mean reversion is consistently the worst.** On 1h and 4h crypto, sharp dips inside an uptrend kept going lower often enough to dominate.
- **Momentum on 4h is the least bad** (BTC OOS Sharpe 0.37 at 59% exposure). It is still well below buy and hold, and it is negative on ETH.
- **Multiple-testing caveat:** 16 runs were made here. Even a pass in one cell would need to hold on the other symbol and interval before it is trusted.

## Not yet tested

- Daily bars with long lookbacks (20–200 days). This is where the published time-series-momentum evidence lies, but over 4.75 years it yields few trades.
- Volatility-targeted position sizing. The engine is currently all-in or flat.
- A regime filter from MacroPulse's historical regime series.
- Cross-sectional momentum (rotating among the top-N coins), which has more published support in crypto than single-asset technical rules.
