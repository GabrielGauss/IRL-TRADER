# Volatility-targeted trend core: walk-forward (2026-10-05)

**Verdict: passes on BTCUSDT, fails on ETHUSDT. The live core trades BTC only.**

**Update 2026-10-08:** a bootstrap ([below](#bootstrap-2026-10-08)) shows the BTC Sharpe gap is not distinguishable from zero, and the drawdown gap misses the 75% bar in about a quarter to a third of resamples. The pass stands as a point estimate; the evidence behind it is weak.

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

- **BTC:** a higher Sharpe than holding (1.03 vs 0.85) with a worst drawdown of 41.6% instead of 76.0%. It gives up some raw return, as designed. In the 2022 bear market (folds 5–6) it lost 16.6% and 8.9% while BTC lost 56.4% and 17.1%. The Sharpe gap is not statistically meaningful (see the bootstrap). The drawdown protection depends on the selected parameters: in fold 13 the selection dropped the trend filter, exposure was 98%, and the strategy fell 28.1% with BTC's 28.6%.
- **ETH:** fails on both counts. Its drawdown ratio (75.9%) is just over the 75% bar, and its Sharpe is lower (0.81 vs 0.89). It is not traded.
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

## Bootstrap (2026-10-08)

Prompted by a reader's question: with only a few market regimes out of sample, how much of the BTC result could be luck?

Method: a paired circular block bootstrap of the stitched out-of-sample daily returns (2,366 days, 2020-01-01 to 2026-06-23). Strategy and buy-and-hold returns are resampled on the same days, so their correlation is kept. There are 10,000 resamples per block length, with a fixed seed. Intervals are 95% percentile intervals. "P(>bar)" is the share of resamples whose drawdown ratio is above the pre-registered 0.75.

| Asset | Block (days) | Sharpe diff | 95% CI | P(diff ≤ 0) | DD ratio | 95% CI | P(>bar) |
| --- | ---: | ---: | --- | ---: | ---: | --- | ---: |
| BTC | 5 | +0.18 | −0.40 to +0.77 | 28.1% | 0.55 | 0.40 to 0.98 | 23.5% |
| BTC | 21 | +0.18 | −0.38 to +0.74 | 28.5% | 0.55 | 0.43 to 1.01 | 31.6% |
| BTC | 63 | +0.18 | −0.38 to +0.73 | 28.8% | 0.55 | 0.41 to 0.99 | 26.5% |
| ETH | 5 | −0.09 | −0.60 to +0.41 | 63.6% | 0.76 | 0.47 to 0.96 | 32.6% |
| ETH | 21 | −0.09 | −0.57 to +0.42 | 64.6% | 0.76 | 0.47 to 0.93 | 30.8% |
| ETH | 63 | −0.09 | −0.56 to +0.42 | 64.1% | 0.76 | 0.46 to 0.95 | 35.5% |

Reading:

- **Sharpe:** the BTC interval spans zero at every block length, and about 28% of resamples show no improvement. The 1.03 vs 0.85 gap is not evidence of better risk-adjusted return.
- **Drawdown:** more robust. The interval's upper end is about 1.0, so the strategy's worst drawdown is rarely deeper than holding's. But the pre-registered "at most 75% of buy and hold's" bar fails in roughly a quarter to a third of resamples.
- **Underestimate:** the bootstrap resamples days only. It does not include the uncertainty of re-selecting parameters on each fold, which fold 13 shows matters. The true uncertainty is larger.
- **Lesson for the gate:** a pre-registered test on point estimates alone was too weak. Future gates should require an interval, not just a point estimate, to clear the bar.

Conclusion: a calmer way to hold BTC, with weak evidence even for that. Not an edge.

Reproduce:

```
trading-bot walkforward --symbol BTCUSDT --interval 1d --start 2018-01-01 --end 2026-10-01   --train-bars 730 --test-bars 182 --strategy vol_target --bootstrap-blocks 5,21,63
```
