import unittest

import kline_forecast as k


class KlineForecastTests(unittest.TestCase):
    def test_quantile_and_probability_are_bounded(self):
        training = [((0, 0, 1, 50, 2), value) for value in (-2, -1, 1, 3)]
        result = k.estimate((0, 0, 1, 50, 2), training)
        self.assertEqual(result["probability_up"], 50)
        self.assertLessEqual(result["low_return_pct"], result["high_return_pct"])

    def test_short_history_is_low_confidence(self):
        self.assertTrue(k.confidence({"unique_market_days": 32, "5d_samples": 5000}).startswith("低"))

    def test_short_history_shrinks_probability_to_neutral(self):
        forecast = {"probability_up": 80.0, "expected_return_pct": 5.0}
        k.apply_history_shrinkage(forecast, 30)
        self.assertLess(forecast["probability_up"], 52)
        self.assertEqual(forecast["raw_probability_up"], 80)

    def test_quality_gate_rejects_loss_making_candidate(self):
        history = []
        for day in range(30):
            history.append([{"code": "1111", "date": f"2026-08-{day+1:02d}", "close": 100+day,
                             "high": 101+day, "low": 99+day, "value": 100000000}])
        results = {"stocks": [{"code": "1111", "name": "測試", "price": 129, "eps": -1,
                                "revenue": 50, "overall": 80}]}
        self.assertEqual(k.build(results, history)[0], [])


if __name__ == "__main__":
    unittest.main()
