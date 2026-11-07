"""Automated checks for the test-only contract gate."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.contract_validator import validate_handoff

POLICY = ROOT / "contracts" / "approved_policy.json"
CONTRACT = ROOT / "tests" / "fixtures" / "dataset_contract.json"
VALID_CSV = ROOT / "tests" / "fixtures" / "valid_clean_data.csv"
INVALID_CSV = ROOT / "tests" / "fixtures" / "invalid_churn_data.csv"


class ContractValidatorTests(unittest.TestCase):
    def test_valid_synthetic_dataset_passes(self):
        result = validate_handoff(POLICY, CONTRACT, VALID_CSV)

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["failed_rules"], [])
        self.assertIs(result["authorizes_crew2_training"], False)

    def test_churn_7_fails_with_rule_and_line(self):
        result = validate_handoff(POLICY, CONTRACT, INVALID_CSV)

        self.assertEqual(result["status"], "FAIL")
        self.assertIs(result["authorizes_crew2_training"], False)

        churn_failures = [
            item for item in result["failed_rules"] if item["rule_id"] == "CHURN-003"
        ]
        self.assertEqual(len(churn_failures), 1)
        failure = churn_failures[0]
        self.assertEqual(failure["column"], "Churn")
        self.assertEqual(failure["rows"], [3])
        self.assertEqual(failure["observed"], [7])


if __name__ == "__main__":
    unittest.main()
