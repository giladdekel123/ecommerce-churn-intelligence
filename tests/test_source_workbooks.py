"""Architecture V2 Phase 1: per-workbook profiles and the one-cell corruption utility.

Every workbook here is synthetic and built in a temporary folder; no customer data,
model training, or LLM calls are involved.
"""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import openpyxl
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import crew1_contract_draft
from src.make_corrupted_workbook import corrupt_workbook
from src.source_profiler import profile, write_profile


def build_workbook(path: Path, scores=(3, 5, 1, 4), sheet="E Comm") -> Path:
    wb = openpyxl.Workbook()
    data = wb.active
    data.title = sheet
    data.append(["CustomerID", "Churn", "Tenure", "PreferedOrderCat", "SatisfactionScore"])
    for i, score in enumerate(scores):
        data.append([5001 + i, i % 2, None if i == 0 else 2.0 + i, "Grocery" if i % 2 else "Fashion", score])
    notes = wb.create_sheet("Data Dict")
    notes.append(["Variable", "Description"])
    notes.append(["Churn", "Churn Flag"])
    wb.save(path)
    return path


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class SourceProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_profile_records_that_workbooks_own_fingerprint(self):
        first = build_workbook(self.root / "a.xlsx")
        second = build_workbook(self.root / "b.xlsx", scores=(2, 2, 2, 2))
        p1, p2 = profile(first), profile(second)
        self.assertEqual(p1["source_sha256"], digest(first))
        self.assertEqual(p2["source_sha256"], digest(second))
        self.assertNotEqual(p1["source_sha256"], p2["source_sha256"])
        self.assertEqual(p1["rows"], 4)
        self.assertEqual(p1["missing_by_column"], {"Tenure": 1})

    def test_write_profile_refuses_overwrite(self):
        source = build_workbook(self.root / "a.xlsx")
        out = self.root / "run" / "crew1" / "source_profile.json"
        report = write_profile(source, out)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8")), report)
        with self.assertRaises(FileExistsError):
            write_profile(source, out)

    def test_missing_worksheet_fails(self):
        source = build_workbook(self.root / "a.xlsx", sheet="Other")
        with self.assertRaisesRegex(ValueError, "Missing worksheet"):
            profile(source)


class CorruptWorkbookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = build_workbook(self.root / "original.xlsx")
        self.output = self.root / "generated" / "corrupted.xlsx"

    def test_exactly_one_cell_changes_to_nine(self):
        before_hash = digest(self.source)
        receipt = corrupt_workbook(self.source, self.output)
        self.assertEqual(digest(self.source), before_hash)
        self.assertTrue(receipt["source_unchanged"])
        self.assertNotEqual(receipt["output_sha256"], receipt["source_sha256"])
        self.assertEqual(receipt["output_sha256"], digest(self.output))
        self.assertEqual((receipt["excel_row"], receipt["original_value"], receipt["new_value"]), (2, 3, 9))

        old = pd.read_excel(self.source, sheet_name=None)
        new = pd.read_excel(self.output, sheet_name=None)
        self.assertEqual(list(old), list(new))
        pd.testing.assert_frame_equal(old["Data Dict"], new["Data Dict"])
        self.assertEqual(new["E Comm"]["SatisfactionScore"].tolist(), [9, 5, 1, 4])
        expected = old["E Comm"].copy()
        expected.loc[0, "SatisfactionScore"] = 9
        pd.testing.assert_frame_equal(expected, new["E Comm"])

        saved = json.loads(self.output.with_name("corrupted.receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, receipt)

    def test_profiles_of_both_files_differ_only_by_fingerprint_and_score(self):
        corrupt_workbook(self.source, self.output)
        good, bad = profile(self.source), profile(self.output)
        self.assertNotEqual(good["source_sha256"], bad["source_sha256"])
        self.assertEqual(good["rows"], bad["rows"])
        self.assertEqual(good["column_names"], bad["column_names"])
        self.assertEqual(good["dtypes_inferred"], bad["dtypes_inferred"])
        self.assertEqual(bad["numeric_observations"]["SatisfactionScore"]["max"], 9.0)

    def test_other_package_parts_and_float_values_are_untouched(self):
        import zipfile
        wb = openpyxl.load_workbook(self.source)
        wb["E Comm"]["F1"] = "CashbackAmount"
        for row in range(2, 6):
            wb["E Comm"][f"F{row}"] = 120.86000000000001
        source = self.root / "floats.xlsx"
        wb.save(source)
        corrupt_workbook(source, self.output)
        with zipfile.ZipFile(source) as a, zipfile.ZipFile(self.output) as b:
            self.assertEqual(a.namelist(), b.namelist())
            changed = [n for n in a.namelist() if a.read(n) != b.read(n)]
        self.assertEqual(len(changed), 1)
        self.assertTrue(changed[0].startswith("xl/worksheets/"))
        # Exact equality with whatever the source stores (no float re-serialisation).
        before = pd.read_excel(source, sheet_name="E Comm")["CashbackAmount"].tolist()
        after = pd.read_excel(self.output, sheet_name="E Comm")["CashbackAmount"].tolist()
        self.assertEqual(after, before)

    def test_refuses_to_overwrite(self):
        corrupt_workbook(self.source, self.output)
        with self.assertRaises(FileExistsError):
            corrupt_workbook(self.source, self.output)

    def test_refuses_source_as_output(self):
        before = digest(self.source)
        with self.assertRaises(ValueError):
            corrupt_workbook(self.source, self.source)
        self.assertEqual(digest(self.source), before)

    def test_refuses_tracked_location_inside_repository(self):
        target = ROOT / "docs" / "should_not_exist.xlsx"
        with self.assertRaisesRegex(ValueError, "Git-ignored"):
            corrupt_workbook(self.source, target)
        self.assertFalse(target.exists())

    def test_no_valid_score_to_change_fails_without_output(self):
        source = build_workbook(self.root / "nulls.xlsx", scores=(None, None, None, None))
        with self.assertRaisesRegex(ValueError, "No existing integer"):
            corrupt_workbook(source, self.output)
        self.assertFalse(self.output.exists())


class DraftProfileSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run_dir = Path(self.temp.name) / "runs" / "r" / "crew1"
        self.run_dir.mkdir(parents=True)

    def test_explicit_profile_wins(self):
        explicit = Path(self.temp.name) / "explicit.json"
        (self.run_dir / "source_profile.json").write_text("{}", encoding="utf-8")
        self.assertEqual(crew1_contract_draft.resolve_profile_path(self.run_dir, explicit), explicit)

    def test_run_specific_profile_is_used_when_present(self):
        per_run = self.run_dir / "source_profile.json"
        per_run.write_text("{}", encoding="utf-8")
        self.assertEqual(crew1_contract_draft.resolve_profile_path(self.run_dir), per_run)

    def test_legacy_global_profile_without_run_profile(self):
        self.assertEqual(crew1_contract_draft.resolve_profile_path(self.run_dir), crew1_contract_draft.PROFILE)

    def test_run_passes_selected_profile_to_draft(self):
        per_run = self.run_dir / "source_profile.json"
        per_run.write_text("{}", encoding="utf-8")
        with patch.object(crew1_contract_draft, "draft", return_value={"ok": True}) as fake:
            crew1_contract_draft.run(self.run_dir)
        fake.assert_called_once_with(self.run_dir, per_run)

    def test_run_profile_for_other_workbook_fails_closed(self):
        """A per-run profile is still rejected unless its hash equals the run audit's."""
        csv = self.run_dir / "clean_data.csv"
        csv.write_text("CustomerID,Churn\n1,0\n", encoding="utf-8")
        h = digest(csv)
        common = {"authorizes_crew2_training": False, "run_id": "r", "clean_data_sha256": h}
        (self.run_dir / "cleaning_audit.json").write_text(json.dumps(
            {**common, "status": "PREPARED_NOT_GATE_APPROVED", "source_sha256": "upload-a"}), encoding="utf-8")
        (self.run_dir / "eda_summary.json").write_text(json.dumps(
            {**common, "status": "EDA_DESCRIPTIVE_ONLY_NOT_GATE_APPROVED"}), encoding="utf-8")
        profile_path = self.run_dir / "source_profile.json"
        profile_path.write_text(json.dumps({"source_sha256": "upload-b"}), encoding="utf-8")
        policy = Path(self.temp.name) / "policy.json"
        policy.write_text(json.dumps({"status": "PROPOSED_NOT_APPROVED", "formally_approved": False,
                                      "authorizes_crew2_training": False}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Source workbook hash mismatch"):
            crew1_contract_draft.draft(self.run_dir, profile_path, policy)


if __name__ == "__main__":
    unittest.main()
