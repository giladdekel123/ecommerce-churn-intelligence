"""Synthetic tests: final contract preparation and independent gate, no customer data."""
from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from src.crew1_contract_finalize import finalize, prepare_finalized_contract
from src.production_gate import validate_production_handoff


class FinalizeContractTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.run_dir = self.root / "runs" / "synthetic-run" / "crew1"
        self.run_dir.mkdir(parents=True)
        self.policy_file = self.root / "policy.json"
        source = Path(__file__).resolve().parents[1] / "contracts" / "production_policy_v1.json"
        self.policy = json.loads(source.read_text(encoding="utf-8"))
        self.policy["source_sha256"] = "synthetic-source-digest"
        self.policy_file.write_text(json.dumps(self.policy), encoding="utf-8")
        self.csv_file = self.run_dir / "clean_data.csv"
        self.names = self.policy["required_columns"]
        self.records = []
        for i in range(2):
            row = {}
            for name in self.names:
                spec = self.policy["columns"][name]
                if name == "CustomerID": row[name] = str(1001+i)
                elif name == "Churn": row[name] = str(i)
                elif spec.get("nullable") and i == 0: row[name] = ""
                elif "allowed_values" in spec: row[name] = str(spec["allowed_values"][0])
                elif spec["type"] in ("number", "integer"): row[name] = str(spec.get("min",0)+1)
                else: raise AssertionError(name)
            self.records.append(row)
        self._write_csv()
        self.draft = {
            "contract_status": "DRAFT_PENDING_INDEPENDENT_POLICY_AND_ALLOWLIST_APPROVAL",
            "contract_version": "0.1.0-draft", "run_id": "synthetic-run",
            "source_sha256": "synthetic-source-digest", "authorizes_crew2_training": False,
            "model_training_started": False, "cleaning_transformation_version": "0.1.0-conservative",
            "category_alias_mappings": {}, "cleaning_operations": {
                "rows_dropped": 0, "nulls_imputed": 0, "category_mappings_applied": {},
                "numeric_values_clipped": 0, "columns_renamed": {}
            },
            "missing_value_strategy": self.policy["missing_value_strategy"],
            "unit_assumptions": self.policy["unit_assumptions"],
            "proposed_model_input_columns_pending_approval": self.policy["model_input_columns"],
            "model_input_columns": [],
            "identifier_column": "CustomerID", "target_column": "Churn",
            "columns": {},
            "prepared_data": {"filename":"clean_data.csv", "sha256":self._hash(), "rows":2,
                              "columns":20, "missing_cells":7},
        }
        for n in self.names:
            spec = self.policy["columns"][n]
            desc={"type":spec["type"], "proposed_nullable":spec["nullable"],
                  "role":"feature_candidate", "constraints_status":"PROPOSED_NOT_APPROVED"}
            if spec["type"]=="string":
                desc["observed_categories_not_approved_allowlist"]=list(spec["allowed_values"])
            self.draft["columns"][n]=desc
        self.audit = {
            "status":"PREPARED_NOT_GATE_APPROVED", "run_id":"synthetic-run",
            "source_sha256":"synthetic-source-digest", "clean_data_sha256":self._hash(),
            "row_count_after":2, "missing_cells_retained":7
        }
        self._write_evidence()

    def _hash(self): return hashlib.sha256(self.csv_file.read_bytes()).hexdigest()

    def _write_csv(self):
        with self.csv_file.open("w",newline="",encoding="utf-8") as fh:
            writer=csv.DictWriter(fh,fieldnames=self.names,lineterminator="\n")
            writer.writeheader()
            writer.writerows(self.records)

    def _write_evidence(self):
        (self.run_dir / "dataset_contract.json").write_text(json.dumps(self.draft),encoding="utf-8")
        (self.run_dir / "cleaning_audit.json").write_text(json.dumps(self.audit),encoding="utf-8")
        self.policy_file.write_text(json.dumps(self.policy),encoding="utf-8")

    def test_valid_reviewed_candidate_passes_independent_gate(self):
        result=finalize(self.run_dir,self.policy_file)
        self.assertEqual(result["status"],"PASS", result)
        self.assertTrue(result["authorizes_crew2_training"])
        self.assertFalse(result["model_training_started"])
        self.assertEqual(result["rule_ids"],[])
        out=json.loads(Path(result["candidate_contract"]).read_text())
        self.assertFalse(out["authorizes_crew2_training"])
        self.assertEqual(out["model_input_columns"],self.policy["model_input_columns"])
        self.assertEqual(out["approved_policy_version"],"1.0.0")
        self.assertEqual(out["columns"]["Churn"]["allowed_values"],[0,1])
        self.assertEqual(self.draft["contract_status"],"DRAFT_PENDING_INDEPENDENT_POLICY_AND_ALLOWLIST_APPROVAL")

    def test_approved_policy_cannot_be_unapproved(self):
        self.policy["formally_approved"]=False
        self._write_evidence()
        with self.assertRaisesRegex(ValueError,"not explicitly approved"):
            finalize(self.run_dir,self.policy_file)

    def test_tampered_csv_is_refused_before_finalization(self):
        self.records[0]["Tenure"]="99"
        self._write_csv()
        with self.assertRaisesRegex(ValueError,"CSV was changed"):
            finalize(self.run_dir,self.policy_file)

    def test_forbidden_feature_is_refused(self):
        self.policy["model_input_columns"]=self.policy["model_input_columns"][:-1]+["Gender"]
        self.draft["proposed_model_input_columns_pending_approval"]=self.policy["model_input_columns"]
        self._write_evidence()
        with self.assertRaisesRegex(ValueError,"allowlist is invalid"):
            finalize(self.run_dir,self.policy_file)

    def test_cannot_overwrite_candidate(self):
        finalize(self.run_dir,self.policy_file)
        with self.assertRaises(FileExistsError):
            finalize(self.run_dir,self.policy_file)

    def test_bad_churn_is_blocked_by_independent_gate(self):
        outcome=finalize(self.run_dir,self.policy_file)
        self.records[1]["Churn"]="7"
        self._write_csv()
        result=validate_production_handoff(self.policy_file,Path(outcome["candidate_contract"]),self.csv_file)
        self.assertEqual(result["status"],"FAIL")
        self.assertFalse(result["authorizes_crew2_training"])
        rules={r["rule_id"] for r in result["failed_rules"]}
        self.assertIn("CHURN-003",rules)
        self.assertIn("HASH-001",rules)

    def test_unverified_units_remain_unknown(self):
        output=prepare_finalized_contract(self.run_dir,self.policy_file)
        self.assertEqual(output["unit_assumptions"],self.policy["unit_assumptions"])
        self.assertIn("unknown_not_independently_verified",set(output["unit_assumptions"].values()))
        self.assertNotIn("Gender",output["model_input_columns"])
        self.assertNotIn("CityTier",output["model_input_columns"])


if __name__=="__main__": unittest.main()
