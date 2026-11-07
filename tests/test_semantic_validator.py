"""Automated checks for the synthetic semantic gate."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.semantic_validator import validate_semantic_handoff

POLICY = ROOT / "contracts" / "semantic_test_policy.json"
CONTRACT = ROOT / "tests" / "fixtures" / "semantic" / "dataset_contract.json"
REFERENCE_CSV = ROOT / "tests" / "fixtures" / "semantic" / "reference_months.csv"
CORRUPTED_CSV = ROOT / "tests" / "fixtures" / "semantic" / "corrupted_months.csv"


def _load_json(path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(directory, name, payload):
    path = Path(directory) / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class SemanticValidatorTests(unittest.TestCase):
    def _assert_crew2_denied(self, result):
        self.assertIs(result["authorizes_crew2_training"], False)

    def test_reference_csv_passes(self):
        result = validate_semantic_handoff(REFERENCE_CSV, POLICY, CONTRACT)

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["failed_rules"], [])
        self._assert_crew2_denied(result)

    def test_corrupted_csv_fails_sem_001(self):
        result = validate_semantic_handoff(CORRUPTED_CSV, POLICY, CONTRACT)

        self.assertEqual(result["status"], "FAIL")
        self._assert_crew2_denied(result)
        sem_failures = [item for item in result["failed_rules"] if item["rule_id"] == "SEM-001"]
        self.assertEqual(len(sem_failures), 1)
        self.assertAlmostEqual(result["evidence"]["relative_change_mean"], 0.9167, places=4)
        self.assertAlmostEqual(result["evidence"]["relative_change_median"], 0.9167, places=4)

    def test_missing_or_incorrect_unit_fails_unit_001(self):
        original = _load_json(CONTRACT)
        cases = {
            "missing": {},
            "incorrect": {"SyntheticTenureMonths": "years"},
        }
        with tempfile.TemporaryDirectory() as directory:
            for name, unit_assumptions in cases.items():
                contract = _load_json(CONTRACT)
                contract["unit_assumptions"] = unit_assumptions
                contract_path = _write_json(directory, f"{name}_contract.json", contract)
                with self.subTest(case=name):
                    result = validate_semantic_handoff(CORRUPTED_CSV, POLICY, contract_path)
                    self.assertEqual(result["status"], "FAIL")
                    self._assert_crew2_denied(result)
                    unit_failures = [
                        item for item in result["failed_rules"] if item["rule_id"] == "UNIT-001"
                    ]
                    self.assertEqual(len(unit_failures), 1)
        self.assertEqual(_load_json(CONTRACT), original)

    def test_reference_hash_mismatch_fails_hash_001(self):
        original = _load_json(POLICY)
        policy = _load_json(POLICY)
        policy["trusted_reference"]["sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as directory:
            policy_path = _write_json(directory, "policy.json", policy)
            result = validate_semantic_handoff(REFERENCE_CSV, policy_path, CONTRACT)

        self.assertEqual(result["status"], "FAIL")
        self._assert_crew2_denied(result)
        hash_failures = [item for item in result["failed_rules"] if item["rule_id"] == "HASH-001"]
        self.assertEqual(len(hash_failures), 1)
        self.assertEqual(_load_json(POLICY), original)

    def test_threshold_2_fails_pol_006(self):
        original = _load_json(POLICY)
        policy = _load_json(POLICY)
        policy["maximum_permitted_relative_change"] = 2.0
        with tempfile.TemporaryDirectory() as directory:
            policy_path = _write_json(directory, "policy.json", policy)
            result = validate_semantic_handoff(CORRUPTED_CSV, policy_path, CONTRACT)

        self.assertEqual(result["status"], "FAIL")
        self._assert_crew2_denied(result)
        threshold_failures = [
            item for item in result["failed_rules"] if item["rule_id"] == "POL-006"
        ]
        self.assertEqual(len(threshold_failures), 1)
        self.assertEqual(threshold_failures[0]["observed"], 2.0)
        self.assertEqual(_load_json(POLICY), original)


if __name__ == "__main__":
    unittest.main()
