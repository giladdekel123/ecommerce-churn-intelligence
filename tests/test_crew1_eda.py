"""Tests for deterministic descriptive EDA."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.crew1_eda import analyze


class Crew1EdaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.csv = self.dir / "clean_data.csv"
        self.audit = self.dir / "cleaning_audit.json"
        self.frame = pd.DataFrame({
            "CustomerID": [2001, 2002, 2003, 2004],
            "Churn": [0, 1, 0, 0],
            "PreferredLoginDevice": ["Phone", "Phone", "Computer", "Computer"],
            "Tenure": [12, None, 36, 24],
        })
        self.frame.to_csv(self.csv, index=False, lineterminator="\n")
        self.payload = {
            "status": "PREPARED_NOT_GATE_APPROVED", "run_id": "synthetic-test",
            "row_count_after": 4, "column_names": list(self.frame.columns),
            "clean_data_sha256": hashlib.sha256(self.csv.read_bytes()).hexdigest(),
            "missing_cells_retained": 1, "authorizes_crew2_training": False,
        }
        self.save_audit()

    def save_audit(self):
        self.audit.write_text(json.dumps(self.payload), encoding="utf-8")

    def test_creates_report_with_computed_aggregates(self):
        result = analyze(self.dir)
        summary = json.loads((self.dir / "eda_summary.json").read_text(encoding="utf-8"))
        report = (self.dir / "eda_report.html").read_text(encoding="utf-8")
        self.assertEqual(result["customers"], 4)
        self.assertEqual(summary["historical_churn_rate"], 0.25)
        self.assertEqual(summary["missing_cells"], 1)
        self.assertIs(summary["authorizes_crew2_training"], False)
        self.assertIn("<svg", report)
        self.assertIn("Phone", report)

    def test_changed_csv_fails_hash_check(self):
        with self.csv.open("a", encoding="utf-8") as handle:
            handle.write("2005,1,Phone,10\n")
        with self.assertRaisesRegex(ValueError, "fingerprint mismatch"):
            analyze(self.dir)

    def test_refuses_to_overwrite_existing_report(self):
        analyze(self.dir)
        with self.assertRaises(FileExistsError):
            analyze(self.dir)

    def test_refuses_authorized_crew2_flag(self):
        self.payload["authorizes_crew2_training"] = True
        self.save_audit()
        with self.assertRaisesRegex(ValueError, "authorizes Crew 2"):
            analyze(self.dir)


if __name__ == "__main__":
    unittest.main()
