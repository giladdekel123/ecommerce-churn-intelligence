"""Architecture V2 Phase 3: full pipeline with an uploaded workbook.

The real CrewAI Flow, deterministic Crew 1 steps, contract drafting, finalizer and
independent gate run on synthetic workbooks. Every CrewAI agent crew and all model
training are mocked: no Gemini calls, no real customer data, no real training.
"""
import hashlib
import inspect
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from test_v2_receipt_gate import synthetic_workbook  # synthetic 20-column V2 workbook

from src import crew1_agents, crew2_train, full_pipeline
from src.full_pipeline import FreshRunCoordinator, make_flow, preflight_upload
from src.make_corrupted_workbook import corrupt_workbook

CREW1_OK = {"status": "AGENT_REVIEW_COMPLETE_NEEDS_HUMAN_REVIEW",
            "missing_required_tools": [], "missing_outputs": []}
CREW2_OK = {"status": "CREW2_AGENT_REVIEWS_COMPLETE_NEED_HUMAN_REVIEW",
            "missing_tools": [], "missing_reports": []}


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class V2PipelineTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.runs = self.base / "runs"
        (self.base / "selected").mkdir()
        self.valid = synthetic_workbook(self.base / "selected" / "valid.xlsx")
        self.bad = self.base / "selected" / "corrupted.xlsx"
        corrupt_workbook(self.valid, self.bad)
        for patcher in (patch.dict(os.environ, {"CREWAI_DISABLE_TELEMETRY": "true", "OTEL_SDK_DISABLED": "true"}),
                        patch.object(crew2_train, "ROOT", self.base)):  # runs/ lives in the temp tree
            patcher.start()
            self.addCleanup(patcher.stop)
        self.calls = []

    def run_pipeline(self, workbook: Path, **kwargs):
        """Drive the real Flow; record the order of key steps; mock agents and training."""
        from src import crew1_contract_draft, crew1_contract_finalize
        real_draft, real_finalize = crew1_contract_draft.run, crew1_contract_finalize.finalize

        def crew1_agents_mock(run_dir, progress=None):
            self.calls.append("crew1_agents")
            return CREW1_OK

        def draft(run_dir, *a, **k):
            self.calls.append("contract_draft")
            return real_draft(run_dir, *a, **k)

        def finalize(run_dir, policy):
            self.calls.append("finalize_and_gate")
            return real_finalize(run_dir, policy)

        def train(run_dir, on_training_start=None):
            self.calls.append("crew2_training")
            self.verified = crew2_train.verify_handoff(run_dir)  # real re-verification, no training
            on_training_start()
            folder = Path(run_dir) / "crew2"
            folder.mkdir()
            for name in ("features.csv", "model.joblib", "evaluation_report.md", "model_card.md"):
                (folder / name).write_text("synthetic", encoding="utf-8")
            return {"status": "TRAINED_AND_EVALUATED_NOT_DEPLOYED"}

        def crew2_reports(run_dir):
            self.calls.append("crew2_reports")
            return {"status": "REPORTS_CREATED_PENDING_HUMAN_REVIEW"}

        def crew2_agents_mock(run_dir, progress=None):
            self.calls.append("crew2_agents")
            return CREW2_OK

        coordinator = FreshRunCoordinator(source=workbook, runs_root=self.runs, upload=True, **kwargs)
        with patch("src.crew1_agents.run_agents", side_effect=crew1_agents_mock), \
             patch("src.crew1_contract_draft.run", side_effect=draft), \
             patch("src.crew1_contract_finalize.finalize", side_effect=finalize), \
             patch("src.crew2_train.train_run", side_effect=train), \
             patch("src.crew2_reports.write_reports", side_effect=crew2_reports), \
             patch("src.crew2_agents.run_agents", side_effect=crew2_agents_mock):
            make_flow(coordinator).kickoff()
        run_dir = coordinator.run_dir
        manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
        live = json.loads((run_dir / "live_status.json").read_text(encoding="utf-8"))
        return coordinator, manifest, live

    @staticmethod
    def states(live):
        return {name: entry["state"] for name, entry in live["stages"].items()}

    def test_original_input_follows_authorized_branch(self):
        coordinator, manifest, live = self.run_pipeline(self.valid)
        run_dir = coordinator.run_dir
        self.assertEqual(manifest["status"], "FULL_RUN_COMPLETE_NEEDS_HUMAN_REVIEW")
        self.assertEqual((manifest["pipeline_mode"], manifest["policy_version"]), ("V2_UPLOAD", "2.0.0"))
        self.assertTrue(manifest["crew2_reached"])
        # The registered copy, its receipt and the per-run profile all describe the selected file.
        stored = run_dir / "source" / "uploaded_workbook.xlsx"
        self.assertEqual(digest(stored), digest(self.valid))
        receipt = json.loads((run_dir / "source_receipt.json").read_text(encoding="utf-8"))
        profile = json.loads((run_dir / "crew1" / "source_profile.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["source_sha256"], digest(self.valid))
        self.assertEqual(profile["source_sha256"], receipt["source_sha256"])
        self.assertEqual(manifest["source_sha256"], receipt["source_sha256"])
        report = json.loads((run_dir / "handoff_candidate" / "validation_report.json").read_text(encoding="utf-8"))
        self.assertEqual((report["status"], report["policy_version"]), ("PASS", "2.0.0"))
        self.assertEqual(report["source_integrity"]["status"], "VERIFIED")
        self.assertEqual(report["inputs"]["source_receipt_path"], str(run_dir / "source_receipt.json"))
        self.assertEqual(len(self.verified["features"]), 15)
        # Contract drafting is the end of Crew 1, before the gate.
        self.assertEqual(self.calls, ["crew1_agents", "contract_draft", "finalize_and_gate",
                                      "crew2_training", "crew2_reports", "crew2_agents"])
        self.assertEqual(set(self.states(live).values()), {"COMPLETED"})
        self.assertEqual(live["overall"], "FULL_RUN_COMPLETE_NEEDS_HUMAN_REVIEW")

    def test_corrupted_input_follows_blocked_branch(self):
        coordinator, manifest, live = self.run_pipeline(self.bad)
        run_dir = coordinator.run_dir
        self.assertEqual(manifest["status"], "FAILED_VALIDATION")
        self.assertEqual(manifest["validation_status"], "FAIL")
        self.assertEqual(manifest["failed_rule_ids"], ["CSV-007"])
        self.assertFalse(manifest["crew2_reached"])
        self.assertFalse(manifest["model_training_started"])
        self.assertEqual(manifest["source_sha256"], digest(self.bad))
        self.assertNotEqual(digest(self.bad), digest(self.valid))
        report = json.loads((run_dir / "handoff_candidate" / "validation_report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["source_integrity"]["status"], "VERIFIED")
        self.assertEqual(report["failed_rules"][0]["column"], "SatisfactionScore")
        # Crew 1 and contract drafting completed; Crew 2 was never invoked.
        self.assertEqual(self.calls, ["crew1_agents", "contract_draft", "finalize_and_gate"])
        self.assertTrue((run_dir / "crew1" / "dataset_contract.json").is_file())
        self.assertFalse((run_dir / "crew2").exists())
        self.assertEqual(self.states(live), {
            "source_intake": "COMPLETED", "crew1_preparation": "COMPLETED", "crew1_agents": "COMPLETED",
            "contract_draft": "COMPLETED", "independent_gate": "FAILED",
            "crew2_modeling": "BLOCKED", "crew2_agents": "BLOCKED"})
        self.assertEqual(live["stages"]["independent_gate"]["detail"], "FAIL: CSV-007")
        self.assertEqual(live["overall"], "FAILED_VALIDATION")

    def test_fingerprint_mismatch_stops_before_crew1(self):
        coordinator, manifest, live = self.run_pipeline(self.valid, expected_sha256="0" * 64)
        self.assertEqual(manifest["status"], "FAILED_SOURCE_INTAKE")
        self.assertEqual(self.calls, [])
        self.assertFalse((coordinator.run_dir / "source_receipt.json").exists())
        self.assertFalse((coordinator.run_dir / "crew1").exists())
        self.assertEqual(self.states(live)["source_intake"], "FAILED")
        self.assertEqual({s for n, s in self.states(live).items() if n != "source_intake"}, {"BLOCKED"})

    def test_workbook_without_e_comm_sheet_is_refused(self):
        other = self.base / "selected" / "wrong_sheet.xlsx"
        workbook = openpyxl.Workbook()
        workbook.active.title = "Sheet1"
        workbook.save(other)
        _, manifest, _ = self.run_pipeline(other)
        self.assertEqual(manifest["status"], "FAILED_SOURCE_INTAKE")
        self.assertIn("E Comm", manifest["error"])
        self.assertEqual(self.calls, [])

    def test_runs_are_isolated_and_never_overwritten(self):
        first, _, _ = self.run_pipeline(self.valid)
        before = {p: digest(p) for p in first.run_dir.rglob("*") if p.is_file()}
        second, _, _ = self.run_pipeline(self.bad)
        self.assertNotEqual(first.run_dir, second.run_dir)
        self.assertEqual({p: digest(p) for p in first.run_dir.rglob("*") if p.is_file()}, before)
        with self.assertRaises(FileExistsError):
            FreshRunCoordinator(source=self.valid, runs_root=self.runs, upload=True,
                                run_id=first.run_id).begin()

    def test_upload_cannot_choose_its_policy(self):
        with self.assertRaisesRegex(ValueError, "registered V2 policy"):
            FreshRunCoordinator(source=self.valid, runs_root=self.runs, upload=True,
                                policy=ROOT / "contracts" / "production_policy_proposed.json")


class V2PreflightAndCompatibilityTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.valid = synthetic_workbook(self.base / "valid.xlsx")

    def test_upload_preflight(self):
        ready = preflight_upload(self.valid, check_dependencies=False)
        self.assertEqual((ready["status"], ready["policy_version"]), ("READY_FOR_FRESH_RUN", "2.0.0"))
        self.assertEqual(ready["source_sha256"], digest(self.valid))
        self.assertFalse(ready["gemini_called"])
        text = self.base / "notes.txt"
        text.write_text("x", encoding="utf-8")
        self.assertEqual(preflight_upload(text, check_dependencies=False)["status"], "BLOCKED")
        fake = self.base / "fake.xlsx"
        fake.write_bytes(b"not a zip")
        self.assertEqual(preflight_upload(fake, check_dependencies=False)["status"], "BLOCKED")

    def test_cli_upload_check_uses_v2_and_writes_nothing(self):
        with patch.object(full_pipeline, "_environment_problems", return_value=[]), \
             patch("builtins.print") as printed:
            code = full_pipeline.main(["--check", "--upload", str(self.valid)])
        self.assertEqual(code, 0)
        output = json.loads(printed.call_args[0][0])
        self.assertEqual((output["mode"], output["policy_version"]), ("V2_UPLOAD", "2.0.0"))
        self.assertEqual(sorted(p.name for p in self.base.iterdir()), ["valid.xlsx"])

    def test_cli_rejects_expected_hash_without_upload(self):
        with patch("sys.stderr"), self.assertRaises(SystemExit):
            full_pipeline.main(["--run", "--expected-sha256", "0" * 64])

    def test_v1_coordinator_is_unchanged(self):
        policy = self.base / "policy.json"
        policy.write_text(json.dumps({"policy_version": "1.0.0"}), encoding="utf-8")
        coordinator = FreshRunCoordinator(source=self.valid, policy=policy,
                                          runs_root=self.base / "runs", run_id="20261111T000000Z_0000abcd")
        coordinator.begin()
        manifest = json.loads((coordinator.run_dir / "run_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual((manifest["pipeline_mode"], manifest["policy_version"]), ("V1_PINNED_SOURCE", "1.0.0"))
        self.assertEqual(coordinator.source, self.valid.resolve())
        self.assertFalse((coordinator.run_dir / "live_status.json").exists())
        self.assertFalse((coordinator.run_dir / "source").exists())

    def test_business_agent_wording_says_pending_gate(self):
        source = inspect.getsource(crew1_agents.run_agents)
        self.assertNotIn("Crew 2 remains blocked", source)
        self.assertNotIn("NOT APPROVED FOR CREW 2", source)
        self.assertIn("is PENDING the independent deterministic gate", source)
        self.assertIn("do not claim Crew 2 is", source)


if __name__ == "__main__":
    unittest.main()
