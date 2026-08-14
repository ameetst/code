"""
Unit tests for the Active Holdings exit-rule evaluation added to
etf_momentum_ranking.py (compute_holding_peak, evaluate_holdings_exit_rules)
and reused via emr.should_exit().

Run:  python test_exit_rules.py
"""

import datetime
import unittest

import numpy as np
import pandas as pd

import etf_momentum_ranking as emr


def make_prices(ticker: str, dates: list[str], values: list[float]) -> pd.DataFrame:
    idx = pd.to_datetime(dates)
    return pd.DataFrame({ticker: values}, index=idx)


def make_ranking_row(ticker: str, pct_from_high: float, inv_rank: int) -> pd.DataFrame:
    return pd.DataFrame([{
        "TICKER": ticker,
        "ETF_NAME": f"{ticker} Fund",
        "PCT_FROM_HIGH": pct_from_high,
        "RANK_INVESTABLE": inv_rank,
    }])


class TestComputeHoldingPeak(unittest.TestCase):

    def test_peak_is_max_price_since_entry(self):
        prices = make_prices(
            "ABC",
            ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"],
            [100, 120, 90, 95],
        )
        peak = emr.compute_holding_peak(
            "ABC", datetime.date(2026, 1, 1), prices, current_price=95.0
        )
        self.assertEqual(peak, 120.0)

    def test_peak_ignores_prices_before_entry(self):
        prices = make_prices(
            "ABC",
            ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"],
            [500, 100, 105, 102],   # 500 is a pre-entry spike that must not count
        )
        peak = emr.compute_holding_peak(
            "ABC", datetime.date(2026, 1, 2), prices, current_price=102.0
        )
        self.assertEqual(peak, 105.0)

    def test_falls_back_to_current_price_when_no_history(self):
        prices = make_prices("ABC", ["2026-01-01"], [100])
        peak = emr.compute_holding_peak(
            "XYZ",  # ticker not in prices_df at all
            datetime.date(2026, 1, 1), prices, current_price=42.0
        )
        self.assertEqual(peak, 42.0)

    def test_falls_back_when_first_buy_date_is_none(self):
        prices = make_prices("ABC", ["2026-01-01"], [100])
        peak = emr.compute_holding_peak("ABC", None, prices, current_price=42.0)
        self.assertEqual(peak, 42.0)

    def test_current_price_wins_when_higher_than_history(self):
        # e.g. a live-refreshed price that exceeds the historical max in `prices`
        prices = make_prices(
            "ABC", ["2026-01-01", "2026-01-02"], [100, 105]
        )
        peak = emr.compute_holding_peak(
            "ABC", datetime.date(2026, 1, 1), prices, current_price=130.0
        )
        self.assertEqual(peak, 130.0)


class TestEvaluateHoldingsExitRules(unittest.TestCase):

    def setUp(self):
        # Neutral CONFIG thresholds so each test controls exactly one trigger
        self._orig_tsl = emr.CONFIG.TSL_THRESHOLD
        self._orig_dd = emr.CONFIG.EXIT_MAX_DD_FROM_HIGH
        self._orig_rank = emr.CONFIG.EXIT_MAX_RANK
        emr.CONFIG.TSL_THRESHOLD = 0.05
        emr.CONFIG.EXIT_MAX_DD_FROM_HIGH = 0.25
        emr.CONFIG.EXIT_MAX_RANK = 20

    def tearDown(self):
        emr.CONFIG.TSL_THRESHOLD = self._orig_tsl
        emr.CONFIG.EXIT_MAX_DD_FROM_HIGH = self._orig_dd
        emr.CONFIG.EXIT_MAX_RANK = self._orig_rank

    def _holding(self, ticker="ABC", current_price=100.0, first_buy_date=None):
        return {
            "Ticker": ticker,
            "Qty": 10,
            "Avg Price": 90.0,
            "Current Price": current_price,
            "Cost Value": 900.0,
            "Market Value": current_price * 10,
            "Unrealized PnL": (current_price - 90.0) * 10,
            "Unrealized PnL %": (current_price - 90.0) / 90.0 * 100,
            "First Buy Date": first_buy_date or datetime.date(2026, 1, 1),
        }

    def test_no_breach_when_within_all_thresholds(self):
        prices = make_prices(
            "ABC", ["2026-01-01", "2026-01-02"], [100, 100]
        )
        ranking = make_ranking_row("ABC", pct_from_high=5.0, inv_rank=3)
        holdings = [self._holding(current_price=100.0)]

        result = emr.evaluate_holdings_exit_rules(holdings, ranking, prices)

        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]["Exit Flag"])
        self.assertEqual(result[0]["Exit Reason"], "OK")

    def test_flags_52wk_high_drawdown_breach(self):
        prices = make_prices("ABC", ["2026-01-01"], [100])
        # 30% away from 52wk high > 25% threshold
        ranking = make_ranking_row("ABC", pct_from_high=30.0, inv_rank=3)
        holdings = [self._holding(current_price=100.0)]

        result = emr.evaluate_holdings_exit_rules(holdings, ranking, prices)

        self.assertTrue(result[0]["Exit Flag"])
        self.assertIn("52wk high DD", result[0]["Exit Reason"])

    def test_flags_rank_degradation_breach(self):
        prices = make_prices("ABC", ["2026-01-01"], [100])
        ranking = make_ranking_row("ABC", pct_from_high=5.0, inv_rank=25)  # > 20
        holdings = [self._holding(current_price=100.0)]

        result = emr.evaluate_holdings_exit_rules(holdings, ranking, prices)

        self.assertTrue(result[0]["Exit Flag"])
        self.assertIn("Rank", result[0]["Exit Reason"])

    def test_flags_tsl_breach_using_peak_since_entry(self):
        # Peak of 100 on day 1, current price has fallen 10% -> breaches 5% TSL
        prices = make_prices(
            "ABC", ["2026-01-01", "2026-01-02"], [100, 90]
        )
        ranking = make_ranking_row("ABC", pct_from_high=5.0, inv_rank=3)
        holdings = [self._holding(current_price=90.0,
                                   first_buy_date=datetime.date(2026, 1, 1))]

        result = emr.evaluate_holdings_exit_rules(holdings, ranking, prices)

        self.assertTrue(result[0]["Exit Flag"])
        self.assertIn("TSL", result[0]["Exit Reason"])
        self.assertEqual(result[0]["Peak Price"], 100.0)

    def test_flags_ticker_no_longer_in_ranking_universe(self):
        prices = make_prices("ABC", ["2026-01-01"], [100])
        ranking = make_ranking_row("OTHER", pct_from_high=5.0, inv_rank=3)  # ABC absent
        holdings = [self._holding(ticker="ABC", current_price=100.0)]

        result = emr.evaluate_holdings_exit_rules(holdings, ranking, prices)

        self.assertTrue(result[0]["Exit Flag"])
        self.assertIn("no longer in ranking universe", result[0]["Exit Reason"])

    def test_multiple_reasons_are_joined(self):
        prices = make_prices("ABC", ["2026-01-01"], [100])
        ranking = make_ranking_row("ABC", pct_from_high=30.0, inv_rank=25)
        holdings = [self._holding(current_price=100.0)]

        result = emr.evaluate_holdings_exit_rules(holdings, ranking, prices)

        self.assertTrue(result[0]["Exit Flag"])
        self.assertIn("52wk high DD", result[0]["Exit Reason"])
        self.assertIn("Rank", result[0]["Exit Reason"])
        self.assertIn("|", result[0]["Exit Reason"])

    def test_original_holding_fields_are_preserved(self):
        prices = make_prices("ABC", ["2026-01-01"], [100])
        ranking = make_ranking_row("ABC", pct_from_high=5.0, inv_rank=3)
        holdings = [self._holding(current_price=100.0)]

        result = emr.evaluate_holdings_exit_rules(holdings, ranking, prices)

        for key in holdings[0]:
            self.assertIn(key, result[0])
            self.assertEqual(result[0][key], holdings[0][key])

    def test_multiple_holdings_evaluated_independently(self):
        prices = pd.DataFrame(
            {"ABC": [100, 100], "XYZ": [50, 50]},
            index=pd.to_datetime(["2026-01-01", "2026-01-02"]),
        )
        ranking = pd.concat([
            make_ranking_row("ABC", pct_from_high=5.0, inv_rank=3),
            make_ranking_row("XYZ", pct_from_high=40.0, inv_rank=3),
        ], ignore_index=True)
        holdings = [
            self._holding(ticker="ABC", current_price=100.0),
            self._holding(ticker="XYZ", current_price=50.0),
        ]

        result = emr.evaluate_holdings_exit_rules(holdings, ranking, prices)

        by_ticker = {r["Ticker"]: r for r in result}
        self.assertEqual(by_ticker["ABC"]["Exit Reason"], "OK")
        self.assertTrue(by_ticker["XYZ"]["Exit Flag"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
