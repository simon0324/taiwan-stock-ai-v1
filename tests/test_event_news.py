import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

import event_news as n

NOW = dt.datetime(2026, 9, 4, 15, tzinfo=n.TZ)
STOCK = {"code": "2330", "name": "台積電", "overall": 80}


class EventTests(unittest.TestCase):
    def test_categories_and_uncertainty(self):
        for text, category in (("台積電取得大型訂單", "訂單"), ("新廠正式量產", "新聞話題"),
                               ("市占率排名第一", "龍頭地位"), ("取得美國發明專利", "專利技術")):
            self.assertEqual(n.classification(text)[:2], (category, 1))
        for text in ("傳出台積電取得訂單", "公司澄清取得大型訂單", "預計取得發明專利", "申請發明專利", "龍頭股火紅"):
            self.assertEqual(n.classification(text)[1], 0)
        self.assertEqual(n.classification("公告取消客戶訂單")[1], -1)

    def test_caps_decay_and_unknown(self):
        one = n.event("2330", "取得大型訂單", NOW, "official", n.OFFICIAL, True, NOW)
        two = n.event("2330", "接獲大型訂單", NOW, "media", "https://www.cna.com.tw/", False, NOW)
        self.assertEqual(n.score(STOCK, [one], NOW)["score"], n.score(STOCK, [one, two], NOW)["score"])
        self.assertEqual(n.score(STOCK, [], NOW)["message_score"], 50)
        self.assertAlmostEqual(n.score(STOCK, [one], NOW + dt.timedelta(days=7))["message_score"], 56.25)
        self.assertEqual(n.score(STOCK, [one], NOW + dt.timedelta(days=15))["message_score"], 50)
        negative = n.event("2330", "取消大型訂單", NOW, "official", n.OFFICIAL, True, NOW)
        self.assertLess(n.score(STOCK, [negative], NOW)["message_score"], 50)

    def test_merge_no_future_or_duplicates(self):
        first = n.event("2330", "取得大型訂單", NOW, "official", n.OFFICIAL, True, NOW)
        future = n.event("2330", "取得新訂單", NOW + dt.timedelta(days=1), "official", n.OFFICIAL, True, NOW)
        result = n.merge_events([first], [first, future], NOW)
        self.assertEqual(result, [first])

    def test_snapshot_freezes_and_failed_sources_are_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            (docs / "results.json").write_text(json.dumps({"market_date": "2026-09-04", "stocks": [STOCK]}), encoding="utf-8")
            def fail(url):
                raise TimeoutError()
            payload = n.run(docs, NOW, fail)
            before = (docs / "news_signals.json").read_bytes()
            n.run(docs, NOW + dt.timedelta(minutes=2), fail)
            self.assertEqual(before, (docs / "news_signals.json").read_bytes())
            self.assertTrue(all(s["status"] == "failed" for s in payload["sources"]))
            n.run(docs, NOW + dt.timedelta(days=1), fail)
            self.assertEqual(before, (docs / "news_signals.json").read_bytes())
            self.assertIn("原版綜合", n.render(payload))

    def test_parsers_match_company_and_keep_dates(self):
        official = [{"公司代號": "2330", "發言日期": "1150904", "發言時間": "70003", "主旨 ": "取得大型訂單"}]
        rss = b'<rss><channel><item><title>2026 test</title><link>https://www.cna.com.tw/test</link><pubDate>Fri, 04 Sep 2026 12:00:00 +0800</pubDate></item></channel></rss>'
        def fake(url):
            return json.dumps(official).encode() if url == n.OFFICIAL else rss
        events, health = n.collect([STOCK], NOW, fake)
        self.assertEqual(len(events), 1)
        self.assertTrue(all(r["status"] == "ok" for r in health))
        self.assertIn("07:00:03", events[0]["published_at"])


if __name__ == "__main__":
    unittest.main()
