import unittest

import beneficiary_rank as b


class BeneficiaryRankTests(unittest.TestCase):
    def test_mild_pullback_scores_higher_than_crash(self):
        self.assertGreater(b.pullback_score(-10), b.pullback_score(-30))

    def test_missing_foreign_is_renormalized_and_disclosed(self):
        results = {"stocks": [{"code": "2449", "name": "京元電子", "price": 90, "revenue": 20, "eps": 3,
                                "foreign": 0, "trend": 100}]}
        history = [[{"code": "2449", "close": 100, "date": "2026-09-01"}],
                   [{"code": "2449", "close": 90, "date": "2026-09-02"}]]
        row = b.build(results, history)[0]
        self.assertFalse(row["foreign_data_available"])
        self.assertIsNone(row["components"]["foreign"])
        self.assertGreater(row["observation_score"], 0)


if __name__ == "__main__":
    unittest.main()
