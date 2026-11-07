"""Isolated tests for the read-only production gate. No real customer data."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from src.production_gate import validate_production_handoff


class ProductionGateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.csv = self.root / "clean_data.csv"
        self.contract_file = self.root / "contract.json"
        self.policy_file = self.root / "policy.json"
        self.columns = {
            "CustomerID": {"type": "integer", "required": True, "nullable": False,
                           "unique": True, "role": "identifier"},
            "Churn": {"type": "integer", "required": True, "nullable": False,
                      "allowed_values": [0, 1], "role": "target"},
            "Tenure": {"type": "number", "required": True, "nullable": True,
                       "min": 0, "role": "feature"},
            "PreferredLoginDevice": {"type": "string", "required": True, "nullable": False,
                                     "allowed_values": ["Phone", "Mobile Phone"], "role": "feature"},
        }
        self.policy = {
            "policy_version": "1.0.0", "status": "APPROVED", "formally_approved": True,
            "approved_by": "Test-only reviewer", "approval_date": "2026-11-07",
            "required_columns": list(self.columns), "columns": self.columns,
            "model_input_columns": ["Tenure", "PreferredLoginDevice"],
            "cleaning_transformation_version": "0.1.0-conservative",
            "source_sha256": "synthetic-source-test-hash",
            "identifier_column": "CustomerID", "target_column": "Churn",
            "unit_assumptions": {"Tenure": "unknown_not_independently_verified"},
            "missing_value_strategy": "Preserve in Crew 1; fit imputation inside Crew 2 training folds only",
        }
        self.contract = {
            "contract_version": "1.0.0", "approved_policy_version": "1.0.0",
            "contract_status": "READY_FOR_GATE_VALIDATION",
            "columns": self.columns, "model_input_columns": self.policy["model_input_columns"],
            "identifier_column": "CustomerID", "target_column": "Churn",
            "unit_assumptions": {"Tenure": "unknown_not_independently_verified"},
            "missing_value_strategy": "Preserve in Crew 1; fit imputation inside Crew 2 training folds only",
            "cleaning_transformation_version": "0.1.0-conservative",
            "source_sha256": "synthetic-source-test-hash",
            "cleaning_operations": {"rows_dropped": 0, "nulls_imputed": 0,
                                   "category_mappings_applied": {}, "numeric_values_clipped": 0,
                                   "columns_renamed": {}},
            "category_alias_mappings": {},
            "prepared_data": {"rows": 2, "columns": 4, "missing_cells": 1},
        }
        self.csv.write_text("CustomerID,Churn,Tenure,PreferredLoginDevice\n1,0,,Phone\n2,1,4,Mobile Phone\n", encoding="utf-8")
        self.save()

    def save(self):
        self.contract["prepared_data"]["sha256"] = hashlib.sha256(self.csv.read_bytes()).hexdigest()
        self.policy_file.write_text(json.dumps(self.policy), encoding="utf-8")
        self.contract_file.write_text(json.dumps(self.contract), encoding="utf-8")

    def validate(self):
        return validate_production_handoff(self.policy_file, self.contract_file, self.csv)

    def assert_failed_rule(self, rule):
        output = self.validate()
        self.assertEqual(output["status"], "FAIL")
        self.assertFalse(output["authorizes_crew2_training"])
        self.assertFalse(output["model_training_started"])
        self.assertIn(rule, [x["rule_id"] for x in output["failed_rules"]])

    def test_independently_approved_synthetic_handoff_passes(self):
        output = self.validate()
        self.assertEqual(output["status"], "PASS", output["failed_rules"])
        self.assertTrue(output["authorizes_crew2_training"])
        self.assertFalse(output["model_training_started"])

    def test_unapproved_policy_fails_even_when_values_valid(self):
        self.policy["status"] = "PROPOSED_NOT_APPROVED"
        self.policy["formally_approved"] = False
        self.save()
        self.assert_failed_rule("POL-001")

    def test_draft_contract_fails_even_when_values_valid(self):
        self.contract["contract_status"] = "DRAFT_PENDING_INDEPENDENT_POLICY_AND_ALLOWLIST_APPROVAL"
        self.save()
        self.assert_failed_rule("CTR-001")

    def test_churn_7_fails_with_rule(self):
        self.csv.write_text("CustomerID,Churn,Tenure,PreferredLoginDevice\n1,7,,Phone\n2,1,4,Mobile Phone\n", encoding="utf-8")
        self.save()
        self.assert_failed_rule("CHURN-003")

    def test_duplicate_customer_fails(self):
        self.csv.write_text("CustomerID,Churn,Tenure,PreferredLoginDevice\n1,0,,Phone\n1,1,4,Mobile Phone\n", encoding="utf-8")
        self.save()
        self.assert_failed_rule("CID-003")

    def test_missing_required_column_fails(self):
        self.csv.write_text("CustomerID,Churn,Tenure\n1,0,\n2,1,4\n", encoding="utf-8")
        self.save()
        self.assert_failed_rule("CSV-001")

    def test_contract_relaxation_fails(self):
        self.contract["columns"] = {key: value.copy() for key,value in self.columns.items()}
        self.contract["columns"]["Churn"]["allowed_values"] = [0,1,7]
        self.save()
        self.assert_failed_rule("CTR-009")

    def test_csv_tamper_fails_even_when_rows_valid(self):
        self.csv.write_text("CustomerID,Churn,Tenure,PreferredLoginDevice\n1,0,,Phone\n2,1,5,Mobile Phone\n", encoding="utf-8")
        # Deliberately do not update contract hash.
        self.assert_failed_rule("HASH-001")

    def test_unapproved_unit_claim_fails(self):
        self.contract["unit_assumptions"]["Tenure"] = "months"
        self.save()
        self.assert_failed_rule("CTR-011")

    def test_contract_cannot_claim_authorization(self):
        self.contract["authorizes_crew2_training"] = True
        self.save()
        self.assert_failed_rule("CTR-010")

    def test_excluded_predictor_blocked(self):
        self.policy["model_input_columns"] = ["Tenure", "Gender"]
        self.contract["model_input_columns"] = ["Tenure", "Gender"]
        self.save()
        self.assert_failed_rule("CTR-004")


if __name__ == "__main__":
    unittest.main()
