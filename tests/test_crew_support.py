"""V3 Phase 1: Linux-safe report paths, optional token-usage recording, optional output-token cap.

No Gemini call, no network. The integration tests run the real crews and Flow with the same
offline scripted LLM used by tests/test_agent_progress.py.
"""
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from test_agent_progress import OfflineCrewMixin, ScriptedLLM  # noqa: E402
from test_v2_receipt_gate import synthetic_workbook  # noqa: E402

from src import crew2_train  # noqa: E402
from src import crew_support as cs  # noqa: E402
from src.crew1_eda import analyze  # noqa: E402
from src.crew1_prepare import run as prepare  # noqa: E402
from src.full_pipeline import FreshRunCoordinator, make_flow  # noqa: E402
from src.live_status import LiveStatus  # noqa: E402


class WorkingDirectoryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.original = os.getcwd()
        self.addCleanup(os.chdir, self.original)

    def test_changes_then_restores_directory(self):
        target = self.base / "run"
        target.mkdir()
        with cs.crew_working_directory(target) as inside:
            self.assertEqual(Path(os.getcwd()).resolve(), target.resolve())
            self.assertEqual(inside, target.resolve())
        self.assertEqual(os.getcwd(), self.original)

    def test_restores_directory_when_the_crew_raises(self):
        target = self.base / "run"
        target.mkdir()
        with self.assertRaises(RuntimeError):
            with cs.crew_working_directory(target):
                raise RuntimeError("crew failed")
        self.assertEqual(os.getcwd(), self.original)

    def test_missing_directory_is_refused_without_changing_anything(self):
        with self.assertRaises(FileNotFoundError):
            with cs.crew_working_directory(self.base / "nope"):
                pass
        self.assertEqual(os.getcwd(), self.original)

    def test_relative_input_is_resolved_to_an_absolute_directory(self):
        (self.base / "rel").mkdir()
        os.chdir(self.base)
        with cs.crew_working_directory("rel") as inside:
            self.assertTrue(inside.is_absolute())
            self.assertEqual(inside, (self.base / "rel").resolve())

    def test_nested_use_in_one_thread_is_allowed(self):
        a, b = self.base / "a", self.base / "b"
        a.mkdir(), b.mkdir()
        with cs.crew_working_directory(a):
            with cs.crew_working_directory(b):
                self.assertEqual(Path(os.getcwd()).resolve(), b.resolve())
            self.assertEqual(Path(os.getcwd()).resolve(), a.resolve())
        self.assertEqual(os.getcwd(), self.original)

    def test_two_threads_take_turns_instead_of_overlapping(self):
        a, b = self.base / "a", self.base / "b"
        a.mkdir(), b.mkdir()
        release, in_a, entered_b = threading.Event(), threading.Event(), threading.Event()
        seen = {}

        def first():
            with cs.crew_working_directory(a):
                in_a.set()
                release.wait(5)

        def second():
            in_a.wait(5)
            with cs.crew_working_directory(b):
                seen["cwd"] = Path(os.getcwd()).resolve()
                entered_b.set()

        t1, t2 = threading.Thread(target=first), threading.Thread(target=second)
        t1.start(), t2.start()
        self.assertTrue(in_a.wait(5))
        self.assertFalse(entered_b.wait(0.4), "second thread must wait while the first holds the directory")
        release.set()
        t1.join(5), t2.join(5)
        self.assertTrue(entered_b.is_set())
        self.assertEqual(seen["cwd"], b.resolve())
        self.assertEqual(os.getcwd(), self.original)

    def test_live_status_path_is_absolute_even_for_a_relative_run_dir(self):
        os.chdir(self.base)
        (self.base / "runs").mkdir()
        live = LiveStatus(Path("runs"), "r1")
        self.assertTrue(live.path.is_absolute())
        inner = self.base / "elsewhere"
        inner.mkdir()
        with cs.crew_working_directory(inner):  # a handler thread writing now must still hit runs/
            live.set_component("Data Quality Specialist", "RUNNING")
        self.assertTrue((self.base / "runs" / "live_status.json").is_file())
        self.assertFalse((inner / "live_status.json").exists())


class OutputTokenLimitTests(unittest.TestCase):
    def test_unset_or_blank_sends_nothing(self):
        self.assertEqual(cs.output_token_limit_kwargs({}), {})
        self.assertEqual(cs.output_token_limit_kwargs({cs.OUTPUT_TOKEN_ENV: "  "}), {})

    def test_valid_value_sets_both_names(self):
        self.assertEqual(cs.output_token_limit_kwargs({cs.OUTPUT_TOKEN_ENV: "8192"}),
                         {"max_output_tokens": 8192, "max_tokens": 8192})

    def test_boundaries(self):
        for value in (cs.MIN_OUTPUT_TOKENS, cs.MAX_OUTPUT_TOKENS):
            self.assertEqual(cs.output_token_limit_kwargs({cs.OUTPUT_TOKEN_ENV: str(value)})["max_output_tokens"], value)

    def test_unsafe_or_malformed_values_are_refused_not_ignored(self):
        for bad in ("abc", "8192.5", "0", "-5", "100", str(cs.MIN_OUTPUT_TOKENS - 1), str(cs.MAX_OUTPUT_TOKENS + 1)):
            with self.assertRaises(ValueError, msg=bad):
                cs.output_token_limit_kwargs({cs.OUTPUT_TOKEN_ENV: bad})

    def test_floor_leaves_room_for_the_largest_recorded_deliverable(self):
        # Largest recorded agent review: 4,424 bytes. At about 4 characters per token that is ~1,100 tokens.
        self.assertGreaterEqual(cs.MIN_OUTPUT_TOKENS, 4_424 // 4 * 1.8)

    def test_real_llm_object_accepts_and_applies_the_cap_offline(self):
        from crewai import LLM
        kwargs = cs.output_token_limit_kwargs({cs.OUTPUT_TOKEN_ENV: "4096"})
        llm = LLM(model="gemini/gemini-3.5-flash-lite", api_key="offline-dummy-key", temperature=0.2,
                  timeout=30, max_retries=0, **kwargs)  # construction only; no request is made
        self.assertEqual(llm.max_output_tokens, 4096)  # the attribute the native Gemini provider sends


class TokenUsageRecordTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.on = {cs.RECORD_USAGE_ENV: "1"}

    def result(self, **counters):
        base = dict(total_tokens=0, prompt_tokens=0, cached_prompt_tokens=0, completion_tokens=0,
                    reasoning_tokens=0, cache_creation_tokens=0, successful_requests=0)
        return SimpleNamespace(token_usage=SimpleNamespace(**{**base, **counters}))

    def test_off_by_default_writes_nothing(self):
        self.assertIsNone(cs.record_token_usage(self.result(total_tokens=5), self.dir, crew="crew1", model="m", env={}))
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_reported_counters_are_recorded_as_given(self):
        res = self.result(total_tokens=1500, prompt_tokens=1000, completion_tokens=400, reasoning_tokens=100,
                          successful_requests=6)
        record = cs.record_token_usage(res, self.dir, crew="crew1", model="gemini/x", env=self.on)
        self.assertTrue(record["counters_reported"])
        self.assertEqual(record["usage"]["total_tokens"], 1500)
        self.assertEqual(record["usage"]["reasoning_tokens"], 100)
        on_disk = json.loads((self.dir / cs.USAGE_FILE).read_text(encoding="utf-8"))
        self.assertEqual(on_disk, record)
        self.assertEqual(on_disk["model"], "gemini/x")

    def test_all_zero_counters_mean_not_reported_and_nothing_is_invented(self):
        record = cs.record_token_usage(self.result(), self.dir, crew="crew2", model="m", env=self.on)
        self.assertFalse(record["counters_reported"])
        self.assertIsNone(record["usage"])
        self.assertIn("no usage has been estimated", record["note"])

    def test_missing_or_garbage_counters_are_not_reported(self):
        for res in (SimpleNamespace(), SimpleNamespace(token_usage=None),
                    SimpleNamespace(token_usage=SimpleNamespace(total_tokens="12", prompt_tokens=True,
                                                                  completion_tokens=-4, successful_requests=None))):
            record = cs.record_token_usage(res, self.dir, crew="crew1", model="m", env=self.on)
            self.assertFalse(record["counters_reported"])
            self.assertIsNone(record["usage"])

    def test_configured_cap_is_recorded_alongside(self):
        env = {**self.on, cs.OUTPUT_TOKEN_ENV: "8192"}
        record = cs.record_token_usage(self.result(total_tokens=1, successful_requests=1), self.dir,
                                       crew="crew1", model="m", env=env)
        self.assertEqual(record["max_output_tokens_configured"], 8192)

    def test_unwritable_target_never_fails_the_run(self):
        record = cs.record_token_usage(self.result(total_tokens=1, successful_requests=1), self.dir / "missing",
                                       crew="crew1", model="m", env=self.on)
        self.assertTrue(record["counters_reported"])  # returned, just not persisted


class PipelineIntegrationTests(OfflineCrewMixin, unittest.TestCase):
    """Real Flow and crews with a scripted LLM; the report files must land in the run folder."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        (self.base / "selected").mkdir()
        self.valid = synthetic_workbook(self.base / "selected" / "valid.xlsx", rows=40)
        patcher = patch.object(crew2_train, "ROOT", self.base)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cwd = self.base / "elsewhere"  # a working directory unrelated to the run folder
        self.cwd.mkdir()
        self.original = os.getcwd()
        os.chdir(self.cwd)
        self.addCleanup(os.chdir, self.original)

    def run_pipeline(self, extra_env=None):
        self.offline()
        captured = []

        def build(**kw):
            captured.append(kw)
            return ScriptedLLM(model="scripted-offline")

        patcher = patch("crewai.LLM", side_effect=build)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, extra_env or {})
        env.start()
        self.addCleanup(env.stop)
        coordinator = FreshRunCoordinator(source=self.valid, runs_root=self.base / "runs", upload=True)
        real_train = crew2_train.train_run
        with patch("src.crew2_train.train_run",
                   side_effect=lambda run_dir, **kw: real_train(run_dir, forest_trees=8, **kw)):
            make_flow(coordinator).kickoff()
        manifest = json.loads((coordinator.run_dir / "run_manifest.json").read_text(encoding="utf-8"))
        return coordinator.run_dir, manifest, captured

    def test_reports_land_inside_the_run_folder_not_the_working_directory(self):
        run_dir, manifest, _ = self.run_pipeline()
        self.assertEqual(manifest["status"], "FULL_RUN_COMPLETE_NEEDS_HUMAN_REVIEW")
        for name in ("quality_review.md", "eda_interpretation.md", "insights.md"):
            self.assertTrue((run_dir / "crew1" / name).is_file(), name)
        for name in ("feature_engineering_review.md", "model_selection_review.md", "evaluation_review.md"):
            self.assertTrue((run_dir / "crew2" / "agent_reviews" / name).is_file(), name)
        self.assertEqual(os.getcwd(), str(self.cwd), "the working directory must be restored")
        self.assertEqual(list(self.cwd.iterdir()), [], "nothing may be written relative to the working directory")

    def test_no_stray_files_appear_in_the_crew_folders(self):
        run_dir, _, _ = self.run_pipeline()
        crew1 = {p.name for p in (run_dir / "crew1").iterdir()}
        self.assertEqual({n for n in crew1 if n.endswith((".md", ".jsonl"))},
                         {"quality_review.md", "eda_interpretation.md", "insights.md", "agent_tool_calls.jsonl"})
        self.assertNotIn(cs.USAGE_FILE, crew1)  # recording is off unless enabled
        crew2 = {p.name for p in (run_dir / "crew2" / "agent_reviews").iterdir()}
        self.assertEqual(crew2, {"feature_engineering_review.md", "model_selection_review.md",
                                 "evaluation_review.md", "agent_tool_calls.jsonl"})

    def test_default_llm_arguments_are_unchanged(self):
        _, _, captured = self.run_pipeline()
        self.assertTrue(captured)
        for kw in captured:
            self.assertEqual(set(kw), {"model", "api_key", "temperature", "timeout", "max_retries"})
            self.assertEqual((kw["temperature"], kw["timeout"], kw["max_retries"]), (0.2, 30, 0))

    def test_cap_is_applied_to_both_crews_only_when_configured(self):
        _, _, captured = self.run_pipeline({cs.OUTPUT_TOKEN_ENV: "8192"})
        self.assertEqual(len(captured), 2)
        for kw in captured:
            self.assertEqual(kw["max_output_tokens"], 8192)
            self.assertEqual((kw["temperature"], kw["timeout"], kw["max_retries"]), (0.2, 30, 0))

    def test_invalid_cap_stops_the_run_before_any_model_use(self):
        run_dir, manifest, _ = self.run_pipeline({cs.OUTPUT_TOKEN_ENV: "10"})
        self.assertEqual(manifest["status"], "FAILED_CREW1")

    def test_usage_recording_is_honest_when_the_counters_report_nothing(self):
        run_dir, _, _ = self.run_pipeline({cs.RECORD_USAGE_ENV: "1"})
        for path in (run_dir / "crew1" / cs.USAGE_FILE, run_dir / "crew2" / "agent_reviews" / cs.USAGE_FILE):
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertFalse(record["counters_reported"], path)  # the scripted LLM reports no usage
            self.assertIsNone(record["usage"])


class RelativePathsAndAsyncEventTests(OfflineCrewMixin, unittest.TestCase):
    """The reviewed assumptions behind crew_working_directory (see docs/v3_phase1/15_V3_PHASE1_OFFLINE_CORE.md)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.run_dir = self.base / "runs" / "r1"
        self.crew1 = self.run_dir / "crew1"
        prepare(source_path=synthetic_workbook(self.base / "book.xlsx"), output_dir=self.crew1)
        analyze(self.crew1)
        self.original = os.getcwd()
        os.chdir(self.base)
        self.addCleanup(os.chdir, self.original)

    def test_a_relative_run_directory_argument_still_lands_in_the_right_folder(self):
        # The crew CLIs accept --run-dir runs/<id>/crew1; relative input must survive the directory change.
        self.offline()
        from src.crew1_agents import run_agents
        live = LiveStatus(Path("runs") / "r1", "r1")                   # relative, like a hand-typed argument
        result = run_agents(Path("runs") / "r1" / "crew1", progress=live)
        self.assertEqual(result["status"], "AGENT_REVIEW_COMPLETE_NEEDS_HUMAN_REVIEW")
        for name in ("quality_review.md", "eda_interpretation.md", "insights.md"):
            self.assertTrue((self.crew1 / name).is_file(), name)
        self.assertTrue((self.run_dir / "live_status.json").is_file())
        self.assertFalse((self.crew1 / "live_status.json").exists(), "status must not follow the crew's directory")
        self.assertEqual(os.getcwd(), str(self.base))
        self.assertEqual(sorted(p.name for p in self.base.iterdir() if p.is_dir()), ["runs"])

    def test_event_handler_threads_write_status_to_the_absolute_path_while_the_directory_is_changed(self):
        self.offline()
        from src.crew1_agents import run_agents
        seen = []
        real_write = LiveStatus._write

        def spy(live_self):
            seen.append((threading.current_thread() is threading.main_thread(), os.getcwd(), live_self.path))
            return real_write(live_self)

        live = LiveStatus(self.run_dir, "r1")
        seen.clear()
        with patch.object(LiveStatus, "_write", spy):
            run_agents(self.crew1, progress=live)
        self.assertTrue(seen, "the crew must have reported progress")
        in_crew_dir = [entry for entry in seen if Path(entry[1]).resolve() == self.crew1.resolve()]
        self.assertTrue(in_crew_dir, "some status writes must happen while the crew's directory is current")
        self.assertTrue(any(not on_main for on_main, _, _ in seen),
                        "CrewAI delivers events on worker threads; those threads must be exercised")
        self.assertEqual({path for _, _, path in seen}, {self.run_dir.resolve() / "live_status.json"})
        self.assertEqual(json.loads((self.run_dir / "live_status.json").read_text(encoding="utf-8"))
                         ["components"]["Business Insights & Contract Specialist"]["state"], "COMPLETED")

    def test_a_crew_that_fails_still_restores_the_directory_and_writes_nothing_stray(self):
        self.offline(fail_role="Data Quality Specialist")
        from src.crew1_agents import run_agents
        with self.assertRaises(Exception):
            run_agents(self.crew1, progress=LiveStatus(self.run_dir, "r1"))
        self.assertEqual(os.getcwd(), str(self.base))
        self.assertEqual(sorted(p.name for p in self.base.iterdir() if p.is_dir()), ["runs"])


if __name__ == "__main__":
    unittest.main()
