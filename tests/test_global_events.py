import datetime as dt
import tempfile
import unittest
from pathlib import Path
import global_events as g


class RadarTests(unittest.TestCase):
    def test_uncertainty_and_no_invented_company(self):
        channels, status = g.analyze('美國考慮提高關稅')
        self.assertEqual(channels[0]['category'], '關稅／貿易')
        self.assertEqual(status, '推測／待確認')
        self.assertEqual(g.analyze('球隊獲勝')[0], [])

    def test_dedup_expiry_failure_and_no_scores(self):
        now = dt.datetime(2026,9,5,12,tzinfo=g.TZ)
        rss = '<rss><channel><item><title>美國提高關稅</title><link>https://www.cna.com.tw/news/test.aspx</link><pubDate>Sat, 05 Sep 2026 09:00:00 +0800</pubDate></item></channel></rss>'.encode()
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            p = g.run(docs, now, lambda _: rss)
            self.assertEqual(len(p['events']), 1)
            self.assertEqual(p['events'][0]['beneficiary_stocks'], [])
            self.assertNotIn('score', p['events'][0])
            def fail(_):
                raise TimeoutError()
            q = g.run(docs, now, fail)
            self.assertEqual(p['events'], q['events'])
            self.assertEqual(g.run(docs, now + dt.timedelta(days=8), fail)['events'], [])
