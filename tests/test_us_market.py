import datetime as dt
import unittest
from unittest.mock import patch

import us_market as u


class UsMarketTests(unittest.TestCase):
    def test_rank_is_neutral_when_required_source_missing(self):
        stocks = [{"code": "2330", "name": "台積電", "overall": 80}]
        row = u.rank(stocks, {}, False)[0]
        self.assertEqual(row["weighted_score"], 80)
        self.assertEqual(row["us_adjustment"], 0)

    def test_market_and_semiconductor_weights_are_bounded(self):
        q = {k: {"return_1d": 99, "date": "2026-09-04"} for k in ("sp500", "nasdaq", "sox", "vix", "tsm")}
        q["vix"]["return_1d"] = -99
        stocks = [{"code": "2330", "name": "台積電", "overall": 80}, {"code": "1101", "name": "台泥", "overall": 80}]
        rows = {r["code"]: r for r in u.rank(stocks, q, True)}
        self.assertEqual(rows["2330"]["us_adjustment"], 5)
        self.assertEqual(rows["1101"]["us_adjustment"], 3.5)
        self.assertIsNotNone(rows["2330"]["semiconductor_evidence"])
        self.assertIsNone(rows["1101"]["semiconductor_evidence"])

    def test_collect_disables_scoring_on_mixed_dates(self):
        def fake(symbol, now):
            day = "2026-09-03" if symbol == "^VIX" else "2026-09-04"
            return {"symbol": symbol, "date": day, "close": 1, "return_1d": 0, "return_5d": 0, "url": "x"}
        with patch.object(u, "fetch_chart", side_effect=fake):
            _, _, healthy = u.collect(dt.datetime(2026, 9, 5, tzinfo=u.TZ))
        self.assertFalse(healthy)

    def test_collect_disables_stale_data(self):
        def fake(symbol, now):
            return {"symbol": symbol, "date": "2026-08-20", "close": 1, "return_1d": 0, "return_5d": 0, "url": "x"}
        with patch.object(u, "fetch_chart", side_effect=fake):
            _, _, healthy = u.collect(dt.datetime(2026, 9, 5, tzinfo=u.TZ))
        self.assertFalse(healthy)


if __name__ == "__main__":
    unittest.main()
