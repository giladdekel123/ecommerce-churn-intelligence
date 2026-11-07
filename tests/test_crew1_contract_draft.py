"""Unit tests for contract draft generation; no real customer data or model calls."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from src.crew1_contract_draft import draft, run, PROPOSED_PREDICTORS


class ContractDraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run_dir = self.root / "runs" / "run_test" / "crew1"
        self.run_dir.mkdir(parents=True)
        csv = self.run_dir / "clean_data.csv"
        csv.write_text("CustomerID,Churn,Tenure\n1,0,\n2,1,4\n", encoding="utf-8")
        csv_hash = hashlib.sha256(csv.read_bytes()).hexdigest()
        self.audit = {
            "status": "PREPARED_NOT_GATE_APPROVED", "authorizes_crew2_training": False,
            "run_id": "run_test", "source_file": "E Commerce Dataset.xlsx", "source_sheet": "E Comm",
            "source_sha256": "sourcehash", "clean_data_sha256": csv_hash,
            "row_count_before": 2, "row_count_after": 2, "column_names": ["CustomerID", "Churn", "Tenure"],
            "missing_cells_retained": 1, "missing_by_column": {"Tenure": 1},
            "churn_counts": {"0": 1, "1": 1}, "identifier_unique": True,
            "rows_dropped": 0, "nulls_imputed": 0, "category_mappings_applied": {},
            "numeric_values_clipped": 0, "columns_renamed": {}, "preparation_version": "test"
        }
        self.eda = {"status": "EDA_DESCRIPTIVE_ONLY_NOT_GATE_APPROVED", "authorizes_crew2_training": False,
                    "run_id": "run_test", "clean_data_sha256": csv_hash, "customers": 2,
                    "missing_cells": 1, "missing_by_column": {"Tenure": 1}, "churn_counts": {"0": 1, "1": 1}}
        self.profile = {"source_sha256": "sourcehash", "rows": 2,
                        "column_names": ["CustomerID", "Churn", "Tenure"],
                        "dtypes_inferred": {"CustomerID": "int64", "Churn": "int64", "Tenure": "float64"}}
        self.policy = {"status": "PROPOSED_NOT_APPROVED", "formally_approved": False,
                       "authorizes_crew2_training": False, "policy_version": "0.1.0-proposed",
                       "required_columns": ["CustomerID", "Churn", "Tenure"],
                       "proposed_nullable_columns": ["Tenure"]}
        self.profile_path = self.root / "profile.json"
        self.policy_path = self.root / "policy.json"
        self.save()

    def save(self):
        (self.run_dir / "cleaning_audit.json").write_text(json.dumps(self.audit), encoding="utf-8")
        (self.run_dir / "eda_summary.json").write_text(json.dumps(self.eda), encoding="utf-8")
        self.profile_path.write_text(json.dumps(self.profile), encoding="utf-8")
        self.policy_path.write_text(json.dumps(self.policy), encoding="utf-8")

    def test_draft_has_no_authorization_and_no_approved_allowlist(self):
        # This test uses a tiny policy: patch predictor list by using matching synthetic fields
        from unittest.mock import patch
        with patch("src.crew1_contract_draft.PROPOSED_PREDICTORS", ["Tenure"]):
            result = draft(self.run_dir, self.profile_path, self.policy_path)
        self.assertFalse(result["authorizes_crew2_training"])
        self.assertEqual(result["model_input_columns"], [])
        self.assertEqual(result["columns"]["Tenure"]["proposed_nullable"], True)
        self.assertIsNone(result["approved_policy_version"])

    def test_csv_tamper_fails_closed(self):
        (self.run_dir / "clean_data.csv").write_text("CustomerID,Churn,Tenure\n1,7,\n", encoding="utf-8")
        from unittest.mock import patch
        with patch("src.crew1_contract_draft.PROPOSED_PREDICTORS", ["Tenure"]):
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                draft(self.run_dir, self.profile_path, self.policy_path)

    def test_fake_policy_authorization_fails_closed(self):
        self.policy["formally_approved"] = True
        self.save()
        from unittest.mock import patch
        with patch("src.crew1_contract_draft.PROPOSED_PREDICTORS", ["Tenure"]):
            with self.assertRaises(ValueError):
                draft(self.run_dir, self.profile_path, self.policy_path)

    def test_cross_run_evidence_fails_closed(self):
        self.eda["run_id"] = "another_run"
        self.save()
        from unittest.mock import patch
        with patch("src.crew1_contract_draft.PROPOSED_PREDICTORS", ["Tenure"]):
            with self.assertRaisesRegex(ValueError, "Run IDs disagree"):
                draft(self.run_dir, self.profile_path, self.policy_path)

    def test_mismatch_schema_fails_closed(self):
        self.audit["column_names"] = ["CustomerID", "Churn", "Wrong"]
        self.save()
        from unittest.mock import patch
        with patch("src.crew1_contract_draft.PROPOSED_PREDICTORS", ["Tenure"]):
            with self.assertRaisesRegex(ValueError, "Column names/order"):
                draft(self.run_dir, self.profile_path, self.policy_path)


if __name__ == "__main__":
    unittest.main()
