content = open("etf_momentum_ranking.py", encoding="utf-8").read()

# Replace unicode arrows/symbols with ASCII equivalents
replacements = [
    ("\u2191 ADD", "^ ADD"),
    ("\u2193 TRIM", "v TRIM"),
    ("\u2691 REGIME", "! REGIME"),
    ("\u2192", "->"),
    ("\u2190", "<-"),
]
for old, new in replacements:
    content = content.replace(old, new)

open("etf_momentum_ranking.py", "w", encoding="utf-8").write(content)
print("Done - all unicode symbols replaced with ASCII equivalents")
