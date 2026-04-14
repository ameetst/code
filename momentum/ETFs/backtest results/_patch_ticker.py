FILE = r"c:\Users\ameet\Documents\Github\code\momentum\ETFs\backtest results\etf_backtest.py"
with open(FILE, "r", encoding="utf-8") as f:
    content = f.read()
content = content.replace("^CNX500", "^CRSLDX")
with open(FILE, "w", encoding="utf-8") as f:
    f.write(content)
print("Patched: ^CNX500 -> ^CRSLDX")
