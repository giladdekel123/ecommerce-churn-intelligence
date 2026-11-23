"""V3 Phase 2, end to end and offline: socket protocol -> service -> real Flow/crews/gate/training -> answer.

The real pipeline code runs (agents, tasks, tools, events, independent gate, training on 40 synthetic rows);
only the LLM is the scripted offline stand-in. Nothing calls Gemini or the network.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_support import claims, token  # noqa: E402
from test_live_ipc import wire  # noqa: E402
import test_live_offline_pipeline as offline  # noqa: E402

from live.projection import contains_leak  # noqa: E402


class IpcPipelineTests(unittest.TestCase):
    def setUp(self):
        # Reuse the offline wiring (real Flow, scripted LLM) without collecting the parent's own tests.
        self.h = offline.LiveOfflineEndToEndTests("test_pass_scenario_runs_gate_crew2_and_projects_safely")
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.client = wire(self.h.service)

    def run_through_boundary(self, scenario):
        started = self.client.start(claims(), scenario, token())
        self.assertEqual((started["ok"], started["status"]), (True, "STARTED"), started)
        self.assertEqual([a["action"] for a in self.h.supervisor.enforce()], ["FINISHED"])
        answer = self.client.status(claims(), started["run_id"])
        self.assertTrue(answer["ok"], answer)
        return started["run_id"], answer["view"], answer

    def assert_boundary_is_clean(self, answer):
        dumped = json.dumps(answer)
        self.assertFalse(contains_leak(dumped))
        for needle in (str(self.h.fx.base), "run_manifest", "live_status", "source_receipt", "offline-test-key",
                       "request_token", "100000000000000000001"):
            self.assertNotIn(needle, dumped, needle)

    def test_pass_permits_crew2_and_reports_arrive_through_the_boundary(self):
        run_id, view, answer = self.run_through_boundary("pass")
        self.assertEqual(view["gate"]["status"], "PASS")
        self.assertTrue(view["outcome"]["crew2_reached"] and view["outcome"]["model_training_started"])
        self.assertEqual([c["state"] for c in view["components"]], ["COMPLETED"] * 7)
        self.assertEqual(len(view["reports"]), 6)
        self.assertIn("winner", view["metrics"])
        self.assert_boundary_is_clean(answer)

    def test_fail_blocks_crew2_through_the_boundary(self):
        run_id, view, answer = self.run_through_boundary("fail")
        self.assertEqual(view["gate"]["status"], "FAIL")
        self.assertEqual(view["gate"]["findings"][0]["rule_id"], "CSV-007")
        self.assertFalse(view["outcome"]["crew2_reached"] or view["outcome"]["model_training_started"])
        self.assertEqual(len(view["reports"]), 3)
        self.assertIsNone(view["metrics"])
        self.assertFalse((self.h.fx.base / "runs" / run_id / "crew2").exists())
        self.assert_boundary_is_clean(answer)

    def test_a_repeated_request_through_the_boundary_reattaches(self):
        tok = token()
        first = self.client.start(claims(), "fail", tok)
        again = self.client.start(claims(), "fail", tok)
        self.assertEqual((first["status"], again["status"], again["run_id"]), ("STARTED", "REATTACHED", first["run_id"]))
        self.assertEqual(len([p for p in (self.h.fx.base / "runs").iterdir() if p.is_dir()]), 1)


if __name__ == "__main__":
    unittest.main()
