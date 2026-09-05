import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class DataFallbackTests(unittest.TestCase):
    def setUp(self):
        app.DATA_WARNINGS.clear()

    def test_revenue_uses_previous_value_when_source_is_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            docs = Path(folder)
            (docs / "results.json").write_text(
                json.dumps({"market_date": "2026-09-04", "stocks": [{"code": "2330", "revenue": 12.5}]}),
                encoding="utf-8",
            )
            with patch.object(app, "DOCS", docs), patch.object(app, "get_json", side_effect=RuntimeError("empty")):
                self.assertEqual(app.fetch_revenue(), {"2330": 12.5})
        self.assertIn("2026-09-04", app.DATA_WARNINGS[0])

    def test_revenue_stops_when_source_and_previous_data_are_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(app, "DOCS", Path(folder)), patch.object(app, "get_json", side_effect=RuntimeError("empty")):
                with self.assertRaisesRegex(RuntimeError, "沒有上次資料可備援"):
                    app.fetch_revenue()

    def test_revenue_rejects_empty_payload(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(app, "DOCS", Path(folder)), patch.object(app, "get_json", return_value=[]):
                with self.assertRaisesRegex(RuntimeError, "沒有上次資料可備援"):
                    app.fetch_revenue()


if __name__ == "__main__":
    unittest.main()
