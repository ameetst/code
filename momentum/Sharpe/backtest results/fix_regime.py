with open('Sharpe.py', 'r', encoding='utf-8') as f:
    content = f.read()
content = content.replace(
    'f"Regime: {regime_flag}")',
    'f"Regime Score: {regime_score:.2f}")')
content = content.replace(
    'color="FF2222" if "NOT BUY" in regime_flag else "1A365D"',
    'color="FF2222" if not allow_new else "1A365D"')
content = content.replace(
    'fill("2A0000") if "NOT BUY" in regime_flag else fill("F0F4F8")',
    'fill("2A0000") if not allow_new else fill("F0F4F8")')
with open('Sharpe.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Fixed all regime_flag references')
