import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import unified_rank as u

NOW = dt.datetime(2026, 9, 4, 15, tzinfo=u.TZ)


class UnifiedTests(unittest.TestCase):
    def test_combines_weights_and_removes_embedded_news(self):
        # 77 = 80 × 95% + 消息次數分 20 × 5%。
        stock = {"code": "2330", "name": "台積電", "overall": 77, "news": 1}
        news = [{"code": "2330", "message_score": 60, "events": []}]
        international = [{"code": "2330", "event_score": 50, "events": []}]
        us = [{"code": "2330", "us_score": 70, "components": []}]
        row = u.combine([stock], news, international, us, True)[0]
        self.assertAlmostEqual(row["base_score_without_news"], 80)
        self.assertAlmostEqual(row["unified_score"], 75.5)

    def test_missing_component_has_no_score(self):
        stock = {"code": "2330", "name": "台積電", "overall": 80, "news": 0}
        row = u.combine([stock], [], [], [], True)[0]
        self.assertIsNone(row["unified_score"])
        self.assertEqual(len(row["missing"]), 3)

    def test_health_rejects_degraded_or_mismatched_sources(self):
        source = {"market_date": "2026-09-04", "data_warnings": []}
        news = {"market_date": "2026-09-04", "as_of": NOW.isoformat(), "sources": [{"status": "ok"}]}
        international = {"market_date": "2026-09-04", "as_of": NOW.isoformat(), "source_status": "正常"}
        us = {"market_date": "2026-09-04", "as_of": NOW.isoformat(), "source_status": "正常"}
        self.assertTrue(u.health(source, news, international, us, NOW)[0])
        us["source_status"] = "缺漏"
        self.assertFalse(u.health(source, news, international, us, NOW)[0])

    def test_snapshot_is_not_duplicated(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)
            stock = {"code": "2330", "name": "台積電", "overall": 80, "news": 0}
            files = {
                "results.json": {"market_date": "2026-09-04", "data_warnings": [], "stocks": [stock]},
                "news_results.json": {"market_date": "2026-09-04", "as_of": NOW.isoformat(), "sources": [{"status": "ok"}], "ranking": [{"code": "2330", "message_score": 50, "events": []}]},
                "international_rank.json": {"market_date": "2026-09-04", "as_of": NOW.isoformat(), "source_status": "正常", "ranking": [{"code": "2330", "event_score": 50, "events": []}]},
                "us_market.json": {"market_date": "2026-09-04", "as_of": NOW.isoformat(), "source_status": "正常", "ranking": [{"code": "2330", "us_score": 50, "components": []}]},
                "market_history.json": [],
            }
            for name, value in files.items():
                (p / name).write_text(json.dumps(value), encoding="utf-8")
            u.run(p, NOW)
            u.run(p, NOW + dt.timedelta(minutes=1))
            self.assertEqual(len(json.loads((p / "unified_signals.json").read_text())), 1)


if __name__ == "__main__":
    unittest.main()
