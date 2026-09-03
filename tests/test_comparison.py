import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import backtest as bt
import compare_backtest as cb


def history(days=65):
    return [[{"code": str(1000 + c), "name": f"Test {c}",
              "date": (dt.date(2023, 1, 1) + dt.timedelta(days=i)).isoformat(),
              "close": 100 + i * .15, "high": 100 + i * .15 + .1,
              "low": 100 + i * .15 - .1, "value": 100_000_000, "volume": 1000}
             for c in range(7)] for i in range(days)]


class ComparisonTests(unittest.TestCase):
    def test_eps_overlay_and_future_cutoff(self):
        h = history()
        eps = {str(1000 + i): [{"available": "2022-12-01", "yoy": i * 10}] for i in range(7)}
        base = bt.ranks(h, 20, {}, eps, 0)
        treatment = bt.ranks(h, 20, {}, eps, .2)
        for i, (a, b) in enumerate(zip(base, treatment)):
            self.assertAlmostEqual(b["growth"], .8 * a["growth"] + .2 * (i + 1) / 7 * 100)
        future = {c: rows + [{"available": "2099-01-01", "yoy": -9999}] for c, rows in eps.items()}
        self.assertEqual(treatment, bt.ranks(h, 20, {}, future, .2))
        self.assertEqual(base, bt.ranks(h, 20, {}, future, 0))

    def test_no_fundamental_io_and_same_schedule(self):
        h = history()
        with patch.object(bt, "load_revenue_history", side_effect=AssertionError("unexpected I/O")), \
             patch.object(bt, "load_eps_history", side_effect=AssertionError("unexpected I/O")):
            result = cb.compare(h, {}, {})
        # Constant EPS shifts scores equally, so selection and trades are identical.
        self.assertEqual(result["revenue_only"], result["with_eps"])
        self.assertGreater(result["revenue_only"]["growth_5"]["trades"], 0)
        self.assertNotIn("validation", result["with_eps"]["growth_5"])

    def test_tie_break_does_not_depend_on_input_order(self):
        h = history()
        # Prices differ only after the first selection, making a changed tied pick visible.
        for day in h[22:]:
            day[-1]["close"] *= 1.03
        a = bt.run_backtest(h, {}, {}, 0, ("growth",))
        b = bt.run_backtest([list(reversed(d)) for d in h], {}, {}, 0, ("growth",))
        self.assertEqual(a, b)

    def test_cache_snapshot_and_missing_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def put(relative, value):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value), encoding="utf-8")
            put("docs/results.json", {"stocks": [{"code": "1000"}, {"code": "1001"}]})
            with self.assertRaisesRegex(RuntimeError, "快取"):
                cb.load_inputs(root)
            h = history(705)
            for i in range(2):
                code = str(1000 + i)
                put(f"work/finmind/{code}.json", [day[i] for day in h[:703 + i]])
                put(f"work/finmind_revenue/{code}.json", [])
                put(f"work/finmind_eps/{code}.json", [])
            first = cb.load_inputs(root)
            self.assertEqual(len(first[0]), 703)
            self.assertEqual(first[3]["end"], h[702][0]["date"])
            self.assertEqual(first[3]["empty_eps_codes"], ["1000", "1001"])
            put("work/finmind/1000.json", list(reversed([d[0] for d in h[:703]])))
            self.assertEqual(first[3]["input_sha256"], cb.load_inputs(root)[3]["input_sha256"])
            page = cb.render({"manifest": first[3], "results": cb.compare(history(), {}, {})})
            self.assertIn("不是獨立驗證", page)
            self.assertEqual(page.count("<tr>"), 7)


if __name__ == "__main__":
    unittest.main()
