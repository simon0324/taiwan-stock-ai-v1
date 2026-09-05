import unittest
import enhanced_rank as e


class EnhancedTests(unittest.TestCase):
    def test_top_level_table_shape(self):
        payload = {"fields": ["證券代號", "投信買賣超股數"], "data": [["2330", "1,200"], ["0050", "3"]]}
        rows = e.rows_by_code(payload, "證券代號")
        self.assertEqual(e.app.number(rows["2330"]["投信買賣超股數"]), 1200)

    def test_nested_table_shape(self):
        payload = {"tables": [{"title": "統計"}, {"title": "融資融券彙總", "fields": ["代號", "今日餘額"], "data": [["2330", "10"]]}]}
        self.assertIn("2330", e.rows_by_code(payload, "代號", "融資融券彙總"))

    def test_margin_duplicate_columns_are_not_confused(self):
        values = ["2330", "台積電", "1", "2", "3", "100", "110", "9", "4", "5", "6", "20", "30", "8", "0", ""]
        payload = {"tables": [{"title": "融資融券彙總", "fields": [str(i) for i in range(16)], "data": [values]}]}
        row = e.margin_rows(payload)["2330"]
        self.assertEqual(row["融資前日餘額"], "100")
        self.assertEqual(row["融券前日餘額"], "20")

    def test_percentile_keeps_unknown_unknown(self):
        self.assertIsNone(e.pct([1, 2], None))
        self.assertEqual(e.pct([1, 2], 2), 100)


if __name__ == "__main__": unittest.main()
