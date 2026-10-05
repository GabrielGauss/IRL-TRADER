# Volatility-targeted trend core: walk-forward (2026-10-05)

**Verdict: passes on BTCUSDT, fails on ETHUSDT. The live core trades BTC only.**

Phase 3 found no timing edge over buy and hold. This core has a different goal, set before it was run: keep most of buy and hold's upside while cutting its drawdowns. It passes only if its out-of-sample Sharpe is at least buy and hold's **and** its max drawdown is at most 75% of buy and hold's, after costs.

## Rule

Target weight = trend_on × min(1, target_vol / realized_vol), long only, spot, no leverage.

- trend_on: close above its N-day SMA (N = 0 disables the filter, giving pure volatility targeting).
- realized_vol: annualized standard deviation of daily log returns over M days.
- The weight decided at a close is traded at the next open. Rebalances smaller than 5% of equity are skipped. Fees are 10 bps and slippage 5 bps per side.

The grid has 24 sets: N ∈ {0, 50, 100, 200}, M ∈ {20, 60}, target_vol ∈ {40%, 60%, 80%}. Each fold picks parameters by Sharpe on the previous 730 days, then trades the next 182 days, which it never saw. Judgement is on the stitched out-of-sample equity curve, so max drawdown spans the whole period rather than being averaged per fold.

## Results (daily bars, 2018-01 to 2026-10, 13 folds, out of sample from 2020-01)

| | Return | Sharpe | Max drawdown | Avg exposure | Rebalances | Fees (on 1,000) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| **BTC strategy** | +610.8% | **1.03** | **41.6%** | 53.7% | 154 | 78.24 |
| BTC buy & hold | +749.4% | 0.85 | 76.0% | 99.5% | 13 | 13.00 |
| ETH strategy | +419.2% | 0.81 | 59.5% | 48.3% | 225 | 61.09 |
| ETH buy & hold | +1142.4% | 0.89 | 78.4% | 99.5% | 13 | 13.00 |

## Reading

- **BTC:** better risk-adjusted return than holding (Sharpe 1.03 vs 0.85) with a worst drawdown of 42% instead of 76%. It gives up some raw return, as designed. In the 2022 bear market (folds 5–6) it lost 17% and 9% while BTC lost 56% and 17%.
- **ETH:** fails on both counts. Its drawdown ratio (76%) is just over the 75% bar, and it gives up too much return. It is not traded.
- **Caveats:**
  - The selected parameters change between folds, so part of the result depends on the selection procedure itself.
  - Two assets are a small sample.
  - Thirteen 6-month windows are not many independent periods.
  
  This is a risk-managed way to hold BTC, not an edge. The live agent uses exactly the tested procedure (re-select on the trailing 730 days) and starts on paper.

Reproduce:

```
trading-bot walkforward --symbol BTCUSDT --interval 1d --start 2018-01-01 --end 2026-10-01 \
  --train-bars 730 --test-bars 182 --strategy vol_target
```
