"""Pure-data regression tests; no original workbook is required."""

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.crew1_prepare import prepare_dataframe


class Crew1PrepareTests(unittest.TestCase):
    def frame(self):
        return pd.DataFrame({
            "CustomerID": [1001, 1002, 1003],
            "Churn": [0, 1, 0],
            "Tenure": [12.0, None, 36.0],
            "PreferredPaymentMode": ["CC", "Credit Card", "COD"],
            "WarehouseToHome": [5, 127, 7],
        })

    def test_preserves_missing_categories_and_outliers(self):
        original = self.frame()
        prepared, audit = prepare_dataframe(original)
        pd.testing.assert_frame_equal(prepared, original)
        self.assertEqual(audit["missing_cells_retained"], 1)
        self.assertEqual(audit["category_mappings_applied"], {})
        self.assertEqual(audit["rows_dropped"], 0)
        self.assertIs(audit["authorizes_crew2_training"], False)

    def test_duplicate_customer_fails_closed(self):
        frame = self.frame()
        frame.loc[2, "CustomerID"] = 1001
        with self.assertRaisesRegex(ValueError, "duplicates"):
            prepare_dataframe(frame)

    def test_invalid_churn_fails_closed(self):
        frame = self.frame()
        frame.loc[2, "Churn"] = 7
        with self.assertRaisesRegex(ValueError, "outside"):
            prepare_dataframe(frame)

    def test_null_customer_fails_closed(self):
        frame = self.frame()
        frame.loc[1, "CustomerID"] = None
        with self.assertRaisesRegex(ValueError, "null"):
            prepare_dataframe(frame)


if __name__ == "__main__":
    unittest.main()
