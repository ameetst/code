"""
rsi_momentum_strategy_backtest.py
====================================
Standalone strategy (actionable #4) built on the RSI signal-quality findings
from rsi_range_momentum_signal_test.py (#2), which replicated Arthur Hill's
"Finding Consistent Trends with Strong Momentum" (SSRN 3412429, Feb 2019) on
the N750 NSE universe.

#2's result diverged from the paper: Hill's own preferred "Range-Momentum"
combination didn't clearly beat "Bull Momentum" alone on N750 -- Bull
Momentum alone had both the higher Success Rate and the higher P/L Ratio at
the 75-125 day lookbacks. This script runs BOTH as full portfolio strategies
(not just isolated per-signal stats) on the same universe/period/engine, so
they can be compared on CAGR / Sharpe / Max Drawdown -- portfolio-level
effects (correlation across simultaneous signals, capital constraints,
one-trade-at-a-time-per-ticker) aren't visible in a signal-quality test.

milt_strategy.py is imported read-only for shared constants (same pattern as
cwa25s_daily_backtest.py) and is never modified.

Signals (both use RSI(14), N=75 days -- the shortest lookback in the paper's
own "sweet spot" of 75-125 days, chosen for a richer trade sample given only
~5 years of data):
  bull_momentum  : RSI has reached >= 70 at some point in the last 75 days
  range_momentum : bull_momentum AND RSI hasn't dipped below 40 in the same window
Entry triggers on the day the signal first turns active (transition
False->True), executed at the next available daily Open.

Exit (checked in this order, first match wins):
  1. STOP_LOSS_PCT hard stop (20%, from milt_strategy.py) -- a safety net the
     paper itself doesn't use; added because every other backtest in this
     repo has one and a pure signal-reversal exit has no floor.
  2. Signal reversal -- the day the active flag turns False (the paper's own
     exit rule).
Both executed at the next available daily Open.

Portfolio engine: identical to milt_backtest.py / cwa25s_daily_backtest.py --
4% of current equity per position (POSITION_PCT), max 25 positions
(MAX_POSITIONS), 252-day ROC tie-break when candidates exceed available
slots. Same universe (MILT_N750_backtest.xlsx), same split-artifact screen.
No transaction costs / slippage.

Usage
-----
    python rsi_momentum_strategy_backtest.py [--file MILT_N750_backtest.xlsx]
                                              [--capital 1500000] [--plot]
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BACKTEST_DIR = Path(__file__).resolve().parent
MAIN_DIR     = BACKTEST_DIR.parent
if str(MAIN_DIR) not in sys.path:
    sys.path.insert(0, str(MAIN_DIR))

import momentum_lib as ml
from milt_strategy import MAX_POSITIONS, POSITION_PCT, STOP_LOSS_PCT   # read-only import
from milt_backtest import screen_split_artifacts, compute_benchmark_metrics  # read-only import

DEFAULT_FILE    = str(BACKTEST_DIR / "MILT_N750_backtest.xlsx")
DEFAULT_CAPITAL = 1_500_000
RFR_ANNUAL      = 0.07

RSI_PERIOD        = 14
RSI_LOOKBACK_N    = 75      # trading days -- paper's shortest "sweet spot" lookback
RANGE_FLOOR        = 40.0
MOMENTUM_CEILING   = 70.0
ROC_LOOKBACK_DAYS  = 252
WARMUP_DAYS        = 100    # >= RSI_LOOKBACK_N + RSI_PERIOD, matches magnitude of other daily scripts

SIGNAL_MODES = {
    "bull_momentum": "RSI Bull Momentum (N=75)",
    "range_momentum": "RSI Bull Range-Momentum (N=75)",
}


# ── DATA PREP ────────────────────────────────────────────────────────────────

def build_all_daily_rsi(ohlc: dict, exclude: set = None) -> dict:
    """Daily OHLC + RSI(14) + both signal/entry-trigger columns + 12M ROC,
    for every ticker with enough history. Computed once and shared by both
    signal-mode backtest runs below."""
    exclude = exclude or set()
    min_len = RSI_LOOKBACK_N + RSI_PERIOD + 5
    daily = {}
    for t in ohlc["tickers"]:
        if t in exclude:
            continue
        if any(ohlc[f] is None or t not in ohlc[f].index for f in ("open", "close")):
            continue
        close = ohlc["close"].loc[t].dropna()
        if len(close) < min_len:
            continue
        df = pd.DataFrame({
            "open":  ohlc["open"].loc[t],
            "close": ohlc["close"].loc[t],
        }).dropna(subset=["close"])
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()

        rsi = ml.compute_rsi(df["close"], RSI_PERIOD)
        df["rsi"] = rsi
        df["active_momentum"] = ml.rsi_momentum_active(rsi, RSI_LOOKBACK_N, MOMENTUM_CEILING)
        df["active_rangemom"] = ml.rsi_range_momentum_active(
            rsi, RSI_LOOKBACK_N, RANGE_FLOOR, MOMENTUM_CEILING)
        df["entry_trigger_momentum"] = df["active_momentum"] & ~df["active_momentum"].shift(1, fill_value=False)
        df["entry_trigger_rangemom"] = df["active_rangemom"] & ~df["active_rangemom"].shift(1, fill_value=False)
        df["roc_12m"] = df["close"] / df["close"].shift(ROC_LOOKBACK_DAYS) - 1.0

        daily[t] = df
    return daily


def _next_available(ddf: pd.DataFrame, after_date) -> tuple:
    later = ddf[ddf.index > after_date]
    if later.empty:
        return None, None
    return later.index[0], float(later.iloc[0]["open"])


# ── BACKTEST LOOP (daily) ─────────────────────────────────────────────────────

def run_backtest(daily: dict, signal_mode: str, capital: float):
    active_col  = f"active_{'momentum' if signal_mode == 'bull_momentum' else 'rangemom'}"
    trigger_col = f"entry_trigger_{'momentum' if signal_mode == 'bull_momentum' else 'rangemom'}"

    master_dates = sorted(set().union(*[set(df.index) for df in daily.values()]))
    if len(master_dates) <= WARMUP_DAYS + 1:
        raise ValueError(f"Not enough daily history (have {len(master_dates)}, need > {WARMUP_DAYS + 1}).")

    cash = capital
    positions: dict[str, dict] = {}
    trades = []
    equity_curve = []

    for i in range(WARMUP_DAYS, len(master_dates) - 1):
        date = master_dates[i]

        for t, pos in positions.items():
            ddf = daily.get(t)
            px = ddf.loc[date, "close"] if (ddf is not None and date in ddf.index) else np.nan
            pos["last_price"] = px if pd.notna(px) else pos["last_price"]

        # ── exits ────────────────────────────────────────────────────────────
        exit_tickers = []
        for t, pos in list(positions.items()):
            ddf = daily.get(t)
            if ddf is None or date not in ddf.index:
                continue
            bar = ddf.loc[date]
            close = bar["close"]
            if pd.isna(close):
                continue

            reason = None
            if close <= pos["entry_price"] * (1 - STOP_LOSS_PCT):
                reason = "hard_stop_20pct"
            elif not bool(bar[active_col]):
                reason = "signal_reversal"

            if reason:
                exit_tickers.append((t, reason))

        for t, reason in exit_tickers:
            ddf = daily[t]
            exec_date, exec_price = _next_available(ddf, date)
            if exec_date is None:
                continue
            pos = positions.pop(t)
            proceeds = pos["shares"] * exec_price
            cash += proceeds
            hold_days = master_dates.index(exec_date) - master_dates.index(pos["signal_date"])
            trades.append({
                "ticker": t, "reason": reason,
                "entry_date": pos["entry_date"].isoformat(), "entry_price": pos["entry_price"],
                "exit_date": exec_date.date().isoformat(), "exit_price": exec_price,
                "shares": pos["shares"], "hold_days": hold_days,
                "pnl_pct": round((exec_price / pos["entry_price"] - 1) * 100, 2),
                "pnl_rs": round(proceeds - pos["shares"] * pos["entry_price"], 2),
            })

        # ── entries ──────────────────────────────────────────────────────────
        available_slots = MAX_POSITIONS - len(positions)
        if available_slots > 0:
            candidates = []
            for t, ddf in daily.items():
                if t in positions or date not in ddf.index:
                    continue
                bar = ddf.loc[date]
                if pd.isna(bar["close"]) or not bool(bar[trigger_col]):
                    continue
                candidates.append((t, bar["close"], bar["roc_12m"]))

            candidates.sort(key=lambda c: (c[2] if pd.notna(c[2]) else -999), reverse=True)
            selected = candidates[:available_slots]

            equity_after_exits = cash + sum(p["shares"] * p["last_price"] for p in positions.values())
            alloc = equity_after_exits * POSITION_PCT

            for t, sig_close, roc in selected:
                ddf = daily[t]
                exec_date, exec_price = _next_available(ddf, date)
                if exec_date is None or exec_price <= 0:
                    continue
                shares = int(alloc // exec_price)
                if shares <= 0:
                    continue
                cost = shares * exec_price
                if cost > cash:
                    continue
                cash -= cost
                positions[t] = {
                    "entry_date": exec_date.date(), "entry_price": exec_price,
                    "shares": shares, "last_price": exec_price,
                    "signal_date": date,
                }

        pos_value = sum(p["shares"] * p["last_price"] for p in positions.values())
        nav = cash + pos_value
        equity_curve.append({
            "date": date.date().isoformat(), "nav": nav,
            "n_positions": len(positions),
            "invested_frac": (nav - cash) / nav if nav > 0 else 0.0,
        })

    for t, pos in positions.items():
        final_price = pos["last_price"]
        proceeds = pos["shares"] * final_price
        hold_days = len(master_dates) - 1 - master_dates.index(pos["signal_date"])
        trades.append({
            "ticker": t, "reason": "open_at_backtest_end",
            "entry_date": pos["entry_date"].isoformat(), "entry_price": pos["entry_price"],
            "exit_date": master_dates[-1].date().isoformat(), "exit_price": final_price,
            "shares": pos["shares"], "hold_days": hold_days,
            "pnl_pct": round((final_price / pos["entry_price"] - 1) * 100, 2),
            "pnl_rs": round(proceeds - pos["shares"] * pos["entry_price"], 2),
        })

    return pd.DataFrame(equity_curve), pd.DataFrame(trades), master_dates


# ── METRICS (daily-annualised) ────────────────────────────────────────────────

def compute_metrics_daily(equity_df: pd.DataFrame, capital: float) -> dict:
    nav = equity_df["nav"].values
    dates = pd.to_datetime(equity_df["date"])
    n_years = (dates.iloc[-1] - dates.iloc[0]).days / 365.25

    total_return = nav[-1] / capital - 1
    cagr = (nav[-1] / capital) ** (1 / n_years) - 1 if n_years > 0 else np.nan

    daily_rets = pd.Series(nav).pct_change().dropna()
    ann_vol = daily_rets.std() * np.sqrt(252)
    rfr_daily = RFR_ANNUAL / 252
    sharpe = ((daily_rets.mean() - rfr_daily) / daily_rets.std() * np.sqrt(252)
              if daily_rets.std() > 0 else np.nan)

    running_max = pd.Series(nav).cummax()
    drawdown = pd.Series(nav) / running_max - 1
    max_dd = drawdown.min()

    return {
        "start_date": dates.iloc[0].date().isoformat(),
        "end_date": dates.iloc[-1].date().isoformat(),
        "years": round(n_years, 2),
        "start_nav": capital,
        "end_nav": round(nav[-1], 0),
        "total_return_pct": round(total_return * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "ann_vol_pct": round(ann_vol * 100, 2),
        "sharpe": round(sharpe, 2) if pd.notna(sharpe) else None,
        "max_drawdown_pct": round(max_dd * 100, 2),
    }


def trade_stats_daily(trades_df: pd.DataFrame) -> dict:
    if trades_df.empty:
        return {"n_trades": 0}
    wins = trades_df[trades_df["pnl_pct"] > 0]
    losses = trades_df[trades_df["pnl_pct"] <= 0]
    return {
        "n_trades": len(trades_df),
        "win_rate_pct": round(len(wins) / len(trades_df) * 100, 1),
        "avg_win_pct": round(wins["pnl_pct"].mean(), 2) if len(wins) else 0.0,
        "avg_loss_pct": round(losses["pnl_pct"].mean(), 2) if len(losses) else 0.0,
        "best_trade_pct": round(trades_df["pnl_pct"].max(), 2),
        "worst_trade_pct": round(trades_df["pnl_pct"].min(), 2),
        "avg_hold_days": round(trades_df["hold_days"].mean(), 1),
        "exit_reason_counts": trades_df["reason"].value_counts().to_dict(),
    }


# ── COMPARISON TABLE ──────────────────────────────────────────────────────────

def _row(label, a, b, fmt="{:.2f}"):
    fa = fmt.format(a) if a is not None else "n/a"
    fb = fmt.format(b) if b is not None else "n/a"
    print(f"  {label:<22} {fa:>20} {fb:>20}")


def print_comparison(metrics_a, tstats_a, metrics_b, tstats_b, bench):
    print(f"\n{'='*76}")
    print("RSI MOMENTUM STRATEGY BACKTEST -- N750 (Hill 2019 methodology, entry-signal comparison)")
    print(f"{'='*76}")
    print(f"  {'Metric':<22} {SIGNAL_MODES['bull_momentum']:>20} {SIGNAL_MODES['range_momentum']:>20}")
    print(f"  {'-'*22} {'-'*20} {'-'*20}")
    print(f"  {'Period':<22} {metrics_a['start_date']+' to '+metrics_a['end_date']:>20.20} "
          f"{metrics_b['start_date']+' to '+metrics_b['end_date']:>20.20}")
    _row("Years", metrics_a["years"], metrics_b["years"])
    _row("Total return %", metrics_a["total_return_pct"], metrics_b["total_return_pct"], "{:+.2f}%")
    _row("CAGR %", metrics_a["cagr_pct"], metrics_b["cagr_pct"], "{:+.2f}%")
    _row("Ann. volatility %", metrics_a["ann_vol_pct"], metrics_b["ann_vol_pct"], "{:.2f}%")
    _row("Sharpe (rf 7%)", metrics_a["sharpe"], metrics_b["sharpe"], "{:.2f}")
    _row("Max drawdown %", metrics_a["max_drawdown_pct"], metrics_b["max_drawdown_pct"], "{:.2f}%")
    print()
    _row("Trades", tstats_a.get("n_trades", 0), tstats_b.get("n_trades", 0), "{:.0f}")
    _row("Win rate %", tstats_a.get("win_rate_pct"), tstats_b.get("win_rate_pct"), "{:.1f}%")
    _row("Avg win %", tstats_a.get("avg_win_pct"), tstats_b.get("avg_win_pct"), "{:+.2f}%")
    _row("Avg loss %", tstats_a.get("avg_loss_pct"), tstats_b.get("avg_loss_pct"), "{:+.2f}%")
    _row("Best trade %", tstats_a.get("best_trade_pct"), tstats_b.get("best_trade_pct"), "{:+.2f}%")
    _row("Worst trade %", tstats_a.get("worst_trade_pct"), tstats_b.get("worst_trade_pct"), "{:+.2f}%")
    _row("Avg hold (days)", tstats_a.get("avg_hold_days"), tstats_b.get("avg_hold_days"), "{:.1f}")
    if bench:
        print()
        _row("NIFTY500 CAGR %", bench["cagr_pct"], bench["cagr_pct"], "{:+.2f}%")
        _row("NIFTY500 max DD %", bench["max_drawdown_pct"], bench["max_drawdown_pct"], "{:.2f}%")
    print(f"\n{'='*76}")
    print("Both variants use identical portfolio rules (4% of current equity, max 25 "
          "positions, 12M ROC tie-break, RSI(14)/N=75, same universe/period/hard-stop) "
          "-- differences above come ONLY from the entry-signal definition (momentum "
          "alone vs. range+momentum).")


# ── MAIN ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="RSI momentum strategy backtest (Hill 2019 methodology) on N750 -- runs both entry-signal variants.")
    parser.add_argument("--file", default=DEFAULT_FILE)
    parser.add_argument("--capital", type=float, default=DEFAULT_CAPITAL)
    parser.add_argument("--plot", action="store_true")
    args = parser.parse_args()

    if not Path(args.file).exists():
        print(f"ERROR: {args.file} not found.")
        return

    print(f"Loading {args.file} ...")
    ohlc = ml.load_ohlc(args.file)
    print(f"  {len(ohlc['tickers'])} tickers | {len(ohlc['dates'])} daily bars "
          f"({ohlc['dates'][0]} -> {ohlc['dates'][-1]})\n")

    flagged = screen_split_artifacts(ohlc)
    print(f"Excluded {len(flagged)} split-artifact ticker(s)\n")

    print(f"Building daily RSI({RSI_PERIOD}) signals (N={RSI_LOOKBACK_N}) ...")
    daily = build_all_daily_rsi(ohlc, exclude=flagged)
    print(f"  {len(daily)} tickers with usable daily history\n")

    results = {}
    for mode in ("bull_momentum", "range_momentum"):
        print(f"Running walk-forward backtest -- {SIGNAL_MODES[mode]} ...")
        equity_df, trades_df, master_dates = run_backtest(daily, mode, args.capital)
        print(f"  Simulated {len(equity_df)} trading days "
              f"({equity_df['date'].iloc[0]} -> {equity_df['date'].iloc[-1]}), "
              f"{len(trades_df)} closed trades\n")

        prefix = "rsi_strategy_bullmom" if mode == "bull_momentum" else "rsi_strategy_rangemom"
        equity_df.to_csv(BACKTEST_DIR / f"{prefix}_equity.csv", index=False)
        trades_df.to_csv(BACKTEST_DIR / f"{prefix}_trades.csv", index=False)

        metrics = compute_metrics_daily(equity_df, args.capital)
        tstats = trade_stats_daily(trades_df)
        results[mode] = (equity_df, trades_df, metrics, tstats)

    bench = compute_benchmark_metrics(
        ohlc["nifty"],
        results["bull_momentum"][0]["date"].iloc[0],
        results["bull_momentum"][0]["date"].iloc[-1],
        args.capital,
    )

    print_comparison(
        results["bull_momentum"][2], results["bull_momentum"][3],
        results["range_momentum"][2], results["range_momentum"][3],
        bench,
    )

    for mode in ("bull_momentum", "range_momentum"):
        tstats = results[mode][3]
        print(f"\n{SIGNAL_MODES[mode]} exit reasons: {tstats.get('exit_reason_counts', {})}")

    if args.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(11, 5))
        for mode, color in (("bull_momentum", "tab:blue"), ("range_momentum", "tab:red")):
            eq = results[mode][0]
            ax.plot(pd.to_datetime(eq["date"]), eq["nav"], label=SIGNAL_MODES[mode], color=color)
        ax.set_title("RSI Momentum Strategy -- Equity Curve Comparison (N750)")
        ax.set_ylabel("NAV (Rs)")
        ax.legend()
        ax.grid(alpha=0.3)
        fig.tight_layout()
        png_path = BACKTEST_DIR / "rsi_strategy_comparison_equity.png"
        fig.savefig(png_path, dpi=120)
        print(f"\nSaved {png_path.name}")


if __name__ == "__main__":
    main()
