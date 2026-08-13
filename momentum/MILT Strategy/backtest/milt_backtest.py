"""
milt_backtest.py
=================
Walk-forward weekly backtest of the MILT strategy (see milt_strategy.py for
the live-trading engine and CWA_MILT_25_Momentum_Strategy_Summary.txt for
the rule writeup). Same entry/exit/sizing rules, run week-by-week over
history instead of once against the latest bar.

Important caveats (read before trusting the numbers)
------------------------------------------------------
1. SURVIVORSHIP BIAS: the universe is today's N750 constituent list,
   projected backward. Stocks that were dropped from the top-750 during the
   backtest window (delisted, shrank, went bankrupt) are invisible here --
   their losses never hit the simulated portfolio, but a real live version
   of this strategy would have held some of them. This biases returns UP
   versus what a real investor would have experienced.
2. NO transaction costs, no slippage, no bid-ask spread, no STT/brokerage.
   Real returns will be lower, especially in choppy periods.
3. Execution model: signal on Friday's weekly close -> filled at the
   *next available* weekly bar's Open (normally the following Monday; if a
   ticker has a gap week, the next week it trades again).
4. Data window is limited to what was fetched (see the console output for
   exact dates) -- much shorter than the "27% CAGR over 3 years" the video
   cites, and covers a different historical period. Do not read this as a
   replication of that number.
5. Same two rule interpretations as the live engine: 20-week Bollinger
   window (only the 3.7 SD multiplier was stated explicitly), and the ATR
   exit implemented as a chandelier trailing stop.
6. DATA QUALITY: yfinance's auto_adjust sometimes fails to back-adjust a
   stock split/bonus, producing a fake single-day price cliff (e.g. GPIL's
   5:1 split on 2024-01-01 shows as an 80% "crash" in the raw data). A
   screen (screen_split_artifacts) excludes any ticker with a single-day
   close ratio outside [0.56, 1.8] from the backtest universe -- confirmed
   12/750 tickers affected. This protects against both phantom stop-losses
   and phantom multibagger gains, but is a blunt instrument: it also drops
   tickers with genuine (adjusted) real corporate actions we can't tell
   apart from the artifacts without cross-referencing a corporate-actions
   feed, which we don't have.

Usage
-----
    python milt_backtest.py [--file MILT_N750_backtest.xlsx]
                             [--capital 1500000] [--plot]
"""

import argparse
import datetime
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# This script lives in MILT Strategy/backtest/ but momentum_lib.py and
# milt_strategy.py (shared library + config constants) live one level up in
# MILT Strategy/ -- add that directory to sys.path so the imports below
# resolve regardless of the current working directory.
BACKTEST_DIR = Path(__file__).resolve().parent
MAIN_DIR     = BACKTEST_DIR.parent
if str(MAIN_DIR) not in sys.path:
    sys.path.insert(0, str(MAIN_DIR))

import momentum_lib as ml
from milt_strategy import (
    BB_WINDOW, BB_STD, MA_PERIOD, ATR_PERIOD, ATR_MULT,
    STOP_LOSS_PCT, MAX_POSITIONS, POSITION_PCT,
)

DEFAULT_FILE = str(BACKTEST_DIR / "MILT_N750_backtest.xlsx")
DEFAULT_CAPITAL = 1_500_000
RFR_ANNUAL = 0.07   # for Sharpe ratio only


# ── DATA PREP ───────────────────────────────────────────────────────────────────

def screen_split_artifacts(ohlc: dict, low: float = 0.56, high: float = 1.8) -> set:
    """
    Flag tickers whose daily Close has a single-day ratio outside
    [low, high] -- essentially never organic for an equity, and almost
    always an unadjusted stock split/bonus/demerger in the source data.
    Returns the set of flagged tickers to exclude from the backtest.
    """
    flagged = set()
    for t in ohlc["tickers"]:
        if t not in ohlc["close"].index:
            continue
        px = ohlc["close"].loc[t].dropna()
        if len(px) < 10:
            continue
        ratio = px / px.shift(1)
        if ((ratio > high) | (ratio < low)).any():
            flagged.add(t)
    return flagged


def build_all_weekly(ohlc: dict, exclude: set = None) -> dict:
    exclude = exclude or set()
    weekly = {}
    for t in ohlc["tickers"]:
        if t in exclude:
            continue
        wdf = ml.build_weekly_indicators(
            ohlc, t, bb_window=BB_WINDOW, bb_std=BB_STD,
            ma_period=MA_PERIOD, atr_period=ATR_PERIOD,
        )
        if wdf is None or len(wdf) < BB_WINDOW + 1:
            continue
        wdf = wdf.copy()
        wdf["roc_12m"] = wdf["close"] / wdf["close"].shift(52) - 1.0
        weekly[t] = wdf
    return weekly


def _next_available(wdf: pd.DataFrame, after_date) -> tuple:
    """First (date, open) in wdf strictly after `after_date`, or (None, None)."""
    later = wdf[wdf.index > after_date]
    if later.empty:
        return None, None
    return later.index[0], float(later.iloc[0]["open"])


# ── BACKTEST LOOP ─────────────────────────────────────────────────────────────

def run_backtest(weekly: dict, capital: float):
    master_dates = sorted(set().union(*[set(df.index) for df in weekly.values()]))
    warmup = MA_PERIOD  # weeks before indicators can be trusted anywhere
    if len(master_dates) <= warmup + 1:
        raise ValueError("Not enough weekly history to backtest "
                          f"(have {len(master_dates)} weeks, need > {warmup + 1}).")

    cash = capital
    positions: dict[str, dict] = {}   # ticker -> {entry_date, entry_price, shares, peak_close}
    trades = []                        # closed trades
    equity_curve = []                  # {date, nav, n_positions, invested_frac}

    for i in range(warmup, len(master_dates) - 1):
        date = master_dates[i]
        next_date = master_dates[i + 1]

        # ── mark-to-market equity (for sizing + equity curve) ─────────────────
        pos_value = 0.0
        for t, pos in positions.items():
            wdf = weekly.get(t)
            px = wdf.loc[date, "close"] if (wdf is not None and date in wdf.index) else np.nan
            if pd.isna(px):
                px = pos["last_price"]   # carry forward if this ticker has a gap week
            else:
                pos["last_price"] = px
            pos_value += pos["shares"] * px
        equity = cash + pos_value

        # ── 1. exits ────────────────────────────────────────────────────────
        exit_tickers = []
        for t, pos in list(positions.items()):
            wdf = weekly.get(t)
            if wdf is None or date not in wdf.index:
                continue
            bar = wdf.loc[date]
            close = bar["close"]
            if pd.isna(close):
                continue
            pos["peak_close"] = max(pos["peak_close"], close)

            reason = None
            if close <= pos["entry_price"] * (1 - STOP_LOSS_PCT):
                reason = "stop_loss_20pct"
            elif pd.notna(bar["sma23"]) and close < bar["sma23"]:
                reason = "trend_reversal_23wma"
            elif pd.notna(bar["atr"]) and close < pos["peak_close"] - ATR_MULT * bar["atr"]:
                reason = "atr_chandelier_stop"

            if reason:
                exit_tickers.append((t, reason))

        for t, reason in exit_tickers:
            wdf = weekly[t]
            exec_date, exec_price = _next_available(wdf, date)
            if exec_date is None:
                continue  # no future data to exit at (e.g. right at the end of history)
            pos = positions.pop(t)
            proceeds = pos["shares"] * exec_price
            cash += proceeds
            hold_weeks = master_dates.index(exec_date) - master_dates.index(pos["signal_date"])
            trades.append({
                "ticker": t, "reason": reason,
                "entry_date": pos["entry_date"].isoformat(), "entry_price": pos["entry_price"],
                "exit_date": exec_date.date().isoformat(), "exit_price": exec_price,
                "shares": pos["shares"], "hold_weeks": hold_weeks,
                "pnl_pct": round((exec_price / pos["entry_price"] - 1) * 100, 2),
                "pnl_rs": round(proceeds - pos["shares"] * pos["entry_price"], 2),
            })

        # ── 2. entries ──────────────────────────────────────────────────────
        available_slots = MAX_POSITIONS - len(positions)
        if available_slots > 0:
            candidates = []
            for t, wdf in weekly.items():
                if t in positions or date not in wdf.index:
                    continue
                bar = wdf.loc[date]
                if pd.isna(bar["bb_upper"]) or pd.isna(bar["close"]):
                    continue
                if bar["close"] > bar["bb_upper"]:
                    candidates.append((t, bar["close"], bar["roc_12m"]))

            candidates.sort(key=lambda c: (c[2] if pd.notna(c[2]) else -999), reverse=True)
            selected = candidates[:available_slots]

            # recompute equity after this week's exits, for sizing
            equity_after_exits = cash + sum(
                p["shares"] * p["last_price"] for p in positions.values()
            )
            alloc = equity_after_exits * POSITION_PCT

            for t, sig_close, roc in selected:
                wdf = weekly[t]
                exec_date, exec_price = _next_available(wdf, date)
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
                    "shares": shares, "peak_close": exec_price,
                    "last_price": exec_price, "signal_date": date,
                }

        # ── 3. record equity curve (post-trade, mark-to-market at `date`) ─────
        pos_value_final = cash * 0  # recompute cleanly
        pos_value_final = sum(p["shares"] * p["last_price"] for p in positions.values())
        nav = cash + pos_value_final
        equity_curve.append({
            "date": date.date().isoformat(), "nav": nav,
            "n_positions": len(positions),
            "invested_frac": (nav - cash) / nav if nav > 0 else 0.0,
        })

    # ── liquidate anything still open at the end, at the last known close ────
    for t, pos in positions.items():
        wdf = weekly[t]
        final_price = pos["last_price"]
        proceeds = pos["shares"] * final_price
        hold_weeks = len(master_dates) - 1 - master_dates.index(pos["signal_date"])
        trades.append({
            "ticker": t, "reason": "open_at_backtest_end",
            "entry_date": pos["entry_date"].isoformat(), "entry_price": pos["entry_price"],
            "exit_date": master_dates[-1].date().isoformat(), "exit_price": final_price,
            "shares": pos["shares"], "hold_weeks": hold_weeks,
            "pnl_pct": round((final_price / pos["entry_price"] - 1) * 100, 2),
            "pnl_rs": round(proceeds - pos["shares"] * pos["entry_price"], 2),
        })

    return pd.DataFrame(equity_curve), pd.DataFrame(trades), master_dates


# ── METRICS ───────────────────────────────────────────────────────────────────

def compute_metrics(equity_df: pd.DataFrame, capital: float) -> dict:
    nav = equity_df["nav"].values
    dates = pd.to_datetime(equity_df["date"])
    n_years = (dates.iloc[-1] - dates.iloc[0]).days / 365.25

    total_return = nav[-1] / capital - 1
    cagr = (nav[-1] / capital) ** (1 / n_years) - 1 if n_years > 0 else np.nan

    weekly_rets = pd.Series(nav).pct_change().dropna()
    ann_vol = weekly_rets.std() * np.sqrt(52)
    rfr_weekly = RFR_ANNUAL / 52
    sharpe = ((weekly_rets.mean() - rfr_weekly) / weekly_rets.std() * np.sqrt(52)
              if weekly_rets.std() > 0 else np.nan)

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


def compute_benchmark_metrics(nifty_series: pd.Series, start_date, end_date, capital: float) -> dict:
    px = nifty_series.dropna()
    px.index = pd.to_datetime(px.index)
    px = px[(px.index >= pd.Timestamp(start_date)) & (px.index <= pd.Timestamp(end_date))]
    if len(px) < 2:
        return {}
    n_years = (px.index[-1] - px.index[0]).days / 365.25
    total_return = px.iloc[-1] / px.iloc[0] - 1
    cagr = (px.iloc[-1] / px.iloc[0]) ** (1 / n_years) - 1 if n_years > 0 else np.nan

    daily_rets = px.pct_change().dropna()
    ann_vol = daily_rets.std() * np.sqrt(252)
    running_max = px.cummax()
    drawdown = px / running_max - 1
    max_dd = drawdown.min()

    return {
        "total_return_pct": round(total_return * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "ann_vol_pct": round(ann_vol * 100, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "end_value": round(capital * (1 + total_return), 0),
    }


def trade_stats(trades_df: pd.DataFrame) -> dict:
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
        "avg_hold_weeks": round(trades_df["hold_weeks"].mean(), 1),
        "exit_reason_counts": trades_df["reason"].value_counts().to_dict(),
    }


# ── MAIN ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Backtest the MILT strategy.")
    parser.add_argument("--file", default=DEFAULT_FILE)
    parser.add_argument("--capital", type=float, default=DEFAULT_CAPITAL)
    parser.add_argument("--plot", action="store_true", help="Save an equity curve PNG")
    args = parser.parse_args()

    if not Path(args.file).exists():
        print(f"ERROR: {args.file} not found. Run milt_update_prices.py --period 5y "
              f"--output {args.file} first.")
        return

    print(f"Loading {args.file} ...")
    ohlc = ml.load_ohlc(args.file)
    print(f"  {len(ohlc['tickers'])} tickers | {len(ohlc['dates'])} daily bars "
          f"({ohlc['dates'][0]} -> {ohlc['dates'][-1]})\n")

    print("Screening for unadjusted split/bonus artifacts (single-day close "
          "ratio outside [0.56, 1.8]) ...")
    flagged = screen_split_artifacts(ohlc)
    print(f"  {len(flagged)} ticker(s) excluded: {sorted(flagged)}\n")

    print("Building weekly OHLC + indicators for every ticker ...")
    weekly = build_all_weekly(ohlc, exclude=flagged)
    print(f"  {len(weekly)} tickers with usable weekly history\n")

    print("Running walk-forward weekly backtest ...")
    equity_df, trades_df, master_dates = run_backtest(weekly, args.capital)
    print(f"  Simulated {len(equity_df)} weeks "
          f"({equity_df['date'].iloc[0]} -> {equity_df['date'].iloc[-1]})")
    print(f"  {len(trades_df)} closed trades\n")

    equity_csv  = BACKTEST_DIR / "milt_backtest_equity.csv"
    trades_csv  = BACKTEST_DIR / "milt_backtest_trades.csv"
    equity_df.to_csv(equity_csv, index=False)
    trades_df.to_csv(trades_csv, index=False)
    print(f"Saved {equity_csv.name}, {trades_csv.name}\n")

    metrics = compute_metrics(equity_df, args.capital)
    tstats = trade_stats(trades_df)
    bench = compute_benchmark_metrics(
        ohlc["nifty"], equity_df["date"].iloc[0], equity_df["date"].iloc[-1], args.capital)

    print(f"{'='*70}\nMILT STRATEGY -- BACKTEST RESULTS\n{'='*70}")
    print(f"  Period            : {metrics['start_date']} -> {metrics['end_date']} "
          f"({metrics['years']} yrs)")
    print(f"  Start NAV         : Rs {metrics['start_nav']:,.0f}")
    print(f"  End NAV           : Rs {metrics['end_nav']:,.0f}")
    print(f"  Total return      : {metrics['total_return_pct']:+.2f}%")
    print(f"  CAGR              : {metrics['cagr_pct']:+.2f}%")
    print(f"  Annualised vol    : {metrics['ann_vol_pct']:.2f}%")
    print(f"  Sharpe (rf {RFR_ANNUAL*100:.0f}%)   : {metrics['sharpe']}")
    print(f"  Max drawdown      : {metrics['max_drawdown_pct']:.2f}%")
    print()
    print(f"  Trades executed   : {tstats['n_trades']}")
    if tstats["n_trades"]:
        print(f"  Win rate          : {tstats['win_rate_pct']}%")
        print(f"  Avg win / loss    : {tstats['avg_win_pct']:+.2f}% / {tstats['avg_loss_pct']:+.2f}%")
        print(f"  Best / worst trade: {tstats['best_trade_pct']:+.2f}% / {tstats['worst_trade_pct']:+.2f}%")
        print(f"  Avg hold period   : {tstats['avg_hold_weeks']} weeks")
        print(f"  Exit reasons      : {tstats['exit_reason_counts']}")
    print()
    if bench:
        print(f"  --- NIFTY500 buy & hold, same period ---")
        print(f"  Total return      : {bench['total_return_pct']:+.2f}%")
        print(f"  CAGR              : {bench['cagr_pct']:+.2f}%")
        print(f"  Annualised vol    : {bench['ann_vol_pct']:.2f}%")
        print(f"  Max drawdown      : {bench['max_drawdown_pct']:.2f}%")
    print(f"{'='*70}")
    print("CAVEATS: survivorship bias (today's N750 list used for the whole "
          "backtest window), no transaction costs/slippage modeled, short "
          "data window vs. the strategy's claimed 3-year track record. "
          "See the module docstring for details.")

    if args.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(11, 5))
        ax.plot(pd.to_datetime(equity_df["date"]), equity_df["nav"], label="MILT strategy")
        if bench:
            nifty_px = ohlc["nifty"].dropna()
            nifty_px.index = pd.to_datetime(nifty_px.index)
            nifty_px = nifty_px[(nifty_px.index >= pd.Timestamp(equity_df["date"].iloc[0])) &
                                (nifty_px.index <= pd.Timestamp(equity_df["date"].iloc[-1]))]
            nifty_norm = nifty_px / nifty_px.iloc[0] * args.capital
            ax.plot(nifty_px.index, nifty_norm, label="NIFTY500 (buy & hold)", alpha=0.7)
        ax.set_title("MILT Strategy Backtest — Equity Curve")
        ax.set_ylabel("NAV (Rs)")
        ax.legend()
        ax.grid(alpha=0.3)
        fig.tight_layout()
        equity_png = BACKTEST_DIR / "milt_backtest_equity.png"
        fig.savefig(equity_png, dpi=120)
        print(f"Saved {equity_png.name}")


if __name__ == "__main__":
    main()
