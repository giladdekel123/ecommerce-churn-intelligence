"""Architecture V2 Phase 2: receipt-based integrity and the unchanged content gate.

Synthetic workbooks only, built in temporary folders. The real deterministic Crew 1
steps run (preparation, EDA, profile, draft, finalize); no agents, LLM calls or
model training are involved.
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import crew2_train
from src.crew1_contract_draft import run as draft_contract
from src.crew1_contract_finalize import finalize
from src.crew1_eda import analyze
from src.crew1_prepare import run as prepare
from src.make_corrupted_workbook import corrupt_workbook
from src.policy_registry import APPROVED_POLICIES, approved_policy_path, require_registered
from src.production_gate import validate_production_handoff
from src.source_profiler import write_profile
from src.source_receipt import create_receipt, receipt_path, sha256_file, verify_receipt

V1 = APPROVED_POLICIES["1.0.0"]
V2 = APPROVED_POLICIES["2.0.0"]
POLICY_V2 = json.loads(V2.read_text(encoding="utf-8"))


def synthetic_workbook(path: Path, rows: int = 8) -> Path:
    """20 columns obeying policy 2.0.0, covering every pinned category exactly."""
    cols = POLICY_V2["columns"]
    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = "E Comm"
    names = POLICY_V2["required_columns"]
    sheet.append(names)
    nullable = [n for n in names if cols[n]["nullable"]]
    for i in range(rows):
        record = []
        for name in names:
            spec = cols[name]
            if name == "CustomerID":
                value = 7001 + i
            elif name == "Churn":
                value = i % 2
            elif name == "SatisfactionScore":
                value = (i % 5) + 1
            elif "allowed_values" in spec:
                value = spec["allowed_values"][i % len(spec["allowed_values"])]
            elif spec["type"] == "integer":
                value = 2 + i % 3
            elif spec["nullable"] and nullable.index(name) == i % len(nullable):
                value = None
            else:
                value = 10.25 + i  # non-integral so the inferred type stays "number"
            record.append(value)
        sheet.append(record)
    wb.create_sheet("Data Dict").append(["Variable", "Description"])
    wb.save(path)
    return path


class V2ReceiptGateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.runs = self.root / "runs"
        self.valid_book = synthetic_workbook(self.root / "valid.xlsx")
        self.bad_book = self.root / "bad" / "corrupted.xlsx"
        corrupt_workbook(self.valid_book, self.bad_book)
        # crew2_train only accepts runs inside <ROOT>/runs; point it at the temp tree.
        root_patch = patch.object(crew2_train, "ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)

    def build_run(self, run_id: str, workbook: Path):
        """Deterministic upload registration plus Crew 1's deterministic steps."""
        run_dir = self.runs / run_id
        (run_dir / "source").mkdir(parents=True)
        stored = run_dir / "source" / "upload.xlsx"
        shutil.copyfile(workbook, stored)
        create_receipt(run_dir, stored, expected_sha256=sha256_file(workbook))
        crew1 = run_dir / "crew1"
        prepare(source_path=stored, output_dir=crew1)
        analyze(crew1)
        write_profile(stored, crew1 / "source_profile.json")
        draft_contract(crew1)
        return run_dir, finalize(crew1, V2)

    def gate(self, run_dir: Path, receipt="default"):
        receipt = receipt_path(run_dir) if receipt == "default" else receipt
        return validate_production_handoff(V2, run_dir / "handoff_candidate" / "dataset_contract.json",
                                           run_dir / "crew1" / "clean_data.csv", source_receipt=receipt)

    def report(self, run_dir: Path) -> dict:
        return json.loads((run_dir / "handoff_candidate" / "validation_report.json").read_text(encoding="utf-8"))

    @staticmethod
    def ids(report: dict) -> list:
        return [rule["rule_id"] for rule in report["failed_rules"]]

    # --- Required behaviour -------------------------------------------------
    def test_valid_upload_passes_content_and_integrity(self):
        run_dir, outcome = self.build_run("valid_run", self.valid_book)
        self.assertEqual(outcome["status"], "PASS", outcome)
        report = self.report(run_dir)
        self.assertEqual(report["policy_version"], "2.0.0")
        self.assertEqual(report["source_integrity"]["status"], "VERIFIED")
        self.assertEqual(report["source_integrity"]["verified_source_sha256"], sha256_file(self.valid_book))
        self.assertNotEqual(sha256_file(self.valid_book), POLICY_V2["reference_source_sha256"])
        evidence = crew2_train.verify_handoff(run_dir)
        self.assertEqual(len(evidence["features"]), 15)

    def test_corrupted_upload_passes_integrity_but_fails_content(self):
        run_dir, outcome = self.build_run("bad_run", self.bad_book)
        report = self.report(run_dir)
        self.assertEqual(outcome["status"], "FAIL")
        self.assertFalse(report["authorizes_crew2_training"])
        self.assertEqual(report["source_integrity"]["status"], "VERIFIED")
        self.assertEqual(report["source_integrity"]["verified_source_sha256"], sha256_file(self.bad_book))
        self.assertEqual(self.ids(report), ["CSV-007"])
        rule = report["failed_rules"][0]
        self.assertEqual((rule["column"], rule["observed"]), ("SatisfactionScore", ["9"]))
        # The observed range (max 9) never replaces the approved bound in the handoff.
        contract = json.loads((run_dir / "handoff_candidate" / "dataset_contract.json").read_text(encoding="utf-8"))
        self.assertEqual(contract["columns"]["SatisfactionScore"]["max"], 5)
        with self.assertRaisesRegex(ValueError, "not a clean, training-authorizing PASS"):
            crew2_train.verify_handoff(run_dir)

    def test_prepared_csv_changed_after_registration_fails(self):
        run_dir, _ = self.build_run("valid_run", self.valid_book)
        csv_file = run_dir / "crew1" / "clean_data.csv"
        csv_file.write_text(csv_file.read_text(encoding="utf-8").replace("7001,", "7999,", 1), encoding="utf-8")
        self.assertIn("HASH-001", self.ids(self.gate(run_dir)))
        with self.assertRaisesRegex(ValueError, "rejected current handoff"):
            crew2_train.verify_handoff(run_dir)

    def test_stored_workbook_changed_after_receipt_fails(self):
        run_dir, _ = self.build_run("valid_run", self.valid_book)
        with (run_dir / "source" / "upload.xlsx").open("ab") as stream:
            stream.write(b"\0")
        report = self.gate(run_dir)
        self.assertIn("SRC-002", self.ids(report))
        self.assertEqual(report["source_integrity"]["status"], "FAILED")
        with self.assertRaises(ValueError):
            crew2_train.verify_handoff(run_dir)

    def test_missing_receipt_fails_closed(self):
        run_dir, _ = self.build_run("valid_run", self.valid_book)
        self.assertEqual(self.ids(self.gate(run_dir, receipt=None)), ["SRC-001"])
        receipt_path(run_dir).unlink()
        self.assertEqual(self.ids(self.gate(run_dir)), ["SRC-002"])

    def test_edited_receipt_cannot_redefine_fingerprint(self):
        run_dir, _ = self.build_run("valid_run", self.valid_book)
        receipt = json.loads(receipt_path(run_dir).read_text(encoding="utf-8"))
        receipt["source_sha256"] = "0" * 64
        receipt_path(run_dir).write_text(json.dumps(receipt), encoding="utf-8")
        self.assertIn("SRC-002", self.ids(self.gate(run_dir)))

    def test_contract_cannot_redefine_fingerprint(self):
        run_dir, _ = self.build_run("valid_run", self.valid_book)
        path = run_dir / "handoff_candidate" / "dataset_contract.json"
        contract = json.loads(path.read_text(encoding="utf-8"))
        contract["source_sha256"] = POLICY_V2["reference_source_sha256"]
        path.write_text(json.dumps(contract), encoding="utf-8")
        self.assertIn("CTR-006", self.ids(self.gate(run_dir)))

    def test_weakened_contract_cannot_override_policy(self):
        run_dir, _ = self.build_run("bad_run", self.bad_book)
        path = run_dir / "handoff_candidate" / "dataset_contract.json"
        contract = json.loads(path.read_text(encoding="utf-8"))
        contract["columns"]["SatisfactionScore"]["max"] = 9
        contract["columns"]["Churn"]["allowed_values"] = [0, 1, 7]
        path.write_text(json.dumps(contract), encoding="utf-8")
        ids = self.ids(self.gate(run_dir))
        self.assertIn("CSV-007", ids)  # enforced from the approved policy, not the contract
        self.assertIn("CTR-009", ids)  # the relaxation itself is reported

    def test_contract_cannot_select_unregistered_policy(self):
        run_dir, _ = self.build_run("valid_run", self.valid_book)
        path = run_dir / "handoff_candidate" / "dataset_contract.json"
        contract = json.loads(path.read_text(encoding="utf-8"))
        contract["approved_policy_version"] = "9.9.9"
        path.write_text(json.dumps(contract), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not a registered approved policy"):
            crew2_train.verify_handoff(run_dir)
        self.assertIn("CTR-002", self.ids(self.gate(run_dir)))

    def test_unregistered_policy_file_is_refused(self):
        run_dir, _ = self.build_run("valid_run", self.valid_book)
        lookalike = self.root / "production_policy_v2.json"
        shutil.copyfile(V2, lookalike)
        with self.assertRaisesRegex(ValueError, "registered approved production policy"):
            crew2_train.verify_handoff(run_dir, lookalike)

    def test_receipt_from_another_run_is_refused(self):
        a, _ = self.build_run("run_a", self.valid_book)
        b, _ = self.build_run("run_b", self.valid_book)
        shutil.copyfile(receipt_path(b), receipt_path(a))
        self.assertIn("SRC-002", self.ids(self.gate(a)))


class V2PolicyTests(unittest.TestCase):
    CONTENT_KEYS = ("required_columns", "columns", "model_input_columns", "excluded_from_model_inputs",
                    "cleaning_transformation_version", "identifier_column", "target_column",
                    "unit_assumptions", "missing_value_strategy", "category_alias_mappings",
                    "raw_category_policy", "numeric_range_policy")

    def test_v2_keeps_every_v1_content_rule(self):
        v1 = json.loads(V1.read_text(encoding="utf-8"))
        for key in self.CONTENT_KEYS:
            self.assertEqual(POLICY_V2[key], v1[key], key)
        self.assertEqual(POLICY_V2["columns"]["SatisfactionScore"]["min"], 1)
        self.assertEqual(POLICY_V2["columns"]["SatisfactionScore"]["max"], 5)
        self.assertEqual(POLICY_V2["columns"]["Churn"]["allowed_values"], [0, 1])
        self.assertEqual(len(POLICY_V2["model_input_columns"]), 15)

    def test_v2_uses_receipts_and_keeps_reference_only_as_information(self):
        v1 = json.loads(V1.read_text(encoding="utf-8"))
        self.assertNotIn("source_sha256", POLICY_V2)
        self.assertEqual(POLICY_V2["reference_source_sha256"], v1["source_sha256"])
        self.assertEqual(POLICY_V2["source_integrity"]["mode"], "run_receipt")
        self.assertEqual(v1["policy_version"], "1.0.0")
        self.assertNotIn("source_integrity", v1)

    def test_registry_only_resolves_approved_versions(self):
        self.assertEqual(approved_policy_path("1.0.0"), V1)
        self.assertEqual(approved_policy_path("2.0.0"), V2)
        for bad in (None, "", "3.0.0", "../contracts/approved_policy.json"):
            with self.assertRaises(ValueError):
                approved_policy_path(bad)
        with self.assertRaises(ValueError):
            require_registered(ROOT / "contracts" / "production_policy_proposed.json")



class AmbiguousPolicyTests(unittest.TestCase):
    def test_policy_declaring_both_integrity_rules_fails_closed(self):
        helper = V2ReceiptGateTests("test_valid_upload_passes_content_and_integrity")
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        run_dir, _ = helper.build_run("valid_run", helper.valid_book)
        ambiguous = helper.root / "ambiguous_policy.json"
        ambiguous.write_text(json.dumps(dict(POLICY_V2, source_sha256="x" * 64)), encoding="utf-8")
        report = validate_production_handoff(ambiguous, run_dir / "handoff_candidate" / "dataset_contract.json",
                                             run_dir / "crew1" / "clean_data.csv",
                                             source_receipt=receipt_path(run_dir))
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("POL-009", [r["rule_id"] for r in report["failed_rules"]])


class SourceReceiptTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run_dir = Path(tmp.name) / "runs" / "r1"
        (self.run_dir / "source").mkdir(parents=True)
        self.stored = self.run_dir / "source" / "upload.xlsx"
        self.stored.write_bytes(b"synthetic workbook bytes")

    def test_receipt_is_recomputed_from_bytes(self):
        receipt = create_receipt(self.run_dir, self.stored)
        self.assertEqual(receipt["source_sha256"], sha256_file(self.stored))
        self.assertEqual(verify_receipt(self.run_dir)["source_sha256"], receipt["source_sha256"])

    def test_refuses_overwrite_and_foreign_source(self):
        create_receipt(self.run_dir, self.stored)
        with self.assertRaises(FileExistsError):
            create_receipt(self.run_dir, self.stored)
        outside = self.run_dir / "upload.xlsx"
        outside.write_bytes(b"x")
        receipt_path(self.run_dir).unlink()
        with self.assertRaises(ValueError):
            create_receipt(self.run_dir, outside)

    def test_upload_fingerprint_mismatch_is_refused(self):
        with self.assertRaisesRegex(ValueError, "computed at upload"):
            create_receipt(self.run_dir, self.stored, expected_sha256="f" * 64)
        self.assertFalse(receipt_path(self.run_dir).exists())

    def test_receipt_cannot_point_outside_source_folder(self):
        create_receipt(self.run_dir, self.stored)
        receipt = json.loads(receipt_path(self.run_dir).read_text(encoding="utf-8"))
        receipt["source_relative_path"] = "../elsewhere.xlsx"
        receipt_path(self.run_dir).write_text(json.dumps(receipt), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "outside the run's source folder"):
            verify_receipt(self.run_dir)


if __name__ == "__main__":
    unittest.main()
