# Capital Allocation Analysis — Three Trading Strategies

**Corpus:** ₹30,00,000
**Date of analysis:** 2026-09-17
**Disclaimer:** Not financial advice. Based entirely on the user's own backtests/live logs, not independently verified.

## Strategies Analyzed

1. **Sharpe (N750)** — Indian equity momentum strategy, ~750-stock universe. `C:\Users\ameet\Documents\Github\code\momentum\Sharpe\`
2. **ETF Momentum Rotation** — rotates a basket of Indian index ETFs. `C:\Users\ameet\Documents\Github\code\momentum\ETFs\`
3. **Clenow Momentum + Supertrend** — individual-stock momentum with a Supertrend overlay. `C:\Users\ameet\Documents\Github\ClenowMomentumSupertrend\`

## Performance Summary (rf = 6%)

| Strategy | Source | Period | CAGR | Ann. Vol | Max DD | Sharpe | Sortino |
|---|---|---|---|---|---|---|---|
| Sharpe (N750) | 9-yr weekly backtest, net of ~0.2%/trade cost | 2017–2026 | 45.2% | 18.3% | −16.6% | 1.82 | 2.45 |
| Clenow Momentum + Supertrend | 9.6-yr monthly backtest, net of 0.05%/leg commission | 2017–2026 | 40.2% | 28.8% | −30.2% | 1.13 | 2.01 |
| ETF Momentum Rotation | 10.3-yr daily backtest, **no transaction-cost model found** | 2016–2026 | 6.5% | 10.2% | −24.6% | 0.10 | 0.12 |
| *(ETF benchmark, Nifty500-like, for reference)* | — | same | 11.5% | 15.8% | −38.3% | 0.40 | 0.47 |

**Key flag:** the ETF strategy's own backtest underperforms its benchmark and has no visible cost model in the script despite frequent rebalancing — weak evidence of real edge.

## Correlation Matrix (monthly returns, 108-month overlap, 2017-09 to 2026-08)

| | Sharpe_N750 | ETF | Clenow |
|---|---|---|---|
| **Sharpe_N750** | 1.00 | 0.30 | 0.61 |
| **ETF** | 0.30 | 1.00 | 0.37 |
| **Clenow** | 0.61 | 0.37 | 1.00 |

Sharpe and Clenow move together (both single-stock momentum); ETF is the only real diversifier, but its edge is unproven.

## Recommended Allocation

Pure mean-variance optimization on these numbers pushes 100% into Sharpe (N750) alone — mathematically "optimal" but a poor real-world choice given single-strategy concentration and backtest-overfitting risk. Applying practical guardrails (cap any single strategy near 55%, keep a floor on the diversifier, discount the strategy with no cost model):

| Strategy | Weight | Capital (₹) |
|---|---|---|
| Sharpe (N750) | 55% | 16,50,000 |
| Clenow Momentum + Supertrend | 30% | 9,00,000 |
| ETF Momentum Rotation | 15% | 4,50,000 |

**Blended portfolio estimate:** ~37.9% CAGR, 17.4% annualized vol, Sharpe ≈ 1.83 — matches or slightly beats a 100%-into-Sharpe bet while keeping any single strategy's risk contribution below ~55%.

## Guardrails

- **Rebalance** back to target weights quarterly, or whenever any sleeve drifts more than ~10 percentage points from target.
- **Drawdown circuit breakers:** cut a sleeve to half-size if its drawdown exceeds ~1.3× its historical max DD (≈22% for Sharpe, ≈40% for Clenow, ≈32% for ETF), and reassess before restoring full size.
- **ETF sleeve:** before scaling beyond 15%, rerun its backtest with realistic transaction costs and confirm it still beats its own benchmark. If it doesn't, redirect that capital to Sharpe/Clenow or hold it as a diversification placeholder.
- **Concentration view:** Sharpe and Clenow are correlated (0.61) and both trade individual Indian equities — treat them as one combined "single-stock momentum" bucket (85% of corpus) for concentration-risk purposes, not two independent bets.

## Data Caveats

1. Comparability is imperfect: different history lengths, universes (individual stocks vs. ETFs), and rebalancing frequencies (weekly/monthly/daily).
2. Sharpe's live N750 ledger only covers 67 trading days since 2026-06-23 — too short to be meaningful, so the 9-year backtest was used instead.
3. Survivorship bias in the stock-universe backtests (delisted/renamed names) was not independently verified.
4. Multiple near-duplicate ETF backtest configs exist (v2, blend, 3wstrict, amfi); the one matching the most recently modified live config file was used.
