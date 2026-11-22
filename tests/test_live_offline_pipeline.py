"""V3 live core, end to end and offline: service -> real Flow/crews/gate/training -> projection.

The pipeline code is the real one (real CrewAI agents, tasks, tools and events, the independent
gate, and training on 40 synthetic rows). Only the LLM is replaced by the scripted offline stand-in,
so nothing calls Gemini or the network. The two scenario workbooks are synthetic and pinned by hash.
"""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_support import Fixture, claims, token  # noqa: E402
from test_agent_progress import OfflineCrewMixin  # noqa: E402
from test_v2_receipt_gate import synthetic_workbook  # noqa: E402

from src import crew2_train  # noqa: E402
from src.full_pipeline import FreshRunCoordinator, make_flow  # noqa: E402
from src.make_corrupted_workbook import corrupt_workbook  # noqa: E402
from live.projection import contains_leak  # noqa: E402
from live.service import LiveService  # noqa: E402
from live.supervisor import Supervisor  # noqa: E402


class LiveOfflineEndToEndTests(OfflineCrewMixin, unittest.TestCase):
    def setUp(self):
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        work = Path(tmp.name)
        valid = synthetic_workbook(work / "valid.xlsx", rows=40)
        bad = work / "bad.xlsx"
        corrupt_workbook(valid, bad)
        self.fx = Fixture(self, scenario_bytes={"pass": valid.read_bytes(), "fail": bad.read_bytes()},
                          launch_fn=self.run_in_process)
        patcher = patch.object(crew2_train, "ROOT", self.fx.base)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.offline()
        # Real run-state evidence (manifest + lock), not the fixture's fake.
        self.supervisor = Supervisor(self.fx.ledger, self.fx.kill, timeout_s=300, runs_root=self.fx.base / "runs",
                                     now=self.fx.clock)
        self.service = LiveService(self.fx.config, self.fx.ledger, self.fx.registry, self.fx.kill, self.supervisor,
                                   launch_fn=self.run_in_process, now=self.fx.clock)

    def run_in_process(self, upload):
        coordinator = FreshRunCoordinator(source=Path(upload["path"]), runs_root=self.fx.base / "runs", upload=True)
        real_train = crew2_train.train_run
        with patch("src.crew2_train.train_run",
                   side_effect=lambda run_dir, **kw: real_train(run_dir, forest_trees=8, **kw)):
            make_flow(coordinator).kickoff()
        return {"run_id": coordinator.run_id, "pid": os.getpid()}

    def run_scenario(self, scenario_id):
        started = self.service.start(claims(), scenario_id, token())
        self.assertEqual(started.status, "STARTED", started)
        self.assertEqual([a["action"] for a in self.supervisor.enforce()], ["FINISHED"])
        return started.run_id, self.service.status(claims(), started.run_id)

    def assert_nothing_leaks(self, view):
        dumped = json.dumps(view)
        self.assertFalse(contains_leak(dumped))
        for needle in (str(self.fx.base), self.fx.base.name, "run_manifest", "live_status", "source_receipt",
                       "agent_tool_calls", "offline-test-key"):
            self.assertNotIn(needle, dumped, needle)

    def test_pass_scenario_runs_gate_crew2_and_projects_safely(self):
        run_id, view = self.run_scenario("pass")
        self.assertEqual(view["run_state"], "FINISHED")
        self.assertEqual(view["gate"]["status"], "PASS")
        self.assertEqual(view["gate"]["source_integrity"], "VERIFIED")
        self.assertEqual([c["state"] for c in view["components"]], ["COMPLETED"] * 7)
        self.assertEqual(len(view["reports"]), 6)
        self.assertTrue(all(r["text"].strip() for r in view["reports"]))
        self.assertTrue(view["outcome"]["crew2_reached"] and view["outcome"]["model_training_started"])
        self.assertFalse(view["outcome"]["model_deployed"])
        self.assertIn("winner", view["metrics"])
        self.assertEqual(view["limit_state"], "FINISHED")
        self.assert_nothing_leaks(view)

    def test_fail_scenario_blocks_crew2_and_shows_the_finding(self):
        run_id, view = self.run_scenario("fail")
        self.assertEqual(view["gate"]["status"], "FAIL")
        self.assertEqual(view["gate"]["findings"][0]["rule_id"], "CSV-007")
        states = {c["name"]: c["state"] for c in view["components"]}
        self.assertEqual([states[n] for n in ("Data Quality Specialist", "Exploratory Data Analyst",
                                              "Business Insights & Contract Specialist")], ["COMPLETED"] * 3)
        self.assertEqual(states["Independent Validation Gate"], "FAILED")
        self.assertEqual([states[n] for n in ("Feature Engineering Specialist", "Machine Learning Engineer",
                                              "Model Evaluation Specialist")], ["BLOCKED"] * 3)
        self.assertEqual(len(view["reports"]), 3)
        self.assertIsNone(view["metrics"])
        self.assertFalse(view["outcome"]["crew2_reached"] or view["outcome"]["model_training_started"])
        self.assertFalse((self.fx.base / "runs" / run_id / "crew2").exists())   # no model was trained
        self.assert_nothing_leaks(view)

    def test_a_refreshed_request_does_not_run_the_pipeline_twice(self):
        tok = token()
        first = self.service.start(claims(), "fail", tok)
        runs_before = sorted(p.name for p in (self.fx.base / "runs").iterdir())
        again = self.service.start(claims(), "fail", tok)
        self.assertEqual((first.status, again.status, again.run_id), ("STARTED", "REATTACHED", first.run_id))
        self.assertEqual(sorted(p.name for p in (self.fx.base / "runs").iterdir()), runs_before)


if __name__ == "__main__":
    unittest.main()
