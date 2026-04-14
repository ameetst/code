"""Patch script: replaces the benchmark/plotting section in etf_backtest.py"""
import re

FILE = r"c:\Users\ameet\Documents\Github\code\momentum\ETFs\backtest results\etf_backtest.py"

with open(FILE, "r", encoding="utf-8") as f:
    content = f.read()

# Find and replace everything from '# Plotting' to the last print before 'if __name__'
new_tail = '''    # Fetch benchmark from yfinance directly
    print("\\nFetching benchmark (^CRSLDX / Nifty 500)...")
    bench_ok = False
    try:
        bench_raw = yf.download(
            "^CRSLDX",
            start=res.index[0].strftime("%Y-%m-%d"),
            end=res.index[-1].strftime("%Y-%m-%d"),
            auto_adjust=True,
            progress=False,
        )
        bench = bench_raw["Close"].squeeze().dropna()
        bench.index = pd.to_datetime(bench.index).tz_localize(None)
        bench = bench.reindex(res.index, method="ffill")
        bench = (bench / bench.iloc[0]) * CONFIG.START_CAPITAL
        b_final  = bench.iloc[-1]
        b_cagr   = (b_final / initial) ** (1 / years) - 1
        b_ret    = bench.pct_change().dropna()
        b_dd     = ((bench - bench.cummax()) / bench.cummax()).min()
        b_vol    = b_ret.std() * np.sqrt(252)
        b_sharpe = (b_cagr / b_vol) if b_vol > 0 else 0
        print(f"Benchmark CAGR:     {b_cagr:.2%}")
        print(f"Benchmark Max DD:   {b_dd:.2%}")
        print(f"Benchmark Sharpe:   {b_sharpe:.2f}")
        bench_ok = True
    except Exception as e:
        print(f"  [WARN] Could not fetch benchmark: {e}")

    plt.figure(figsize=(12, 7))
    plt.plot(res["equity"], label="Strategy (Monthly Momentum)", linewidth=2, color="steelblue")
    if bench_ok:
        plt.plot(bench, label="Benchmark (Nifty 500 ^^CRSLDX)", linewidth=1.5, alpha=0.8, color="darkorange")
    plt.title("ETF Momentum Strategy Backtest  |  INR 10L Start  |  5pct TSL  |  Monthly Rebalance")
    plt.ylabel("Equity Value (INR)")
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig("equity_curve.png", dpi=150)
    print("\\nEquity curve saved as equity_curve.png")

if __name__ == "__main__":
    run_backtest()
'''

# Replace from '    # Plotting' onwards
pattern = r'    # Plotting.*'
idx = content.find("    # Plotting")
if idx == -1:
    print("ERROR: Could not find '# Plotting' section")
else:
    content = content[:idx] + new_tail
    with open(FILE, "w", encoding="utf-8") as f:
        f.write(content)
    print("SUCCESS: Benchmark section patched.")
