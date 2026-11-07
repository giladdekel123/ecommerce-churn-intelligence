"""No-LLM tests of Crew 1 evidence boundaries and preflight."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.crew1_agents import _load_evidence, check


class Crew1AgentPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run_dir = Path(self.tmp.name) / "runs" / "sample" / "crew1"
        self.run_dir.mkdir(parents=True)
        csv = self.run_dir / "clean_data.csv"
        csv.write_text("CustomerID,Churn\n1,0\n2,1\n", encoding="utf-8", newline="\n")
        sha = hashlib.sha256(csv.read_bytes()).hexdigest()
        self.audit = {
            "status": "PREPARED_NOT_GATE_APPROVED", "run_id": "sample",
            "authorizes_crew2_training": False, "row_count_after": 2,
            "column_names": ["CustomerID", "Churn"], "missing_cells_retained": 0,
            "churn_counts": {"0": 1, "1": 1}, "clean_data_sha256": sha,
            "category_mappings_applied": {}, "nulls_imputed": 0,
        }
        self.eda = {
            "status": "EDA_DESCRIPTIVE_ONLY_NOT_GATE_APPROVED", "run_id": "sample",
            "authorizes_crew2_training": False, "customers": 2, "column_count": 2,
            "missing_cells": 0, "churn_counts": {"0": 1, "1": 1},
            "clean_data_sha256": sha, "historical_churn_rate": .5,
        }
        self.save()
        (self.run_dir / "eda_report.html").write_text("<h1>test</h1>", encoding="utf-8")

    def save(self):
        (self.run_dir / "cleaning_audit.json").write_text(json.dumps(self.audit), encoding="utf-8")
        (self.run_dir / "eda_summary.json").write_text(json.dumps(self.eda), encoding="utf-8")

    def test_valid_evidence_preflight(self):
        result = check(self.run_dir)
        self.assertEqual(result["status"], "EVIDENCE_VERIFIED_FOR_AGENT_REVIEW_ONLY")
        self.assertEqual(len(result["agents_planned"]), 3)
        self.assertIs(result["authorizes_crew2_training"], False)

    def test_tampered_csv_hash_fails(self):
        with (self.run_dir / "clean_data.csv").open("a") as f:
            f.write("3,0\n")
        with self.assertRaisesRegex(ValueError, "hash"):
            _load_evidence(self.run_dir)

    def test_mismatched_run_id_fails(self):
        self.eda["run_id"] = "other"
        self.save()
        with self.assertRaisesRegex(ValueError, "run IDs"):
            _load_evidence(self.run_dir)

    def test_unapproved_training_claim_fails(self):
        self.audit["authorizes_crew2_training"] = True
        self.save()
        with self.assertRaisesRegex(ValueError, "authoriz"):
            _load_evidence(self.run_dir)


if __name__ == "__main__":
    unittest.main()
