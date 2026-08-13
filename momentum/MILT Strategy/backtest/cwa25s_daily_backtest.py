"""
cwa25s_daily_backtest.py
=========================
Same CWA 2.5-Sigma rules and portfolio engine as cwa25s_backtest.py, but
evaluated on DAILY bars instead of weekly-resampled bars -- this is the
more literal reading of the source Pine Script (../CWA 2.5 Sigma.txt),
which declares no weekly resample and so runs on whatever timeframe it's
charted on (most likely daily by default in TradingView).

Rules (identical to the weekly version, just on daily bars now):
  Entry : crossover(close, bb_upper) AND close > EMA(100)
            bb_upper = SMA(close, 50) + 2.5 * stdev(close, 50)
          all periods now mean 50/100/14 DAYS, not weeks.
  Exit  : ANY of (priority order: hard stop > EMA break > ATR ratchet)
            1. close <= entry_price * 0.80
            2. close < EMA(100)
            3. close < ratcheting trailing stop (running max of
               close_t - 1.8*ATR14_t since entry, never decreases)
Portfolio: same as MILT/CWA-weekly -- N750 universe, 4% of current equity
per position, max 25 positions, 12-month (252 trading day) ROC tie-break,
no rebalancing.

Why this matters vs. the weekly version
------------------------------------------
Same signal logic, but checked and executed every trading day instead of
once a week. Expect: far more trades, faster reaction to both entries and
exits, and a result that's more sensitive to daily noise/whipsaws. No
transaction costs are modeled here either -- with a much higher trade count
than the weekly version, real-world costs would erode this result more.

Usage
-----
    python cwa25s_daily_backtest.py [--file MILT_N750_backtest.xlsx] [--plot]
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
from milt_strategy import MAX_POSITIONS, POSITION_PCT, STOP_LOSS_PCT, ATR_MULT
from milt_backtest import screen_split_artifacts, compute_benchmark_metrics

DEFAULT_FILE = str(BACKTEST_DIR / "MILT_N750_backtest.xlsx")
DEFAULT_CAPITAL = 1_500_000
RFR_ANNUAL = 0.07

BB_WINDOW  = 50   # trading days (not weeks -- this is the daily-native reading)
BB_STD     = 2.5
EMA_PERIOD = 100  # trading days
ATR_PERIOD = 14   # trading days
ROC_LOOKBACK_DAYS = 252


# ── DATA PREP ───────────────────────────────────────────────────────────────────

def build_all_daily_cwa(ohlc: dict, exclude: set = None) -> dict:
    """Daily OHLC + CWA indicators (bb_upper 50/2.5, ema100, atr14, 12M ROC,
    crossover flag) for every ticker. No weekly resample -- raw daily bars."""
    exclude = exclude or set()
    daily = {}
    for t in ohlc["tickers"]:
        if t in exclude:
            continue
        if any(ohlc[f] is None or t not in ohlc[f].index for f in ("open", "high", "low", "close")):
            continue
        close = ohlc["close"].loc[t].dropna()
        if len(close) < BB_WINDOW + 1:
            continue
        df = pd.DataFrame({
            "open":  ohlc["open"].loc[t],
            "high":  ohlc["high"].loc[t],
            "low":   ohlc["low"].loc[t],
            "close": ohlc["close"].loc[t],
        }).dropna(subset=["close"])
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()

        df["bb_upper"] = ml.compute_bollinger_upper(df["close"], BB_WINDOW, BB_STD)
        df["ema100"]   = ml.compute_ema(df["close"], EMA_PERIOD)
        df["atr"]      = ml.compute_atr(df["high"], df["low"], df["close"], ATR_PERIOD)
        df["roc_12m"]  = df["close"] / df["close"].shift(ROC_LOOKBACK_DAYS) - 1.0

        prev_close    = df["close"].shift(1)
        prev_bb_upper = df["bb_upper"].shift(1)
        df["crossover"] = (df["close"] > df["bb_upper"]) & (prev_close <= prev_bb_upper)

        daily[t] = df
    return daily


def _next_available(ddf: pd.DataFrame, after_date) -> tuple:
    later = ddf[ddf.index > after_date]
    if later.empty:
        return None, None
    return later.index[0], float(later.iloc[0]["open"])


# ── BACKTEST LOOP (daily) ─────────────────────────────────────────────────────

def run_backtest_daily(daily: dict, capital: float):
    master_dates = sorted(set().union(*[set(df.index) for df in daily.values()]))
    warmup = EMA_PERIOD
    if len(master_dates) <= warmup + 1:
        raise ValueError(f"Not enough daily history (have {len(master_dates)}, need > {warmup + 1}).")

    cash = capital
    positions: dict[str, dict] = {}
    trades = []
    equity_curve = []

    for i in range(warmup, len(master_dates) - 1):
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

            if pd.notna(bar["atr"]):
                candidate = close - ATR_MULT * bar["atr"]
                if candidate > pos["trailing_stop"]:
                    pos["trailing_stop"] = candidate

            reason = None
            if close <= pos["entry_price"] * (1 - STOP_LOSS_PCT):
                reason = "hard_stop_20pct"
            elif pd.notna(bar["ema100"]) and close < bar["ema100"]:
                reason = "ema100_break"
            elif close < pos["trailing_stop"]:
                reason = "atr_ratchet_stop"

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
                if pd.isna(bar["close"]) or pd.isna(bar["ema100"]):
                    continue
                if bool(bar["crossover"]) and bar["close"] > bar["ema100"]:
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
                entry_bar = ddf.loc[date]
                entry_atr = entry_bar["atr"] if pd.notna(entry_bar["atr"]) else 0.0
                positions[t] = {
                    "entry_date": exec_date.date(), "entry_price": exec_price,
                    "shares": shares, "last_price": exec_price,
                    "signal_date": date,
                    "trailing_stop": exec_price - ATR_MULT * entry_atr,
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


# ── MAIN ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Backtest CWA 2.5-Sigma on DAILY bars.")
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

    print(f"Building daily indicators (BB{BB_WINDOW}/{BB_STD}sigma, EMA{EMA_PERIOD}, ATR{ATR_PERIOD}) ...")
    daily = build_all_daily_cwa(ohlc, exclude=flagged)
    print(f"  {len(daily)} tickers with usable daily history\n")

    print("Running walk-forward DAILY backtest (this loops over ~1200+ days x ~700 tickers, may take a few minutes) ...")
    equity_df, trades_df, master_dates = run_backtest_daily(daily, args.capital)
    print(f"  Simulated {len(equity_df)} trading days "
          f"({equity_df['date'].iloc[0]} -> {equity_df['date'].iloc[-1]})")
    print(f"  {len(trades_df)} closed trades\n")

    equity_csv = BACKTEST_DIR / "cwa25s_daily_backtest_equity.csv"
    trades_csv = BACKTEST_DIR / "cwa25s_daily_backtest_trades.csv"
    equity_df.to_csv(equity_csv, index=False)
    trades_df.to_csv(trades_csv, index=False)
    print(f"Saved {equity_csv.name}, {trades_csv.name}\n")

    metrics = compute_metrics_daily(equity_df, args.capital)
    tstats = trade_stats_daily(trades_df)
    bench = compute_benchmark_metrics(
        ohlc["nifty"], equity_df["date"].iloc[0], equity_df["date"].iloc[-1], args.capital)

    print(f"{'='*70}\nCWA 2.5-SIGMA (DAILY BARS) -- BACKTEST RESULTS\n{'='*70}")
    print(f"  Period            : {metrics['start_date']} -> {metrics['end_date']} ({metrics['years']} yrs)")
    print(f"  CAGR              : {metrics['cagr_pct']:+.2f}%")
    print(f"  Annualised vol    : {metrics['ann_vol_pct']:.2f}%")
    print(f"  Sharpe (rf 7%)    : {metrics['sharpe']}")
    print(f"  Max drawdown      : {metrics['max_drawdown_pct']:.2f}%")
    print(f"  Trades            : {tstats['n_trades']}")
    if tstats["n_trades"]:
        print(f"  Win rate          : {tstats['win_rate_pct']}%")
        print(f"  Avg win / loss    : {tstats['avg_win_pct']:+.2f}% / {tstats['avg_loss_pct']:+.2f}%")
        print(f"  Best / worst trade: {tstats['best_trade_pct']:+.2f}% / {tstats['worst_trade_pct']:+.2f}%")
        print(f"  Avg hold period   : {tstats['avg_hold_days']} days")
        print(f"  Exit reasons      : {tstats['exit_reason_counts']}")
    if bench:
        print(f"  NIFTY500 CAGR (same window): {bench['cagr_pct']:+.2f}%")
    print(f"{'='*70}")

    if args.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(11, 5))
        ax.plot(pd.to_datetime(equity_df["date"]), equity_df["nav"], label="CWA 2.5-Sigma (daily)")
        ax.set_title("CWA 2.5-Sigma — Daily-Bar Backtest — Equity Curve")
        ax.set_ylabel("NAV (Rs)")
        ax.legend()
        ax.grid(alpha=0.3)
        fig.tight_layout()
        equity_png = BACKTEST_DIR / "cwa25s_daily_backtest_equity.png"
        fig.savefig(equity_png, dpi=120)
        print(f"Saved {equity_png.name}")


if __name__ == "__main__":
    main()
