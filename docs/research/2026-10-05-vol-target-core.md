# Volatility-targeted trend core: walk-forward (2026-10-05)

**Verdict: passes on BTCUSDT, fails on ETHUSDT. The live core trades BTC only.**

**Update 2026-10-08:** a bootstrap ([below](#bootstrap-2026-10-08)) shows the BTC Sharpe gap is not distinguishable from zero, and the drawdown gap misses the 75% bar in about a quarter to a third of resamples. The pass stands as a point estimate; the evidence behind it is weak.

**Update 2026-11-16:** with the trend filter fixed instead of selected ([below](#fixed-trend-filter-run-2026-11-16)), the BTC drawdown result holds as a point estimate (43.6% vs 76.0%), but it fails the 75% bar in about a quarter of resamples. The drawdown cut needs **both** components: on BTC, volatility targeting alone (71.8%) and the SMA 200 filter alone (64.3%) each fail the bar; only the combination passes. ETH shows the same pattern (this does not change the ETH verdict). SMA 200 was not pre-registered, and 10 variants were tried with no multiple-testing correction; see that section.

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

## Fixed trend filter (run 2026-11-16)

Prompted by a second reader: in fold 13 the walk-forward selected no trend filter and fell with BTC, so is the protection coming from the selection step? This test removes the selection step for the trend filter. `trend_period` is fixed for every fold, and only `vol_period` and `target_vol` are still selected in-sample.

**Provenance of SMA 200 (not pre-registered).** SMA 200 is the headline variant because it is the default of `VolTargetTrendStrategy` (`trend_period=200` in `src/trading_bot/strategy/vol_target.py`, already in the code before this test) and the textbook choice. That makes it a credible a priori choice, but it was not pre-registered: it was picked after the original walk-forward and its fold-by-fold results had been seen, and no record of the choice predates the results. These runs and this section were done and committed on 2026-11-16 (commit 3a11af1). An earlier version of this heading said 2026-10-09, which was wrong.

Same data, folds, costs and bootstrap as above. The table shows 21-day blocks. Point estimates do not depend on the block length. Across 5-, 21- and 63-day blocks, interval ends move by up to 0.09 for the Sharpe difference and 0.03 for the drawdown ratio. P(>bar) moves more: by up to 5.7 percentage points for the rows with vol targeting, and 10.8 points for SMA 200 alone. For example, SMA 200 with vol targeting gives 21.9%, 26.3% and 27.6%. Read P(>bar) as a range, not a precise number.

| Trend filter | Vol targeting | OOS Sharpe (B&H 0.85) | Max DD (B&H 76.0%) | Return (B&H 749%) | Verdict | Sharpe diff 95% CI | P(diff ≤ 0) | DD ratio 95% CI | P(>bar) |
| --- | --- | ---: | ---: | ---: | --- | --- | ---: | --- | ---: |
| **SMA 200** | **yes** | **0.93** | **43.6%** | **434%** | **passes** | **−0.51 to +0.65** | **41.1%** | **0.41 to 0.98** | **26.3%** |
| SMA 200 | no (weight 1 above the SMA) | 0.84 | 64.3% | 471% | **fails** | −0.60 to +0.56 | 52.6% | 0.53 to 1.21 | 69.3% |
| SMA 100 | yes | 1.06 | 29.1% | 534% | passes | −0.39 to +0.78 | 25.8% | 0.37 to 0.91 | 13.4% |
| SMA 50 | yes | 1.28 | 43.5% | 831% | passes | −0.17 to +0.99 | 8.4% | 0.32 to 0.82 | 6.1% |
| None | yes (pure vol targeting) | 0.72 | 71.8% | 343% | **fails** | −0.35 to +0.08 | 89.4% | 0.78 to 1.08 | 99.1% |

SMA 200 alone, BTC, by block length (5 / 21 / 63 days): Sharpe diff −0.02 at all three; DD ratio 95% CI 0.51 to 1.19 / 0.53 to 1.21 / 0.52 to 1.18; P(>bar) 61.5% / 69.3% / 72.3%.

### Decomposition: which component cuts the drawdown?

Max drawdown of the stitched out-of-sample curve, with the drawdown ratio to buy and hold in brackets. "Trend filter" is SMA 200, fixed.

| Asset | Buy & hold | Vol targeting only | Trend filter only | Both |
| --- | ---: | ---: | ---: | ---: |
| BTC | 76.0% | 71.8% (0.94), fails | 64.3% (0.85), fails | 43.6% (0.57), passes |
| ETH | 78.4% | 62.9% (0.80), fails | 67.1% (0.86), fails | 35.1% (0.45), passes |

ETH bootstrap, 21-day blocks, P(>bar): vol targeting only 76.3%, trend filter only 79.8%, both 7.2% (DD ratio 95% CI 0.37 to 0.81).

Reading:

- **The drawdown cut needs both components.** Neither alone reaches the 75% bar on either asset, and each alone misses it in most resamples (BTC: 99.1% and 69.3%). Together they pass as a point estimate on both. The combined cut is larger than the two single-component cuts added up. A plausible reading is that the filter keeps the strategy out of long declines while volatility targeting shrinks exposure in the turbulent stretches the filter is still in, but that mechanism has not been tested.
- **The selection step was not the source.** With SMA 200 fixed, the BTC drawdown ratio is 0.57, close to the selected version's 0.55. In fold 13 the fixed SMA 200 held nothing while BTC fell 28.6%.
- **The pass is weak.** The BTC SMA 200 + vol targeting drawdown ratio misses the 0.75 bar in 21.9% to 27.6% of resamples, depending on block length. The pass is a point estimate, not an interval that clears the bar.
- **Sharpe:** with SMA 200 the gap shrinks to +0.08 and its interval is centered near zero. There is no evidence of better risk-adjusted return.
- **Shorter filters look better, but that is not a finding.** SMA 50 has the best numbers, but it was picked after looking at all four. With several variants tried, the best one is expected to look good. It would need its own out-of-sample period to count.
- **ETH with SMA 200 fixed passes as a point estimate.** This does not change the ETH verdict above. The pre-registered ETH test was the selected walk-forward, which failed; the fixed-filter ETH run came after that failure was known, and the live core still trades BTC only.
- **Correction to earlier wording:** an earlier version of this section (commit 3a11af1) said that "the drawdown protection comes from the trend filter, not from volatility targeting" and that "volatility targeting mainly scales the position size". That was not supported: SMA 200 without volatility targeting had not been run, and when it was, it failed. Wording that credits the drawdown gap to volatility targeting alone, or to the trend filter alone, is wrong. The supported statement is that the combination does the work.

### Variants tried and multiple testing

On the same 2020-01 to 2026-06 out-of-sample period, these were evaluated and looked at:

- Selected walk-forward over the 24-set grid: BTC and ETH (2).
- Fixed trend filter with vol targeting, BTC: SMA 200, 100, 50 and none (4), each still selecting `vol_period` and `target_vol` in-sample.
- Fixed SMA 200 with vol targeting, ETH (1); pure vol targeting, ETH (1).
- SMA 200 without vol targeting: BTC and ETH (2).
- Cost stress (fees and slippage doubled) on BTC SMA 200 and on BTC without a filter (2), run by the validator. No numbers from it are quoted here.

That is 10 strategy-asset variants plus 2 cost-stress reruns. No correction for multiple testing has been applied to any interval or probability in this document. With this many looks at one out-of-sample period, a single passing variant is weaker evidence than its own interval suggests.

### Bootstrap method notes

- The bootstrap is a **circular block bootstrap with a fixed block length** (5, 21 or 63 days), not a stationary bootstrap with random block lengths. Strategy and buy-and-hold days are resampled together.
- **Max drawdown is path-dependent.** Resampling breaks drawdown episodes longer than the block into pieces and reassembles them in a new order, so the bootstrap distribution of max drawdown, and of the drawdown ratio, is an approximation. That is why it is reported at three block lengths. None of them keeps a multi-month bear market intact.
- The seed is fixed (`DEFAULT_SEED = 20261008` in `src/trading_bot/backtest/bootstrap.py`), with 10,000 resamples per block length. Only days are resampled, not the parameter re-selection.

Conclusion: the BTC drawdown result survives fixing the trend filter, as a weak point estimate, and it comes from the combination of the 200-day filter and volatility sizing. Neither component passes alone. Holding BTC above its 200-day average, sized by volatility, cut the worst out-of-sample drawdown from 76.0% to 43.6%, at the cost of 42% of the total return (434% vs 749%), and that cut misses the bar in roughly a quarter of resamples. The Sharpe gap is not distinguishable from zero.

Reproduce (one run per row; the seed is the default):

```
# Trend filter with vol targeting (BTC rows: trend_period = 200, 100, 50, 0)
trading-bot walkforward --symbol BTCUSDT --interval 1d --start 2018-01-01 --end 2026-10-01   --train-bars 730 --test-bars 182 --strategy vol_target --bootstrap-blocks 5,21,63 --param trend_period=200

# Trend filter only: target_vol=100 (10,000%) makes the vol weight min(1, target/realized) equal to 1
trading-bot walkforward --symbol BTCUSDT --interval 1d --start 2018-01-01 --end 2026-10-01   --train-bars 730 --test-bars 182 --strategy vol_target --bootstrap-blocks 5,21,63 --param trend_period=200 --param target_vol=100

# ETH rows: the same commands with --symbol ETHUSDT and trend_period=200; trend_period=0; trend_period=200 target_vol=100
```

Data vintage: Binance spot daily klines cached on 2026-10-05. `data/klines/BTCUSDT_1d_2018-01-01_2026-10-01.csv` has sha256 `af5ccafb6735afe44c4aa21fb9f5c7590a2546a97d3b87b128d3eb3998b32347`, and `data/klines/ETHUSDT_1d_2018-01-01_2026-10-01.csv` has sha256 `334e2e385a42534beafbcde9ab13b9f3f1d6c5c02c27fb7f716297c387dde188`. The cache is gitignored, so a fresh download may differ if the exchange revises history.
