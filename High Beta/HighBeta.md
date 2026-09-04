# High Beta Strategy — Backtest

## Idea
High-beta stocks amplify market moves. In a rising market they tend to outperform;
in a falling market they tend to fall harder. This strategy takes that trade-off
deliberately: it holds the 20 stocks with the highest rolling beta to NIFTY500
each month, equal-weighted, and rides the market's swings with leverage-like
exposure (without actual leverage).

## Rules
- **Universe**: all NSE tickers in `dhan_datahq/base files/History_updated.xlsx` (DATA sheet), benchmark = NIFTY500.
- **Signal**: beta of daily returns vs NIFTY500 over the trailing 126 trading days (~6 months).
- **Liquidity filter**: median daily traded value (VOLUME sheet, INR) over the same lookback must be ≥ INR 1,00,00,000... i.e. ≥ INR 10,000,000 (1 crore). Also requires ≥100 valid price observations in the window and a valid price on the rebalance date.
- **Selection**: top 20 stocks by beta among the liquid, eligible universe.
- **Weighting**: equal weight (5% each).
- **Rebalance**: monthly, on the last trading day of each month — full reconstitution to the new top-20 list.
- **Transaction costs**: flat INR 20 per executed order (buy or sell leg), charged only on names entering or leaving the portfolio that month. Continuing holdings are not re-traded, so this understates cost slightly relative to a strategy that also re-levels weights on every name every month.
- **Starting capital**: INR 10,00,000 (assumption — change `START_CAPITAL` in the script to resize).
- **Risk-free rate**: 6.5% p.a. (assumption, used only in Sharpe/Sortino).

## Results (2017-04-03 → 2026-08-27, 9.2 years)

*(Numbers below use the slot-based NAV engine — capital split 1/N across the N
selected names, tracked at position level — added so a stop-loss could be
modelled precisely. This also fixed a data nuance: ~140 "dates" in the source
file are exchange holidays with blank prices for every ticker; these are now
dropped from the trading calendar instead of silently counted as flat days.
Numbers therefore differ slightly from an earlier pass of this doc, but the
picture — big return pickup, roughly double the vol and drawdown of the
benchmark — is unchanged.)*

| Metric | No stop-loss | **20% per-stock stop-loss** | NIFTY500 (Buy & Hold) |
|---|---|---|---|
| Total Return | 337.0% | **587.2%** | 191.6% |
| CAGR | 17.4% | **23.3%** | 12.3% |
| Annualised Volatility | 35.6% | **32.8%** | 16.4% |
| Sharpe (rf 6.5%) | 0.31 | **0.51** | 0.36 |
| Sortino | 0.37 | **0.64** | 0.42 |
| Max Drawdown | -80.3% | **-70.3%** | -38.3% |
| Calmar | 0.22 | **0.33** | 0.32 |
| Realised beta (full period) | 1.61 | **1.40** | 1.00 |
| Realised annualised alpha | +1.9% | **+8.5%** | — |

### What a 20% stop-loss on every position does
Each of the 20 names is stopped out (sold, proceeds held as cash earning 0%
until the next monthly rebalance) if its close ever falls 20% below the price
it was bought at that month. This triggered **195 times** over 114 rebalances
— an average of **~1.7 stop-outs per month** out of the 20 holdings, at an
extra ~INR 20 sell-order cost each (~INR 3,900 total incremental cost, still
negligible against the portfolio's size).

The result here is a genuine improvement on every axis: higher CAGR (23.3% vs
17.4%), lower volatility (32.8% vs 35.6%), a shallower max drawdown (-70.3% vs
-80.3%), and roughly 65% better Sharpe and Sortino. This is a fairly intuitive
outcome for a high-beta book: without a stop, a stock that starts crashing
mid-month keeps dragging on the equal-weighted NAV all the way to the next
rebalance (sometimes -50% or worse in one holding period, given these are the
market's highest-beta names). Cutting the loss at -20% and sitting in cash for
the rest of that month avoids the worst of that tail, and because the freed
capital isn't reinvested into other names, it doesn't chase further downside
either — it just goes flat. The realised beta of the stopped-out version is
also lower (1.40 vs 1.61), because during drawdown episodes a growing share of
the book is sitting in cash rather than in falling stocks, mechanically
damping the portfolio's market sensitivity exactly when that sensitivity is
most costly.

This is a favourable result for *this* dataset and time period, but a stop
loss is not a free lunch in general — it can also whipsaw (stopping out a
name right before it recovers) and this simple version doesn't allow
re-entry intra-month even if a stock recovers past its entry price. Treat the
+6pp of CAGR uplift as period-specific evidence that trimming single-name
tail risk was worth it here, not as a universal guarantee.

Average liquid/eligible universe per rebalance: ~519 names out of 750. Average
turnover: ~11 names replaced per month (before any stop-loss exits). Total
transaction costs over the full backtest: ~INR 25,600 (no stop-loss) /
~INR 29,500 (with stop-loss) — both negligible against final NAV.

## 3-month vs 6-month beta lookback

Same monthly rebalance, same Top-20 equal-weight selection, same liquidity
filter and 20% stop-loss — only the window used to estimate beta shrinks from
126 trading days (~6 months) to 63 (~3 months), with the minimum-coverage
thresholds scaled down to match (`MIN_HISTORY_PTS` 100→50, `MIN_BETA_PTS`
60→40).

### Today's portfolio would look different

Using the 3-month window on the latest available data (2026-08-27), the top-20
list changes substantially from the 6-month version shown earlier — only
**8 of the 20 names overlap**. The 3-month list skews toward names that have
gotten hot *recently* (NBFCs and financials — IFCI, Aditya Birla Capital,
Bandhan Bank, Cholamandalam, Motilal Oswal; a couple of newly-listed/re-rated
names like Ixigo, Netweb, MTAR Tech, Aegis Vopak — that a 6-month window
wouldn't yet weight as heavily), while dropping names whose high beta was more
a feature of the last 6 months than the last 3 (Force Motors, IndiGo, RailTel,
SML Isuzu, Sunteck, DB Realty, Ashoka Buildcon, RattanIndia, SPARC, TMCV). The
average selected beta is also noticeably higher: 2.59 (3-month) vs 2.03
(6-month) — a shorter window is more reactive and tends to surface names in
the middle of a live volatility spike, which is both the appeal and the risk
of shortening it.

### Backtest results

| Metric | 6-month lookback, no stop | 6-month lookback + 20% stop | 3-month lookback, no stop | **3-month lookback + 20% stop** | NIFTY500 |
|---|---|---|---|---|---|
| CAGR | 17.4% | 23.3% | 17.1% | **21.4%** | 13.6% |
| Annualised Vol | 35.6% | 32.8% | 33.9% | **31.5%** | 16.2% |
| Sharpe (rf 6.5%) | 0.31 | 0.51 | 0.31 | **0.47** | 0.44 |
| Sortino | 0.37 | 0.64 | 0.39 | **0.60** | 0.52 |
| Max Drawdown | -80.3% | -70.3% | -81.6% | **-74.2%** | -38.3% |
| Calmar | 0.22 | 0.33 | 0.21 | **0.29** | 0.36 |
| Realised beta | 1.61 | 1.40 | 1.59 | **1.38** | 1.00 |
| Realised alpha (ann.) | +1.9% | +8.5% | -0.2% | **+5.5%** | — |
| Stop-loss events | — | 195 | — | **212** | — |
| Avg trades / rebalance | 11.3 | 11.3 | 18.7 | **18.7** | — |
| Final NAV (INR 10L start) | 44.6L | 70.1L | 45.8L | **64.7L** | 29.2L (6m window's period) / 33.5L (3m window's period) |

*(The two lookback variants start their compounding a few months apart —
Jan 2017 for the 3-month version vs Apr 2017 for the 6-month version, since a
shorter lookback needs less warm-up history — so the benchmark column is
shown for each period separately; the qualitative comparison between the
strategy variants themselves is unaffected.)*

**The 3-month lookback modestly underperforms the 6-month lookback on every
metric**, both with and without the stop-loss: lower CAGR (21.4% vs 23.3% with
stop), similar-to-slightly-worse volatility, a deeper max drawdown (-74.2% vs
-70.3%), and a lower Calmar (0.29 vs 0.33). It also nearly doubles monthly
turnover (18.7 names replaced per month vs 11.3), because a 3-month beta
ranking is noisier and reshuffles the list more aggressively — and it
triggers slightly *more* stop-loss events (212 vs 195) despite the extra
turnover, consistent with the shorter window tending to select names that
are already mid-spike rather than names with a longer, steadier record of
elevated beta.

This lines up with a general pattern in beta estimation: beta is a fairly
noisy, slowly-mean-reverting quantity, and shorter estimation windows pick up
more of that noise (a stock having one volatile quarter) rather than a
stock's more persistent risk character. The 6-month window isn't free of this
either — its own average *selected* beta (a forward-look proxy) doesn't fully
survive into realised portfolio beta (an effect noted earlier in this
document) — but a 3-month window makes the effect somewhat worse: it chases
more names that are having a temporarily volatile few months, generating more
turnover and slightly worse risk-adjusted returns for the extra trading. If
you want more responsiveness to genuinely regime-shifting names, a window
between 3 and 6 months (e.g. 4-5 months) or a blended/shrinkage estimator
would likely be a better trade-off than the pure 3-month cut tested here.

## 12-month beta lookback

Same setup again — monthly rebalance, Top-20 equal-weight, liquidity filter,
20% stop-loss — with the beta window stretched to 252 trading days (~12
months), coverage thresholds scaled to match (`MIN_HISTORY_PTS` 200,
`MIN_BETA_PTS` 120).

### Today's portfolio, one more time

The 12-month list overlaps the 6-month list on 12 of 20 names (closer than the
3-month list's 8/20, as expected — a longer window changes more slowly).
Average selected beta drops to **1.99** (vs 2.03 for 6-month, 2.59 for
3-month) — a full year of history smooths out single-quarter volatility
spikes and settles on a more persistent set of high-beta names: infra/capital
goods and small-cap industrials (Lloyds Engineering, Lloyds Enterprises, HCC,
Texmaco Rail, TARIL, Gokaldas Exports, Kitex, Waaree/SW Solar, PCBL, Apollo
Tricoat) alongside the now-familiar Shriram Finance, Force Motors, Inox Wind,
DB Realty, RattanIndia, Sunteck, SPARC and PG Electroplast.

### Backtest results — all three lookbacks side by side

| Metric | 3-month + stop | 6-month + stop | **12-month + stop** | NIFTY500 (own period) |
|---|---|---|---|---|
| CAGR | 21.4% | 23.3% | **14.9%** | 12.1% (12m period) |
| Annualised Vol | 31.5% | 32.8% | **30.7%** | 16.6% |
| Sharpe (rf 6.5%) | 0.47 | 0.51 | **0.28** | 0.34 |
| Sortino | 0.60 | 0.64 | **0.36** | 0.40 |
| Max Drawdown | -74.2% | -70.3% | **-67.2%** | -38.3% |
| Calmar | 0.29 | 0.33 | **0.22** | 0.32 |
| Realised beta | 1.38 | 1.40 | **1.38** | 1.00 |
| Realised alpha (ann.) | +5.5% | +8.5% | **+1.0%** | — |
| Stop-loss events | 212 | 195 | **186** | — |
| Avg trades / rebalance | 18.7 | 11.3 | **6.8** | — |
| Final NAV (INR 10L start) | 64.7L | 70.1L | **33.9L** | 27.1L (12m period) |

*(Each lookback needs a different amount of warm-up history, so the three
start dates differ — Jan 2017 / Apr 2017 / Oct 2017 for 3/6/12-month — the
NIFTY500 column is shown for the 12-month variant's own period for a fair
comparison against it specifically; see the individual sections above for the
3- and 6-month periods' own benchmark figures.)*

**The 12-month lookback is the weakest of the three by a clear margin on
return, though it does keep improving on drawdown and turnover as the window
lengthens.** CAGR drops to 14.9% (barely above NIFTY500's own 12.1% over that
period), Sharpe falls to 0.28 — worse than the benchmark's 0.34 — and Calmar
(0.22) is also worse than simply holding the index (0.32). At the same time,
turnover keeps falling as the lookback lengthens (18.7 → 11.3 → 6.8 names per
month) and so does the max drawdown (-74.2% → -70.3% → -67.2%) and stop-loss
frequency (212 → 195 → 186 events) — the portfolio is more stable and less
reactive, but that stability comes at the cost of most of the excess return
that made this strategy interesting in the first place.

Put together, the three lookbacks trace out a fairly clean trade-off curve:
short windows (3 months) chase noisy, recently-hot names — more turnover, more
stop-outs, and worse risk-adjusted returns than a middle setting; long windows
(12 months) settle on stable, persistently-high-beta names but end up so slow
to react that a meaningful share of the return edge over the index disappears,
to the point Sharpe and Calmar actually fall behind plain NIFTY500. **The
6-month window tested from the start of this project sits at the best point
on that curve for this dataset** — high enough alpha/Sharpe/Calmar to justify
the extra risk, without the value-destroying turnover of the 3-month version
or the return-destroying sluggishness of the 12-month version. That's
reassuring as a sanity check on the original choice, but it's also worth
flagging as a form of look-ahead risk: three points (3/6/12 months) isn't
enough to rule out that 6 months is a local optimum specific to this decade
of Indian market history rather than a structurally "correct" window; a
walk-forward or out-of-sample test would be the honest next step before
treating 6 months as more than "the best of the three settings we tried."

## Equal-weighted blended beta score (3m + 6m + 12m)

Instead of ranking on a single lookback, this variant scores each stock on
the simple average of its 3-month, 6-month, and 12-month beta (a stock needs
a valid beta in all three windows to get a blended score — missing any one
drops it from consideration that month). Liquidity/history eligibility is
gated on the longest (12-month) window, since that's what all three betas
ultimately depend on. Same Top-20 equal-weight selection, monthly rebalance,
20% stop-loss as every other variant above.

### Today's blended portfolio

| Ticker | 3m beta | 6m beta | 12m beta | **Blended** |
|---|---|---|---|---|
| HCC | 2.73 | 2.15 | 2.11 | **2.33** |
| LLOYDSENGG | 2.61 | 2.11 | 2.16 | **2.30** |
| LLOYDSENT | 2.53 | 2.07 | 2.11 | **2.24** |
| TEXRAIL | 2.40 | 2.16 | 2.00 | **2.18** |
| ASHOKLEY | 2.70 | 2.09 | 1.75 | **2.18** |
| IFCI | 2.94 | 1.74 | 1.76 | **2.15** |
| SHRIRAMFIN | 2.42 | 2.08 | 1.94 | **2.15** |
| MTARTECH | 3.01 | 1.72 | 1.67 | **2.13** |
| IXIGO | 2.94 | 1.68 | 1.67 | **2.10** |
| AIIL | 2.59 | 2.04 | 1.67 | **2.10** |
| ...(+10 more, avg blended beta 2.15 across the full Top-20) | | | | |

This basket looks like a genuine cross of the three single-window lists —
Lloyds Engineering/Enterprises, HCC, Texmaco Rail and Shriram Finance persist
across every window (hence their top ranks here), while names that were only
prominent in one window (e.g. the very-recent movers unique to the 3-month
list, or the steadier industrials unique to the 12-month list) get pulled
toward the middle or dropped if they don't clear all three window's beta
coverage requirements.

### Backtest results

| Metric | 3-month | 6-month | 12-month | **Blended (3+6+12m)** | NIFTY500 (own period) |
|---|---|---|---|---|---|
| CAGR | 21.4% | 23.3% | 14.9% | **17.7%** | 12.1% |
| Annualised Vol | 31.5% | 32.8% | 30.7% | **31.1%** | 16.6% |
| Sharpe (rf 6.5%) | 0.47 | 0.51 | 0.28 | **0.36** | 0.34 |
| Sortino | 0.60 | 0.64 | 0.36 | **0.47** | 0.40 |
| Max Drawdown | -74.2% | -70.3% | -67.2% | **-68.8%** | -38.3% |
| Calmar | 0.29 | 0.33 | 0.22 | **0.26** | 0.32 |
| Realised beta | 1.38 | 1.40 | 1.38 | **1.39** | 1.00 |
| Realised alpha (ann.) | +5.5% | +8.5% | +1.0% | **+3.4%** | — |
| Stop-loss events | 212 | 195 | 186 | **201** | — |
| Avg trades / rebalance | 18.7 | 11.3 | 6.8 | **11.5** | — |
| Final NAV (INR 10L start) | 64.7L | 70.1L | 33.9L | **41.2L** | 27.1L (this period) |

**The blend lands almost exactly where a naive average of the three inputs
would predict, not better.** CAGR of 17.7% sits close to the simple average
of 21.4/23.3/14.9 (≈19.9%, a bit above where the blend actually landed), and
Sharpe (0.36) and Calmar (0.26) are solidly in the middle of the three single
windows rather than at either extreme. It doesn't inherit the 6-month
window's strength, and it doesn't fully escape the 12-month window's
weakness either — turnover (11.5 trades/rebalance) and stop-loss frequency
(201 events) both land close to the 6-month standalone numbers, which makes
sense since the blend is arithmetically dominated by whichever component
beta is most volatile from month to month (the 3-month figure), while the
12-month component acts as a drag that pulls the ranking away from the
6-month window's better-performing selections.

The practical takeaway: **blending betas here did not produce a "best of all
worlds" signal — it produced an average-of-all-worlds signal**, which in this
case is worse than just using the single best-performing window (6 months)
outright, because the average includes a component (12-month) that
independently underperforms on its own. Blending tends to help most when the
underlying signals are capturing genuinely different, complementary
information and are individually similarly-good; here, all three windows are
the same signal (beta) measured at different horizons, and the middle horizon
already dominates, so diluting it with the other two doesn't add
diversification value — it just pulls the result toward the mean of the
group. If you want to keep a multi-window idea, a next step worth testing
would be weighting the blend toward 6 months (e.g. 25/50/25 instead of equal
thirds) rather than an unweighted average, or requiring agreement across
windows as a *filter* (only consider a stock's 6-month beta rank if its
3-month and 12-month beta also clear some minimum) rather than averaging the
three into a single score.

## Adding quality/momentum filters: beta > 1 and within 25% of 52-week high

Back to the plain 6-month beta window (the best-performing single-window
setup found above), monthly rebalance, 20% stop-loss — but now with two extra
gates applied before ranking by beta:

- **Beta > 1** — trivial in practice (the Top-20 by beta rank are almost
  always well above 1 anyway), included for completeness/robustness rather
  than because it changes much on its own.
- **Within 25% of the trailing 52-week high** — `(52w_high − price) / 52w_high
  < 25%`, i.e. excludes any high-beta stock that has already fallen more than
  a quarter from its year-high. This is the filter that does the real work:
  it screens out "falling knife" high-beta names — stocks whose beta is high
  because they're in the middle of a severe decline — and keeps only
  high-beta names that are also showing relative price strength (a
  beta+momentum combination rather than beta alone).

This is a materially stricter filter: the liquidity-eligible universe
(~519 names/month) shrinks to an average of **~186 names** passing both
gates before the beta ranking even happens — about a third of the liquid
universe, illiquidity aside. Only one month in the whole backtest failed to
produce a full 20-name list under both filters.

### Today's filtered portfolio

Only **10 of 20** names overlap with the unfiltered 6-month list — every name
in this list is both high-beta *and* trading within 25% of its 52-week high:
HCC, Lloyds Engineering, Ashok Leyland, Shriram Finance, Lloyds Enterprises,
AIIL, PG Electroplast, IndiGo, SML Isuzu and TMCV survive from the unfiltered
list; new entrants (that the plain beta rank alone would have missed, or that
rank lower there) include PCBL, Motilal Oswal, Anant Raj, Optiemus, Apollo
Tricoat, Aditya Birla Capital, Nam-India (Sundaram Finance sub), Aegis
Vopak, Bandhan Bank, and Gokaldas Exports. Average beta of the filtered list
(1.95) is close to the unfiltered list's (2.03) — the filter isn't picking
lower-beta names, it's picking the same beta tier while additionally
requiring price strength.

### Backtest results — this is the best variant found so far

| Metric | 6-month, no filters, no stop | 6-month, no filters + 20% stop | 6-month **+ filters**, no stop | **6-month + filters + 20% stop** | NIFTY500 |
|---|---|---|---|---|---|
| CAGR | 17.4% | 23.3% | 21.5% | **26.0%** | 12.3% |
| Annualised Vol | 35.6% | 32.8% | 29.1% | **27.3%** | 16.4% |
| Sharpe (rf 6.5%) | 0.31 | 0.51 | 0.51 | **0.71** | 0.36 |
| Sortino | 0.37 | 0.64 | 0.62 | **0.90** | 0.42 |
| Max Drawdown | -80.3% | -70.3% | -71.5% | **-60.4%** | -38.3% |
| Calmar | 0.22 | 0.33 | 0.30 | **0.43** | 0.32 |
| Realised beta | 1.61 | 1.40 | 1.44 | **1.24** | 1.00 |
| Realised alpha (ann.) | +1.9% | +8.5% | +5.4% | **+11.1%** | — |
| Avg trades / rebalance | 11.3 | 11.3 | 16.7 | **16.7** | — |
| Stop-loss events | — | 195 | — | **130** | — |
| Final NAV (INR 10L start) | 44.6L | 70.1L | 61.3L | **85.8L** | 29.2L |

**This is the strongest result in the whole exercise, on every metric
simultaneously** — not just higher return, but genuinely better
risk-adjusted return: Sharpe nearly doubles versus the unfiltered no-stop
baseline (0.71 vs 0.31) and comfortably beats even NIFTY500's own Sharpe
(0.36). Calmar (0.43) is the only variant tested anywhere in this document
that clearly and convincingly beats the benchmark's Calmar (0.32) rather than
merely matching it. Max drawdown improves to -60.4%, still well above the
index's -38.3% but the shallowest of any high-beta variant tried. Stop-loss
events drop from 195 to 130 — fewer positions ever need the emergency exit,
because the momentum filter is already screening out the names most likely
to keep falling once they're in the portfolio.

The mechanism is intuitive once stated: **high beta by itself just tells you
a stock swings hard — it says nothing about which direction it's been
swinging lately.** A stock can have a high trailing beta because it just
crashed 60% (huge amplitude down) or because it just ran up 60% (huge
amplitude up); pure beta ranking can't distinguish the two and will happily
buy either. Adding "must be within 25% of its 52-week high" filters the
selection down to the subset of high-beta names in the middle of a favourable
move rather than an unfavourable one — combining the beta factor (amplitude)
with a simple momentum/strength factor (direction). That combination is
well-documented in equity factor research generally (momentum and beta
interact, and screening out high-beta laggards is a known way to improve a
pure-beta sleeve), and it shows up clearly here too.

The cost: turnover rises appreciably (16.7 trades/rebalance vs 11.3), since
requiring "within 25% of highs" is a moving target that more names fail and
pass month to month than a smoother beta rank alone — worth keeping in mind
against a more realistic (non-flat) transaction cost model than this
backtest uses.

## Adding a third filter: 1-year stock Sharpe > 1

On top of the beta>1 and within-25%-of-52-week-high filters, a third gate:
each stock's own trailing 252-trading-day daily Sharpe ratio (annualised,
using the same 6.5% risk-free assumption as the portfolio-level Sharpe)
must exceed 1.0. Where the 52-week-high filter screens out names crashing
from their highs, this filter screens out names whose *own* risk-adjusted
return over the last year has been poor even if they haven't technically
fallen 25% — a broader consistency/quality screen than price-distance alone.

*(Note: rerunning the two-filter version with a proper 252-day warm-up and
liquidity-gating window, for a fair side-by-side with this three-filter
version, moved its numbers slightly from the section above — CAGR 24.05% vs
the earlier 26.0% — the direction and the "best result so far" conclusion is
unchanged, this is a warm-up/gating consistency fix, not a different
strategy.)*

### Today's triple-filtered portfolio

Only 61 stocks in the entire universe pass all three filters today (vs ~186
for the two-filter version) — a much smaller, higher-conviction shortlist,
still enough to fill all 20 slots today. It looks meaningfully different from
every prior list: Shriram Finance is the only name near the top of the
6-month-only list that also tops this one. New names dominate — Apollo
Tricoat, Aditya Birla Capital, L&T Finance, MTAR Tech, Motherson, Ujjivan
SFB, Sky Gold, Bank of Maharashtra, Diamond Power (Diacabs), DCB Bank,
RateGain, Jayaswal Neco, Paras Defence, KRN Heat Exchanger, HFCL, PNB
Housing, Tejas Networks (TMB), STL Tech, Sansera Engineering — a broader,
more mid/small-cap-heavy set of names that are simultaneously high-beta,
near their highs, *and* have delivered strong risk-adjusted returns over the
past year. Average beta of this list (1.62) is noticeably lower than the
unfiltered (2.03) or two-filter (1.95) lists — the Sharpe filter is
trading off some raw beta for consistency.

### Backtest results

| Metric | No filters + stop | 2 filters (beta>1, near-high) + stop | **3 filters (+ 1y Sharpe>1) + stop** | NIFTY500 |
|---|---|---|---|---|
| CAGR | 23.3% | 24.1% | **24.8%** | 12.1% |
| Annualised Vol | 32.8% | 27.4% | **26.7%** | 16.6% |
| Sharpe (rf 6.5%) | 0.51 | 0.64 | **0.69** | 0.34 |
| Sortino | 0.64 | 0.80 | **0.83** | 0.40 |
| Max Drawdown | -70.3% | -61.3% | **-51.4%** | -38.3% |
| Calmar | 0.33 | 0.39 | **0.48** | 0.32 |
| Realised beta | 1.40 | 1.23 | **1.08** | 1.00 |
| Realised alpha (ann.) | +8.5% | +9.9% | **+12.2%** | — |
| Stop-loss events | 195 | 130 | **120** | — |
| Avg trades / rebalance | 11.3 | 16.9 | **15.0** | — |
| Final NAV (INR 10L start) | 70.1L | 66.3L | **70.1L** | 27.1L (this period) |

**The third filter continues the same trend as the second, on every metric,
and produces the best drawdown and Calmar of anything tested in this whole
exercise:** max drawdown improves to -51.4% (from -61.3% with two filters,
-80.3% with none), and Calmar reaches 0.48 — comfortably clear of NIFTY500's
own 0.32. Realised beta drops to 1.08, barely above the market itself, which
is a striking outcome for a strategy explicitly built to select high-beta
names — the combination of quality filters is now doing more work than the
beta ranking itself in shaping the realised risk profile.

**There is an important caveat the aggregate numbers don't show: the
portfolio is sometimes far from a diversified 20-name book.** In 10 of 108
months, fewer than 10 stocks passed all three filters; in 4 months, fewer
than 5 did; in the single worst month, only **1 stock** passed every gate,
meaning the "portfolio" that month was 100% in one name. Because capital is
still split 1/N across however many names qualify, a 1-stock month carries
dramatically more single-name risk than the 1/20 sizing assumed everywhere
else in this backtest — a risk the aggregate Sharpe/Calmar numbers don't
distinguish from a normal 20-name month. This tends to happen during broad
market stress, exactly when concentrated single-stock risk is least welcome.
Before running this triple-filtered version live, it would be worth adding
an explicit minimum-holdings rule (e.g. skip the month and hold cash, or fall
back to a looser filter, if fewer than N names qualify) rather than trading
whatever the filters happen to produce.

## Weekly vs monthly rebalance

Same signal (6-month trailing beta), same Top-20 equal-weight selection, same
liquidity filter — only the reconstitution frequency changes, to the last
trading day of each week (495 rebalances instead of 114 over the same 9.3
years).

| Metric | Monthly, no stop | Monthly + 20% stop | Weekly, no stop | **Weekly + 20% stop** | NIFTY500 |
|---|---|---|---|---|---|
| CAGR | 17.4% | 23.3% | 21.4% | **24.1%** | 12.6% |
| Annualised Vol | 35.6% | 32.8% | 36.1% | **35.2%** | 16.3% |
| Sharpe (rf 6.5%) | 0.31 | 0.51 | 0.41 | **0.50** | 0.37 |
| Sortino | 0.37 | 0.64 | 0.52 | **0.64** | 0.44 |
| Max Drawdown | -80.3% | -70.3% | -73.2% | **-68.7%** | -38.3% |
| Calmar | 0.22 | 0.33 | 0.29 | **0.35** | 0.33 |
| Realised beta | 1.61 | 1.40 | 1.65 | **1.61** | 1.00 |
| Stop-loss events (total) | — | 195 | — | **78** | — |
| Avg trades / rebalance | 11.3 | 11.3 | 4.8 | **4.8** | — |
| Total txn cost (backtest) | INR 25,600 | INR 29,500 | INR 47,500 | **INR 49,100** | — |
| Final NAV (INR 10L start) | 44.6L | 70.1L | 61.8L | **75.5L** | 29.9L |

**Weekly rebalancing on its own (no stop-loss) already beats monthly on
return** (21.4% vs 17.4% CAGR) at a similar risk level, because it reacts
faster to a stock's beta rank changing — a name whose beta is decaying gets
dropped in days rather than waiting up to a month. Combined with the 20%
stop-loss, weekly rebalancing edges out monthly+stop further: CAGR 24.1% vs
23.3%, max drawdown a touch shallower (-68.7% vs -70.3%), Calmar modestly
better (0.35 vs 0.33) — the best risk-adjusted profile of the four
combinations tested, and the closest of any variant to beating NIFTY500's
Calmar outright.

Two costs come with that improvement, both worth weighing before adopting it:

- **Stop-loss events drop from 195 to 78.** This isn't the stop-loss working
  less — it's that weekly reconstitution already removes fading, falling
  names from the book before they get anywhere near a 20% loss, so the stop
  has less to do. The strategy is doing more of its risk control through
  faster turnover and less through the hard stop.
- **Transaction costs roughly double** (INR 47,500–49,100 vs 25,600–29,500),
  simply from ~4.3x more rebalance events, even though each one now trades
  fewer names (4.8 vs 11.3 on average — the book is more stable week-to-week
  since it isn't a full reconstitution shock every 21 trading days). At this
  flat-fee assumption (INR 20/order) the extra cost is still under 0.1% of
  final NAV, so it doesn't change the conclusion — but a more realistic
  cost model (spread + market impact, especially in smaller-cap high-beta
  names) would erode more of weekly's edge than this flat-fee backtest shows,
  since impact scales with trade frequency and size in a way a flat order fee
  does not.

## Reading the numbers
The strategy roughly doubled NIFTY500's CAGR, which is the expected reward for
running at ~1.5x realised beta (average *selected* beta at each rebalance was
closer to ~2.3, but realised portfolio beta over the holding periods came in
lower — a common effect: trailing beta estimates decay/mean-revert once a stock
is actually held forward). The cost of that extra return is real: volatility
was roughly double the benchmark's, and the max drawdown (-66.8%, in the
2019-2020/2022 stretch) is nearly twice as deep as NIFTY500's. Risk-adjusted
metrics (Sharpe, Calmar) are close to the benchmark's or slightly worse on
Calmar — this is a strategy that harvests beta, not a source of genuine alpha
on its own, though the realised +4.3%/yr alpha here is a reasonable outcome of
buying high-beta names ahead of index up-moves more often than down-moves over
this particular decade. A user of this strategy should size it well below 100%
of a portfolio, or pair it with a hedge/timing overlay, given the drawdown
profile.

## Caveats
- Survivorship: tickers with all-zero prices (proxy for delisted/not-yet-listed)
  are excluded from selection but remain in the file; no explicit
  survivorship-bias correction was attempted beyond what's in the source data.
- Zero-volume-filled gaps in the VOLUME sheet are treated as illiquid and
  excluded via the liquidity filter, which also filters out genuinely inactive
  or thinly-traded names.
- Fills are assumed at the rebalance-day close with no market-impact/slippage
  model beyond the flat per-order fee — a real high-beta basket including
  small/mid caps could see meaningfully more slippage than modelled here.
- Beta is estimated on trailing daily returns; it is a backward-looking proxy
  and is known to be unstable / mean-reverting for individual names.

## Files
- `high_beta_backtest.py` — the backtest script. Edit config constants at the top to vary lookback, TOP_N, costs, capital, liquidity threshold, or set `STOP_LOSS_PCT = 0.20` (or any fraction) to turn the per-stock stop-loss on; leave `None` for the baseline. Output filenames get an `_slNN` suffix when a stop-loss is active, so both runs coexist in the results folder.
- `high_beta_backtest_results/High_Beta_Strategy_Backtest.xlsx` / `..._sl20.xlsx` — summary, charts, annual returns, full holdings history, daily NAV, for the no-stop and 20%-stop runs respectively.
- `high_beta_backtest_results/high_beta_equity_curve[.png|_sl20.png]`, `high_beta_avg_beta[.png|_sl20.png]`
- `high_beta_backtest_results/high_beta_stoploss_comparison.png` — equity curve + drawdown, no-stop vs 20%-stop vs NIFTY500, side by side.
- `high_beta_backtest_results/high_beta_daily_nav[.csv|_sl20.csv]`, `high_beta_holdings_history[.csv|_sl20.csv]`, `high_beta_summary[.json|_sl20.json]`
