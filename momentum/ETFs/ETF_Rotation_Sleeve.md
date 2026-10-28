# ETF Rotation Sleeve — monthly dual momentum (paper trading)

> **Module:** `etf_rotation_sleeve.py` · **Dashboard tab:** *Rotation Sleeve (paper)* in `etf_flask/` · **Tests:** `tests/test_rotation_sleeve.py`
> **State:** `rotation_paper_log.json` (own file) · **Optional config:** `rotation_sleeve_config.json`
> **Status:** paper trading only. It never places orders and never reads or writes the live tradelog, positions ledger, `holdings_log.json` or `paper_trade_log.json`.

A small monthly strategy that runs *next to* the live weekly ETF strategy (`ETF_Momentum_Strategy - v2.md`). The live strategy and its scripts are unchanged.

## Rules (frozen — these are the backtested rules)

| Item | Rule |
|:---|:---|
| Universe | NIFTYBEES, JUNIORBEES, BANKBEES, MOM100, CPSEETF, HNGSNGBEES, GOLDBEES (distinct asset classes) |
| Signal date | Last trading day of the most recent **completed** month in the price data |
| Relative momentum | Rank by 12-month return (252 trading days); take the top 3 |
| Absolute momentum | A pick is held only if its 12-month return beats cash (**assumed** 5.5% a year); otherwise its slot stays in cash |
| Weights | Equal, 1/3 per slot (0, 33%, 67% or 100% invested) |
| Rebalance | Once per month; trades to target at the latest close |
| Costs | 20 bps one-way slippage per fill plus Rs 20 per trade leg; drift trades under 0.5% of equity are skipped |
| Cash | Earns the assumed 5.5% a year, compounded on calendar days |
| Start capital | Rs 10,00,000 (paper) |

### Data hygiene
An ETF is **not scored** (and so never bought) if it is missing from the data, has fewer than 253 rows up to the signal date, has a gap in the window, a non-positive price, or any single-day |log return| above 1.2 (a vendor glitch). The dashboard lists the reason; the slot stays in cash.

`ETF_updated.xlsx` holds about 13 months of rows, so the 12-month lookback has little spare history. If the file ever shrinks below 253 rows before the signal date, every ETF shows *SHORT HISTORY* and the sleeve refuses to update rather than guess.

## How it is used

- **Dashboard:** open the *Rotation Sleeve (paper)* tab. It shows the signal, the paper holdings, the actions the next update would take, performance, and the controls (auto-update switch, update now, reset).
- **Auto-update:** with the switch on, *Run Weekly Rebalance* also updates the sleeve. It trades only once a month (the first update after a month-end); any other update just marks the portfolio to market.
- **Command line:**
  ```bash
  python etf_rotation_sleeve.py            # show the signal and pending trades
  python etf_rotation_sleeve.py --update   # apply this month's rebalance
  python etf_rotation_sleeve.py --reset    # archive the history and restart from cash
  ```
- **First start:** the first update initialises the portfolio from the latest month-end signal. If that signal is more than 7 days old the tab warns that fills happen at today's close; waiting for the first update after the next month-end gives a fresh signal.
- **Reset** archives the log to `rotation_paper_log.reset_<timestamp>.json`; nothing is deleted.

## Backtest evidence (and its limits)

From `backtest/etf_backtest_dual_momentum.py`, Oct 2017 → Sep 2026, these 7 ETFs, Rs 20 per leg, cash 5.5%:

| | CAGR | Max drawdown | Volatility |
|:---|:---:|:---:|:---:|
| This sleeve (top 3, 12M, absolute test) | 14.5% | −17.5% | 13.8% |
| Same, 20 bps one-way costs | 13.6% | −17.6% | |
| Equal weight, same ETFs, monthly | 13.8% | −29.9% | 12.8% |
| Live ETF strategy (cash 5.5%) | 14.4% | −21.3% | 14.0% |

Monthly-return correlation with the live strategy: **0.70**. A 70/30 live/sleeve blend cut the monthly max drawdown from about −20% to −14% at similar CAGR.

**Caveats**
- The ETFs were chosen with hindsight from what exists today.
- One 9-year in-sample test; about 25 variants were run across the dual-momentum study.
- The advantage over equal weight is mostly **lower drawdown**: excluding 2025 (a gold and Hang Seng year) the sleeve's CAGR was 12.1% against 12.3% for equal weight.
- The cash yield (5.5%) is an assumption; there is no usable cash history in the data.
- Taxes on switching, ETF liquidity and live execution are not modelled.
- Keep the rules fixed while paper trading; re-tuning on the same sample would defeat the purpose.

## Verification
`tests/test_rotation_sleeve.py` (14 tests) covers the signal, the cash hurdle, no look-ahead, bad-data handling, costs, idempotent monthly updates, month-to-month rotation, stale-data refusal, read-only preview and reset. One test replays **every month-end since 2017** on the real history file and checks that the sleeve's holdings and weights equal those produced by the backtest (`gem_schedule`).

```bash
python -m pytest tests/test_rotation_sleeve.py -q
```
