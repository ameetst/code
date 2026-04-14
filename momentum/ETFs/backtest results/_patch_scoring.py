FILE = r"c:\Users\ameet\Documents\Github\code\momentum\ETFs\etf_backtest.py"
with open(FILE, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Patch the scoring loop
OLD_SCORE = '''        # ── Scoring & selection ───────────────────────────────
        rank_list = []
        for _, m_row in meta.iterrows():
            t = m_row["TICKER"]
            if t not in hist_prices.columns: continue
            s = hist_prices[t].dropna()
            if len(s) < CONFIG.WINDOW_3M: continue
            sh3 = sharpe_score(s, CONFIG.WINDOW_3M)
            sh6 = sharpe_score(s, CONFIG.WINDOW_6M)
            if pd.isna(sh6): sh6 = 0.0
            w_sharpe = 0.5 * sh6 + 0.5 * sh3
            high_52  = s.tail(252).max()
            close_p  = s.iloc[-1]
            if (high_52 - close_p) / high_52 <= CONFIG.MAX_DRAWDOWN_FROM_HIGH:
                rank_list.append({'TICKER': t, 'NAME': m_row['ETF_NAME'],
                                  'WT_SHARPE': w_sharpe,
                                  'SECTOR': classify_sector(m_row['ETF_NAME'], t)})

        rank_df  = pd.DataFrame(rank_list).sort_values('WT_SHARPE', ascending=False)'''

NEW_SCORE = '''        # ── Scoring & selection ───────────────────────────────
        rank_list = []
        for _, m_row in meta.iterrows():
            t = m_row["TICKER"]
            if t not in hist_prices.columns: continue
            s = hist_prices[t].dropna()
            if len(s) < CONFIG.WINDOW_3M: continue

            sh3 = sharpe_score(s, CONFIG.WINDOW_3M)
            sh6 = sharpe_score(s, CONFIG.WINDOW_6M)
            r3  = r2_score(s, CONFIG.WINDOW_3M)
            r6  = r2_score(s, CONFIG.WINDOW_6M)

            _sh6 = 0.0 if pd.isna(sh6) else sh6
            _sh3 = 0.0 if pd.isna(sh3) else sh3
            _r6  = 0.0 if (r6 is None or pd.isna(r6)) else r6
            _r3  = 0.0 if (r3 is None or pd.isna(r3)) else r3

            w_sharpe  = 0.5 * _sh6 + 0.5 * _sh3
            sr2_blend = ((_sh6 * _r6) + (_sh3 * _r3)) / 2.0

            high_52 = s.tail(252).max()
            close_p = s.iloc[-1]
            if (high_52 - close_p) / high_52 <= CONFIG.MAX_DRAWDOWN_FROM_HIGH:
                rank_list.append({
                    'TICKER'   : t,
                    'NAME'     : m_row['ETF_NAME'],
                    'WT_SHARPE': w_sharpe,
                    'SR2_BLEND': sr2_blend,
                    'SECTOR'   : classify_sector(m_row['ETF_NAME'], t),
                })

        rank_df  = pd.DataFrame(rank_list).sort_values(ranking_metric, ascending=False)'''

# 2. Patch the main block and add return value + comparison runner
OLD_CHART = '''    # Chart
    plt.figure(figsize=(14, 7))
    plt.plot(res['equity'], label=f"Strategy ({CONFIG.TSL_THRESHOLD*100:.0f}pct TSL, Monthly)", linewidth=2, color="steelblue")
    if bench_ok:
        plt.plot(bench, label="Benchmark (Nifty 500)", linewidth=1.5, alpha=0.8, color="darkorange")
    plt.fill_between(res.index, res['equity'], CONFIG.START_CAPITAL, alpha=0.07, color="steelblue")
    plt.title("ETF Momentum Strategy Backtest  |  INR 10L  |  Monthly Rebalance  |  10pct TSL")
    plt.ylabel("Equity Value (INR)")
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(CONFIG.CHART_FILE, dpi=150)
    print(f"\\nEquity curve saved -> {CONFIG.CHART_FILE}")


if __name__ == "__main__":
    run_backtest()'''

NEW_CHART = '''    return {
        "label"   : ranking_metric,
        "equity"  : res["equity"],
        "cagr"    : cagr,
        "max_dd"  : max_dd,
        "vol"     : vol,
        "sharpe"  : sharpe,
        "bench"   : bench if bench_ok else None,
        "b_cagr"  : b_cagr if bench_ok else None,
        "b_dd"    : b_dd   if bench_ok else None,
        "b_sharpe": b_sharpe if bench_ok else None,
    }


# =========================================================
# COMPARISON RUNNER
# =========================================================
if __name__ == "__main__":
    COLORS = {"WT_SHARPE": "steelblue", "SR2_BLEND": "mediumseagreen"}
    LABELS = {"WT_SHARPE": "Wtd Sharpe", "SR2_BLEND": "SR2 Blend"}

    results = {}
    for metric in ["WT_SHARPE", "SR2_BLEND"]:
        print(f"\\n{'='*55}")
        print(f"  Running backtest with ranking metric: {metric}")
        print(f"{'='*55}")
        results[metric] = run_backtest(ranking_metric=metric)

    # Side-by-side equity curve
    fig, ax = plt.subplots(figsize=(15, 7))

    bench_plotted = False
    for metric, res in results.items():
        ax.plot(res["equity"], label=f"Strategy ({LABELS[metric]})",
                linewidth=2, color=COLORS[metric])
        if not bench_plotted and res["bench"] is not None:
            ax.plot(res["bench"], label="Benchmark (Nifty 500)",
                    linewidth=1.5, alpha=0.7, color="darkorange", linestyle="--")
            bench_plotted = True

    ax.axhline(CONFIG.START_CAPITAL, color="grey", linewidth=0.8, linestyle=":")
    ax.set_title("ETF Momentum Strategy: Wtd Sharpe vs SR2 Blend  |  10pct TSL  |  Monthly Rebalance")
    ax.set_ylabel("Equity Value (INR)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(CONFIG.CHART_FILE, dpi=150)
    print(f"\\nComparison chart saved -> {CONFIG.CHART_FILE}")

    # Print side-by-side summary
    print(f"\\n{'='*60}")
    print(f"  {'Metric':<22} {'Wtd Sharpe':>14} {'SR2 Blend':>14}")
    print(f"  {'-'*50}")
    for key, label in [("cagr","CAGR"), ("max_dd","Max Drawdown"),
                        ("vol","Annual Vol"), ("sharpe","Sharpe Ratio")]:
        ws = results["WT_SHARPE"][key]
        sr = results["SR2_BLEND"][key]
        if key in ("cagr","max_dd","vol"):
            print(f"  {label:<22} {ws:>13.2%} {sr:>13.2%}")
        else:
            print(f"  {label:<22} {ws:>13.2f} {sr:>13.2f}")
    bm = results["WT_SHARPE"]
    if bm["b_cagr"] is not None:
        print(f"  {'-'*50}")
        print(f"  {'Benchmark CAGR':<22} {bm['b_cagr']:>13.2%} {'(same)':>14}")
        print(f"  {'Benchmark Max DD':<22} {bm['b_dd']:>13.2%} {'(same)':>14}")
        print(f"  {'Benchmark Sharpe':<22} {bm['b_sharpe']:>13.2f} {'(same)':>14}")
    print(f"{'='*60}")'''

# Apply patches
count = 0
if OLD_SCORE in content:
    content = content.replace(OLD_SCORE, NEW_SCORE)
    count += 1
    print("Patch 1 (scoring loop): OK")
else:
    print("Patch 1 (scoring loop): NOT FOUND - check manually")

if OLD_CHART in content:
    content = content.replace(OLD_CHART, NEW_CHART)
    count += 1
    print("Patch 2 (chart + main): OK")
else:
    print("Patch 2 (chart + main): NOT FOUND - check manually")

if count > 0:
    with open(FILE, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"\\nSaved {count}/2 patches to {FILE}")
