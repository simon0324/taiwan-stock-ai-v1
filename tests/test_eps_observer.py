import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
import eps_observer as e


class ObserverTests(unittest.TestCase):
    def test_only_explicit_monthly_line(self):
        self.assertEqual(e.extract('單月EPS：-0.12元')["monthly_eps_candidate"], -.12)
        for text in ('每股盈餘 1.2 3.4 5.6', '累計EPS：2.5元', '單月EPS：1元\n單月EPS：2元'):
            self.assertIsNone(e.extract(text)["monthly_eps_candidate"])

    def test_archive_escape_failure_and_repeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            (docs / 'results.json').write_text(json.dumps({'stocks':[{'code':'2330'}]}))
            raw = [{'公司代號':'2330','公司名稱':'測試','主旨 ':'更正EPS','說明':'<script>x</script>\n單月EPS：1元'}]
            now = dt.datetime(2026,9,5,tzinfo=e.TZ)
            first = e.run(docs, lambda _: json.dumps(raw).encode(), now)
            self.assertEqual(len(first['records']), 1)
            second = e.run(docs, lambda _: json.dumps(raw).encode(), now)
            self.assertEqual(first, second)
            self.assertNotIn('<script>', e.render(first))
            def fail(_):
                raise TimeoutError()
            failed = e.run(docs, fail, now)
            self.assertEqual(first['records'], failed['records'])
            self.assertIn('失敗', failed['source_status'])
