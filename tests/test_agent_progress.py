"""Architecture V2 Phase 4: per-agent status from real CrewAI task events.

The real crew code runs (real Agents, Tasks, tools, sequential Crew, and the real
TaskStarted/Completed/Failed events). Only the LLM is replaced by an offline,
scripted BaseLLM, so there are no Gemini or network calls. Workbooks are
synthetic; the only model training is on 40 synthetic rows.
"""
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from crewai.llms.base_llm import BaseLLM

from test_v2_receipt_gate import synthetic_workbook

from src import crew2_train
from src.crew1_eda import analyze
from src.crew1_prepare import run as prepare
from src.full_pipeline import FreshRunCoordinator, make_flow
from src.live_status import COMPONENTS, GATE, LiveStatus
from src.make_corrupted_workbook import corrupt_workbook

CREW1 = [name for name, crew, _ in COMPONENTS if crew == "crew1"]
CREW2 = [name for name, crew, _ in COMPONENTS if crew == "crew2"]


class ScriptedLLM(BaseLLM):
    """Offline stand-in for Gemini: requests the agent's tool once, then answers.

    fail_role: raise inside that agent's task (simulates a provider/agent error).
    skip_tool_role: that agent answers without calling its tool (missing evidence).
    """
    fail_role: str | None = None
    skip_tool_role: str | None = None

    def call(self, messages, *args, **kwargs):
        text = messages if isinstance(messages, str) else "\n".join(
            str(m.get("content", "")) if isinstance(m, dict) else str(m) for m in messages)
        if self.fail_role and self.fail_role in text:
            raise RuntimeError(f"scripted failure in {self.fail_role}")
        tool = re.search(r"Tool Name: (.+)", text)
        already_observed = "Observation:" in text.split("Current Task")[-1]
        skip = self.skip_tool_role and self.skip_tool_role in text
        if tool and not already_observed and not skip:
            return f"Thought: I need the verified evidence\nAction: {tool.group(1).strip()}\nAction Input: {{}}"
        return "Thought: I now know the final answer\nFinal Answer: Synthetic review based on verified tool output."

    def supports_function_calling(self):
        return False

    def supports_stop_words(self):
        return True

    def get_context_window_size(self):
        return 16000


class OfflineCrewMixin:
    """Route every CrewAI LLM construction to ScriptedLLM; never read the real .env key."""

    def offline(self, **script):
        for patcher in (
            patch.dict(os.environ, {"GEMINI_API_KEY": "offline-test-key", "CREW1_GEMINI_MODEL": "gemini/offline",
                                    "CREW2_GEMINI_MODEL": "gemini/offline", "CREWAI_DISABLE_TELEMETRY": "true",
                                    "CREWAI_TRACING_ENABLED": "false", "OTEL_SDK_DISABLED": "true"}),
            patch("dotenv.load_dotenv", return_value=False),
            patch("crewai.LLM", side_effect=lambda **kw: ScriptedLLM(model="scripted-offline", **script)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)


def components(run_dir: Path) -> dict:
    data = json.loads((Path(run_dir) / "live_status.json").read_text(encoding="utf-8"))
    return data["components"]


def states(run_dir: Path) -> dict:
    return {name: entry["state"] for name, entry in components(run_dir).items()}


class LiveStatusTransitionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.live = LiveStatus(Path(tmp.name), "r1")
        self.name = CREW1[0]

    def test_seven_components_tracked_independently_in_order(self):
        self.assertEqual(list(self.live.data["components"]), [
            "Data Quality Specialist", "Exploratory Data Analyst", "Business Insights & Contract Specialist",
            "Independent Validation Gate", "Feature Engineering Specialist", "Machine Learning Engineer",
            "Model Evaluation Specialist"])
        self.live.set_component(CREW1[1], "RUNNING")
        self.assertEqual([e["state"] for e in self.live.data["components"].values()].count("RUNNING"), 1)

    def test_forward_only_transitions(self):
        self.assertTrue(self.live.set_component(self.name, "RUNNING", at="2026-11-11T08:00:00+00:00"))
        self.assertTrue(self.live.set_component(self.name, "COMPLETED", at="2026-11-11T08:00:05+00:00"))
        self.assertFalse(self.live.set_component(self.name, "RUNNING"))   # no going back
        self.assertFalse(self.live.set_component(self.name, "FAILED"))    # not without evidence downgrade
        self.assertFalse(self.live.set_component(self.name, "BLOCKED"))
        entry = self.live.data["components"][self.name]
        self.assertEqual((entry["state"], entry["started_at"], entry["finished_at"]),
                         ("COMPLETED", "2026-11-11T08:00:00+00:00", "2026-11-11T08:00:05+00:00"))

    def test_late_start_event_keeps_completion(self):
        # CrewAI's thread pool may deliver "completed" before "started".
        self.live.set_component(self.name, "COMPLETED", at="2026-11-11T08:00:05+00:00")
        self.assertFalse(self.live.set_component(self.name, "RUNNING", at="2026-11-11T08:00:00+00:00"))
        entry = self.live.data["components"][self.name]
        self.assertEqual((entry["state"], entry["started_at"]), ("COMPLETED", "2026-11-11T08:00:00+00:00"))

    def test_evidence_downgrade_only_when_explicit(self):
        self.live.set_component(self.name, "COMPLETED")
        self.assertTrue(self.live.set_component(self.name, "FAILED", "no tool call", downgrade=True))
        self.assertEqual(self.live.component_state(self.name), "FAILED")

    def test_concurrent_event_writes_keep_a_valid_file(self):
        names = [n for n, _, _ in COMPONENTS]
        threads = [threading.Thread(target=self.live.set_component, args=(names[i % 7], "RUNNING"))
                   for i in range(28)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        on_disk = json.loads(self.live.path.read_text(encoding="utf-8"))
        self.assertEqual({e["state"] for e in on_disk["components"].values()}, {"RUNNING"})


class Crew1AgentEventTests(OfflineCrewMixin, unittest.TestCase):
    """Real crew1_agents.run_agents with real CrewAI events."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        run_dir = Path(tmp.name) / "runs" / "r1"
        self.crew1 = run_dir / "crew1"
        book = synthetic_workbook(Path(tmp.name) / "book.xlsx")
        prepare(source_path=book, output_dir=self.crew1)
        analyze(self.crew1)
        self.live = LiveStatus(run_dir, "r1")

    def run_crew1(self, **script):
        self.offline(**script)
        from src.crew1_agents import run_agents
        return run_agents(self.crew1, progress=self.live)

    def test_all_three_agents_complete_from_real_events(self):
        result = self.run_crew1()
        self.assertEqual(result["status"], "AGENT_REVIEW_COMPLETE_NEEDS_HUMAN_REVIEW")
        for name in CREW1:
            entry = self.live.data["components"][name]
            self.assertEqual((entry["state"], entry["source"]), ("COMPLETED", "crewai_task_event"), name)
            self.assertLessEqual(entry["started_at"], entry["finished_at"])
        self.assertEqual({self.live.component_state(n) for n in [GATE] + CREW2}, {"PENDING"})

    def test_failing_agent_is_failed_and_later_agents_blocked(self):
        with self.assertRaises(RuntimeError):
            self.run_crew1(fail_role="Exploratory Data Analyst")
        got = {n: self.live.component_state(n) for n in CREW1}
        self.assertEqual(got, {"Data Quality Specialist": "COMPLETED", "Exploratory Data Analyst": "FAILED",
                               "Business Insights & Contract Specialist": "BLOCKED"})
        self.assertIn("scripted failure", self.live.data["components"]["Exploratory Data Analyst"]["detail"])

    def test_task_without_required_tool_call_is_not_completed(self):
        result = self.run_crew1(skip_tool_role="Business Insights & Contract Specialist")
        self.assertEqual(result["status"], "AGENT_REVIEW_INCOMPLETE")
        entry = self.live.data["components"]["Business Insights & Contract Specialist"]
        self.assertEqual((entry["state"], entry["source"]), ("FAILED", "evidence_check"))
        self.assertIn("not called", entry["detail"])
        self.assertEqual(self.live.component_state("Data Quality Specialist"), "COMPLETED")


class PipelineAgentEventTests(OfflineCrewMixin, unittest.TestCase):
    """Real Flow + real crews (scripted LLM) + real training on 40 synthetic rows only."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        (self.base / "selected").mkdir()
        self.valid = synthetic_workbook(self.base / "selected" / "valid.xlsx", rows=40)
        self.bad = self.base / "selected" / "corrupted.xlsx"
        corrupt_workbook(self.valid, self.bad)
        root_patch = patch.object(crew2_train, "ROOT", self.base)
        root_patch.start()
        self.addCleanup(root_patch.stop)

    def run_pipeline(self, workbook: Path, **script):
        self.offline(**script)
        coordinator = FreshRunCoordinator(source=workbook, runs_root=self.base / "runs", upload=True)
        real_train = crew2_train.train_run
        with patch("src.crew2_train.train_run",
                   side_effect=lambda run_dir, **kw: real_train(run_dir, forest_trees=8, **kw)) as train:
            make_flow(coordinator).kickoff()
        manifest = json.loads((coordinator.run_dir / "run_manifest.json").read_text(encoding="utf-8"))
        return coordinator.run_dir, manifest, train

    def test_successful_run_tracks_all_seven(self):
        run_dir, manifest, _ = self.run_pipeline(self.valid)
        self.assertEqual(manifest["status"], "FULL_RUN_COMPLETE_NEEDS_HUMAN_REVIEW")
        self.assertTrue(manifest["crew2_reached"] and manifest["model_training_started"])
        comps = components(run_dir)
        self.assertEqual({e["state"] for e in comps.values()}, {"COMPLETED"})
        self.assertEqual(comps[GATE]["detail"], "PASS")
        for name in CREW1 + CREW2:
            self.assertEqual(comps[name]["source"], "crewai_task_event", name)
        # Crew 2 agents ran only after the gate completed.
        self.assertLessEqual(comps[GATE]["finished_at"], comps[CREW2[0]]["started_at"])

    def test_gate_failure_blocks_crew2_agents_without_training(self):
        run_dir, manifest, train = self.run_pipeline(self.bad)
        self.assertEqual(manifest["status"], "FAILED_VALIDATION")
        self.assertFalse(manifest["crew2_reached"])
        self.assertFalse(manifest["model_training_started"])
        train.assert_not_called()
        self.assertFalse((run_dir / "crew2").exists())
        self.assertEqual(states(run_dir), {**{n: "COMPLETED" for n in CREW1}, GATE: "FAILED",
                                           **{n: "BLOCKED" for n in CREW2}})
        self.assertEqual(components(run_dir)[GATE]["detail"], "FAIL: CSV-007")

    def test_crew2_agent_failure_is_reported_truthfully(self):
        run_dir, manifest, train = self.run_pipeline(self.valid, fail_role="Machine Learning Engineer")
        self.assertEqual(manifest["status"], "FAILED_CREW2")
        self.assertTrue(manifest["model_training_started"])  # training genuinely ran before the reviews
        train.assert_called_once()
        self.assertEqual({n: s for n, s in states(run_dir).items() if n in CREW2},
                         {"Feature Engineering Specialist": "COMPLETED", "Machine Learning Engineer": "FAILED",
                          "Model Evaluation Specialist": "BLOCKED"})
        self.assertEqual(states(run_dir)[GATE], "COMPLETED")

    def test_crew1_failure_blocks_gate_and_crew2(self):
        run_dir, manifest, train = self.run_pipeline(self.valid, fail_role="Data Quality Specialist")
        self.assertEqual(manifest["status"], "FAILED_CREW1")
        train.assert_not_called()
        got = states(run_dir)
        self.assertEqual(got["Data Quality Specialist"], "FAILED")
        self.assertEqual({got[n] for n in CREW1[1:] + [GATE] + CREW2}, {"BLOCKED"})

    def test_v1_runs_pass_no_progress_tracker(self):
        coordinator = FreshRunCoordinator(source=self.valid, runs_root=self.base / "runs")
        self.assertEqual(coordinator._progress_kwargs(), {})


if __name__ == "__main__":
    unittest.main()
