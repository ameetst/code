# ==============================
# NSE500 Weekly Strategy Backtest Engine (V3 - Optimized)
# ==============================

import pandas as pd
import numpy as np

# ------------------------------
# CONFIG
# ------------------------------
PRICE_FILE = "prices.xlsx"
INITIAL_CAPITAL = 1_000_000
MAX_POSITIONS = 5
STOP_LOSS = -0.03
MIN_PRICE_FILTER = 50

# ------------------------------
# LOAD DATA
# ------------------------------

def load_wide_excel(path):
    df = pd.read_excel(path)
    df = df.set_index(df.columns[0])
    df.columns = pd.to_datetime(df.columns)
    return df

price_wide = load_wide_excel(PRICE_FILE)

# ------------------------------
# CLEAN DATA
# ------------------------------

mask_valid_days = (price_wide != 0).sum(axis=0) > (0.05 * price_wide.shape[0])
price_wide = price_wide.loc[:, mask_valid_days]

price_wide = price_wide.replace(0, np.nan)
price_wide = price_wide.ffill(axis=1)

# ------------------------------
# TRANSFORM TO LONG
# ------------------------------

price = price_wide.stack().reset_index()
price.columns = ["Ticker", "Date", "Close"]
price = price.sort_values(["Ticker", "Date"])

# ------------------------------
# FEATURE ENGINEERING
# ------------------------------

def compute_features(df):
    df["ret_60"] = df.groupby("Ticker")["Close"].pct_change(60)
    df["ret_120"] = df.groupby("Ticker")["Close"].pct_change(120)

    df["dma_100"] = df.groupby("Ticker")["Close"].rolling(100).mean().reset_index(0, drop=True)
    df["dma_200"] = df.groupby("Ticker")["Close"].rolling(200).mean().reset_index(0, drop=True)

    return df

price = compute_features(price)

# ------------------------------
# MARKET FILTER (NIFTY500)
# ------------------------------

index_df = price[price["Ticker"] == "NIFTY500"].copy()
index_df["dma_200"] = index_df["Close"].rolling(200).mean()
market_trend = dict(zip(index_df["Date"], index_df["Close"] > index_df["dma_200"]))

# ------------------------------
# STRATEGY LOGIC
# ------------------------------

portfolio = {}
cash = INITIAL_CAPITAL
equity_curve = []
trade_log = []

all_dates = sorted(price["Date"].unique())

for i in range(200, len(all_dates)-1):
    date = all_dates[i]
    next_date = all_dates[i+1]

    # Market regime check
    if not market_trend.get(date, False):
        portfolio.clear()
        equity_curve.append((date, cash))
        continue

    daily = price[price["Date"] == date].copy()

    # Stock-level trend filter (relaxed for earlier entry)
    daily = daily[(daily["Close"] > MIN_PRICE_FILTER) & (daily["Close"] > daily["dma_100"])]

    # Score (strong momentum bias)
    daily["score"] = 0.7*daily["ret_60"] + 0.3*daily["ret_120"]
    daily = daily.dropna(subset=["score"])

    candidates = daily.sort_values("score", ascending=False).head(10)

    # EXIT LOGIC (rotation)
    for ticker in list(portfolio.keys()):
        row = daily[daily["Ticker"] == ticker]
        if row.empty:
            continue

        current_price = row["Close"].values[0]
        entry_price = portfolio[ticker]["entry_price"]
        quantity = portfolio[ticker]["quantity"]

        ret = (current_price / entry_price) - 1

        # Stop-loss
        if ret < STOP_LOSS:
            pnl = (current_price - entry_price) * quantity
            cash += pnl
            trade_log.append((ticker, portfolio[ticker]["entry_date"], date, entry_price, current_price, pnl))
            del portfolio[ticker]

    # ROTATION: replace worst holding if better candidate exists
    portfolio_scores = {}
    for ticker in portfolio:
        row = daily[daily["Ticker"] == ticker]
        if not row.empty:
            portfolio_scores[ticker] = row["score"].values[0]

    for _, row in candidates.iterrows():
        ticker = row["Ticker"]
        score = row["score"]

        if len(portfolio) < MAX_POSITIONS:
            # New position
            allocation = cash / (MAX_POSITIONS - len(portfolio))
            quantity = allocation // row["Close"]
            if quantity <= 0:
                continue
            portfolio[ticker] = {"entry_price": row["Close"], "entry_date": date, "quantity": quantity}
            cash -= quantity * row["Close"]
        else:
            # Rotation logic
            min_ticker = min(portfolio_scores, key=portfolio_scores.get) if portfolio_scores else None
            if min_ticker and score > portfolio_scores[min_ticker]:
                # Exit worst
                row_exit = daily[daily["Ticker"] == min_ticker]
                exit_price = row_exit["Close"].values[0] if not row_exit.empty else portfolio[min_ticker]["entry_price"]
                pnl = (exit_price - portfolio[min_ticker]["entry_price"]) * portfolio[min_ticker]["quantity"]
                cash += exit_price * portfolio[min_ticker]["quantity"]
                trade_log.append((min_ticker, portfolio[min_ticker]["entry_date"], date, portfolio[min_ticker]["entry_price"], exit_price, pnl))
                del portfolio[min_ticker]

                # Enter new
                allocation = cash / 1  # only one new
                quantity = allocation // row["Close"]
                if quantity <= 0:
                    continue
                portfolio[ticker] = {"entry_price": row["Close"], "entry_date": date, "quantity": quantity}
                cash -= quantity * row["Close"]
                portfolio_scores[ticker] = score

    # MARK TO MARKET
    mtm_value = sum([price[(price["Ticker"] == t) & (price["Date"] == date)]["Close"].values[0] * portfolio[t]["quantity"] for t in portfolio if not price[(price["Ticker"] == t) & (price["Date"] == date)].empty])
    total_value = cash + mtm_value
    equity_curve.append((date, total_value))

# ------------------------------
# OUTPUT
# ------------------------------

equity_df = pd.DataFrame(equity_curve, columns=["Date", "PortfolioValue"])
equity_df.to_csv("equity_curve_v3.csv", index=False)

trade_df = pd.DataFrame(trade_log, columns=["Ticker", "EntryDate", "ExitDate", "EntryPrice", "ExitPrice", "PnL"])
trade_df.to_csv("trade_log_v3.csv", index=False)

print("Backtest V3 complete. Files generated:")
print("- equity_curve_v3.csv")
print("- trade_log_v3.csv")

# ==============================
# END
# ==============================
