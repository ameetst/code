"""
Patch: copy EMA200-HOLD script, change EMA slow window to 100, update output dir.
"""
src  = r"run_2026-04-18_ema200-hold\etf_backtest_ema200.py"
dst  = r"run_2026-04-18_ema100-hold\etf_backtest_ema100.py"

content = open(src, encoding="utf-8").read()
content = content.replace("TREND_SLOW_EMA   = 200", "TREND_SLOW_EMA   = 100")
content = content.replace(
    r"run_2026-04-18_ema200-hold",
    r"run_2026-04-18_ema100-hold"
)
content = content.replace(
    "New Approach (EMA200-HOLD",
    "New Approach (EMA100-HOLD"
)
content = content.replace(
    "New Approach: EMA200-HOLD",
    "New Approach: EMA100-HOLD"
)
content = content.replace(
    "NEW APPROACH: EMA200-HOLD",
    "NEW APPROACH: EMA100-HOLD"
)
content = content.replace(
    "EMA200 regime",
    "EMA100 regime"
)
content = content.replace(
    "EMA{CONFIG.TREND_SLOW_EMA} regime",
    "EMA{CONFIG.TREND_SLOW_EMA} regime"
)

open(dst, "w", encoding="utf-8").write(content)
print("Patched -> " + dst)
print("TREND_SLOW_EMA occurrences:", content.count("TREND_SLOW_EMA   = 100"))
