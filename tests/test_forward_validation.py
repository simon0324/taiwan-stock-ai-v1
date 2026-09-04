import json
import tempfile
import unittest
from pathlib import Path

import forward_validation as fv


def candidates():
    return [{"code": str(1000 + i), "name": f"T{i}", "trend": 60 + i,
             "momentum": i, "volume_ratio": 1 + i / 10, "revenue": i * 2, "eps": 7 - i}
            for i in range(7)]


def market(date, close=100, low=99, high=101):
    return [{"code": str(1000 + i), "date": date, "close": close, "low": low, "high": high}
            for i in range(7)]


class ForwardTests(unittest.TestCase):
    def test_active_rerun_and_changed_ranking_are_frozen(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            fv.update(candidates(), "2026-01-02", market("2026-01-02"), docs)
            first = fv.update(candidates(), "2026-01-03", market("2026-01-03"), docs)
            changed = candidates()
            changed[0]["trend"] = 999
            second = fv.update(changed, "2026-01-03", market("2026-01-03"), docs)
            self.assertEqual(first, second)
            third = fv.update(candidates(), "2026-01-04", market("2026-01-04"), docs)
            fourth = fv.update(changed, "2026-01-04", market("2026-01-04"), docs)
            self.assertEqual(third, fourth)

    def test_legacy_preserved_and_corruption_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            initial = fv.update(candidates(), "2026-01-02", market("2026-01-02"), docs)
            initial["rules_version"] = 1
            path = docs / "forward_validation.json"
            path.write_text(json.dumps(initial), encoding="utf-8")
            new = fv.update(candidates(), "2026-01-03", market("2026-01-03"), docs)
            self.assertEqual(new["legacy_count"], 10)
            self.assertTrue(all(r["status"] == "waiting_entry" for r in new["records"][:10]))
            path.write_text('{broken', encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                fv.update(candidates(), "2026-01-04", market("2026-01-04"), docs)

    def test_signal_enters_next_day_and_exits_after_five_more(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            first = fv.update(candidates(), "2026-01-02", market("2026-01-02"), docs)
            self.assertEqual(len(first["records"]), 10)
            self.assertTrue(all(r["status"] == "waiting_entry" for r in first["records"]))
            fv.update(candidates(), "2026-01-03", market("2026-01-03"), docs)
            saved = json.loads((docs / "forward_validation.json").read_text(encoding="utf-8"))
            original = [r for r in saved["records"] if r["signal_date"] == "2026-01-02"]
            self.assertTrue(all(r["status"] == "active" and r["held_days"] == 0 for r in original))
            for day in range(4, 9):
                fv.update(candidates(), f"2026-01-{day:02}", market(f"2026-01-{day:02}"), docs)
            saved = json.loads((docs / "forward_validation.json").read_text(encoding="utf-8"))
            original = [r for r in saved["records"] if r["signal_date"] == "2026-01-02"]
            self.assertTrue(all(r["status"] == "completed" and r["exit_date"] == "2026-01-08" for r in original))
            self.assertEqual(saved["summary"]["revenue_only"]["completed"], 5)

    def test_same_date_is_idempotent_and_stop_precedes_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            fv.update(candidates(), "2026-01-02", market("2026-01-02"), docs)
            fv.update(candidates(), "2026-01-02", market("2026-01-02"), docs)
            fv.update(candidates(), "2026-01-03", market("2026-01-03"), docs)
            result = fv.update(candidates(), "2026-01-04", market("2026-01-04", low=94, high=109), docs)
            first_day = [r for r in result["records"] if r["signal_date"] == "2026-01-02"]
            self.assertEqual(len(first_day), 10)
            self.assertTrue(all(r["reason"] == "stop" and r["exit_price"] == 95 for r in first_day))

    def test_picks_are_deterministic(self):
        rows = candidates()
        self.assertEqual(fv.ranked_picks(rows), fv.ranked_picks(list(reversed(rows))))


if __name__ == "__main__":
    unittest.main()
