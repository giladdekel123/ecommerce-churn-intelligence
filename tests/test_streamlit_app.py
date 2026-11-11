"""Phase 5 Streamlit interface tests (streamlit.testing AppTest).

The real app.py runs against temporary folders. Process creation is a FakeProcess,
so no pipeline, Gemini call or training ever starts.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phase5_support import CORRUPTED_GATE, PASS_GATE, FakeProcess, ROOT, isolate, write_run  # noqa: E402

from streamlit.testing.v1 import AppTest  # noqa: E402

from src import run_launcher  # noqa: E402
from test_v2_receipt_gate import synthetic_workbook  # noqa: E402

APP = str(ROOT / "app.py")
HISTORICAL_V1 = "20261107T175915Z_aaaabbbb"
OLD_FAILED = "20261110T120000Z_0dd0fa11"  # must match the real run-ID format to be listed


def texts(at, *kinds):
    return " ".join(str(e.value) for kind in kinds for e in getattr(at, kind))


class StreamlitAppTests(unittest.TestCase):
    def setUp(self):
        self.base = isolate(self)
        self.runs = run_launcher.RUNS_ROOT
        self.book = synthetic_workbook(self.base / "book.xlsx").read_bytes()
        self.other_book = synthetic_workbook(self.base / "other.xlsx", rows=12).read_bytes()

    def app(self, **state):
        at = AppTest.from_file(APP, default_timeout=60)
        for key, value in state.items():
            at.session_state[key] = value
        at.run()
        self.assertEqual(list(at.exception), [], [e.value for e in at.exception])
        return at

    def upload(self, at, name="Original.xlsx", data=None):
        # The uploader's key changes after "Start a new analysis", so address it by position.
        at.file_uploader[0].set_value((name, data if data is not None else self.book,
                                       "application/vnd.ms-excel")).run()
        return at

    def finish(self, at, run_id, gate=CORRUPTED_GATE, status="FAILED_VALIDATION"):
        write_run(self.runs, run_id, status=status, gate=gate)
        FakeProcess.instances[-1].alive = False
        at.run()
        return at

    # --- Final stabilization: uploaded workbook vs. finished run ------------
    def test_finished_run_is_shown_consistently_and_not_relaunchable(self):
        at = self.upload(self.app())
        run_id = self.start(at)
        self.finish(at, run_id)
        notice = texts(at, "info")
        self.assertIn(f"Already analyzed in run **{run_id}** — Finished: FAILED_VALIDATION", notice)
        self.assertNotIn("Not started", texts(at, "success"))  # no contradictory "Not started"
        self.assertIn(f"Run {run_id} finished: **FAILED_VALIDATION**", texts(at, "error"))
        at.checkbox(key="consent").check().run()
        self.assertTrue(at.button(key="start").disabled)  # same workbook: cannot be relaunched
        at.run()
        at.run()
        self.assertEqual(len(FakeProcess.instances), 1)

    def test_running_upload_shows_running_not_not_started(self):
        at = self.upload(self.app())
        run_id = self.start(at)
        self.assertIn(f"Already analyzed in run **{run_id}** — Running", texts(at, "info"))
        self.assertNotIn("Start a new analysis", [b.label for b in at.button])  # not while running

    def test_start_new_analysis_resets_to_a_clean_pending_state(self):
        at = self.upload(self.app())
        run_id = self.start(at)
        self.finish(at, run_id, gate=PASS_GATE, status="FULL_RUN_COMPLETE_NEEDS_HUMAN_REVIEW")
        at.button(key="reset").click().run()
        self.assertIn("Status: **Not started**", texts(at, "info"))
        self.assertEqual(at.file_uploader[0].value, None)        # workbook must be chosen again
        self.assertFalse(at.checkbox(key="consent").value)        # consent must be given again
        self.assertTrue(at.button(key="start").disabled)
        self.assertNotIn(run_id, texts(at, "subheader", "info", "success", "error"))
        self.assertEqual(len(FakeProcess.instances), 1)
        past = self.past(run_id)  # the finished run is kept and still viewable
        self.assertIn(f"Run results: {run_id}", texts(past, "subheader"))

    def test_new_workbook_is_a_separate_pending_analysis_with_fresh_consent(self):
        at = self.upload(self.app())
        run_id = self.start(at)
        self.finish(at, run_id)
        self.upload(at, name="Second.xlsx", data=self.other_book)
        self.assertIn("Uploaded: **Second.xlsx**", texts(at, "success"))
        self.assertIn("Not started", texts(at, "success"))
        self.assertFalse(at.checkbox(key="consent").value)
        self.assertTrue(at.button(key="start").disabled)
        self.assertNotIn(run_id, texts(at, "subheader", "info", "error"))
        at.checkbox(key="consent").check().run()
        at.button(key="start").click().run()
        self.assertEqual(len(FakeProcess.instances), 2)  # only after a new explicit click

    def start(self, at):
        at.checkbox(key="consent").check().run()
        at.button(key="start").click().run()
        return at.session_state["launched_run"]

    def past(self, run_id):
        at = self.app()
        at.toggle(key="view_past").set_value(True).run()
        at.selectbox(key="past_run").select(run_id).run()
        self.assertEqual(list(at.exception), [])
        return at

    def history(self):
        write_run(self.runs, HISTORICAL_V1, status="FULL_RUN_COMPLETE_NEEDS_HUMAN_REVIEW",
                  gate={"status": "PASS", "policy_version": "1.0.0", "failed_rules": []},
                  crew2_reached=True, training=True, v2=False)
        write_run(self.runs, OLD_FAILED, status="FAILED_VALIDATION", gate=CORRUPTED_GATE)

    def assert_clean(self, at):
        self.assertIn("Status: **Not started**", texts(at, "info"))
        self.assertFalse([s for s in at.subheader if str(s.value).startswith(("Run results", "Live execution"))])
        self.assertEqual(list(at.metric), [])
        self.assertEqual(list(at.dataframe), [])
        page = texts(at, "markdown", "caption", "success", "error", "info")
        for run_id in (HISTORICAL_V1, OLD_FAILED):
            self.assertNotIn(run_id, page)

    # --- Correction 1-3: clean start, explicit past runs, new upload ---------
    def test_opens_clean_even_with_historical_runs(self):
        self.history()
        self.assert_clean(self.app())

    def test_past_runs_only_after_explicit_choice(self):
        self.history()
        at = self.app()
        at.toggle(key="view_past").set_value(True).run()
        self.assertIn("Choose a past run", texts(at, "info"))
        self.assertEqual(list(at.metric), [])  # toggle alone shows nothing
        at.selectbox(key="past_run").select(HISTORICAL_V1).run()
        self.assertIn(f"Run results: {HISTORICAL_V1}", texts(at, "subheader"))
        at.toggle(key="view_past").set_value(False).run()
        self.assert_clean(at)  # leaving past-run view returns to the clean dashboard

    def test_new_upload_hides_the_previous_finished_run(self):
        at = self.upload(self.app())
        run_id = self.start(at)
        write_run(self.runs, run_id, status="FAILED_VALIDATION", gate=CORRUPTED_GATE)
        FakeProcess.instances[0].alive = False
        at.run()
        self.assertIn(f"Run results: {run_id}", texts(at, "subheader"))
        self.upload(at, name="Second.xlsx", data=self.other_book)
        self.assertIn("Uploaded: **Second.xlsx**", texts(at, "success"))
        self.assert_clean(at)
        self.assertNotIn(run_id, texts(at, "markdown", "caption", "info", "error"))

    def test_refresh_after_a_finished_run_opens_clean(self):
        at = self.upload(self.app())
        self.start(at)
        FakeProcess.instances[0].alive = False
        self.assert_clean(self.app())  # new session: nothing active, so nothing old is shown

    # --- Upload, consent, launch -------------------------------------------
    def test_start_requires_upload_and_explicit_consent(self):
        at = self.app()
        self.assertTrue(at.button(key="start").disabled)
        self.upload(at)
        self.assertIn("Uploaded: **Original.xlsx**", texts(at, "success"))
        self.assertIn("Not started", texts(at, "success"))
        self.assertTrue(at.button(key="start").disabled)  # uploaded, but no consent yet
        self.assertEqual(FakeProcess.instances, [])      # uploading never launches
        at.checkbox(key="consent").check().run()
        self.assertFalse(at.button(key="start").disabled)
        self.assertEqual(FakeProcess.instances, [])      # consent alone never launches

    def test_start_launches_once_and_disables_further_clicks(self):
        at = self.upload(self.app())
        run_id = self.start(at)
        self.assertEqual(len(FakeProcess.instances), 1)
        self.assertEqual(run_id, json.loads(run_launcher.lock_path().read_text(encoding="utf-8"))["run_id"])
        self.assertTrue(at.button(key="start").disabled)
        self.assertIn("is running", texts(at, "info"))
        at.run()
        at.run()  # further reruns (e.g. auto refresh) never launch again
        self.assertEqual(len(FakeProcess.instances), 1)

    # --- Correction 4: only the current run; reconnect after refresh --------
    def test_current_run_shows_only_its_own_results(self):
        self.history()
        at = self.upload(self.app())
        run_id = self.start(at)
        write_run(self.runs, run_id, status="FAILED_VALIDATION", gate=CORRUPTED_GATE, live_updates=[
            ("comp", "Independent Validation Gate", "FAILED", "FAIL: CSV-007")])
        FakeProcess.instances[0].alive = False
        at.run()
        self.assertIn(f"Run results: {run_id}", texts(at, "subheader"))
        page = texts(at, "markdown", "caption", "success", "error", "info", "subheader")
        for other in (HISTORICAL_V1, OLD_FAILED):
            self.assertNotIn(other, page)
        self.assertEqual(at.dataframe[0].value.iloc[0]["Rule"], "CSV-007")

    def test_refresh_reconnects_to_the_active_run(self):
        self.history()
        at = self.upload(self.app())
        run_id = self.start(at)
        fresh = self.app()  # a brand-new browser session / page refresh
        self.assertIn(f"Run {run_id} is running", texts(fresh, "info"))
        self.assertIn("Live execution monitor", texts(fresh, "subheader"))
        self.assertTrue(fresh.button(key="start").disabled)
        self.assertEqual(len(FakeProcess.instances), 1)
        self.assertNotIn(HISTORICAL_V1, texts(fresh, "subheader", "caption"))

    def test_monitor_shows_real_component_states(self):
        run_id = "20261111T090000Z_aaaa0001"
        run_launcher.lock_path().write_text(json.dumps({"run_id": run_id, "pid": 1}), encoding="utf-8")
        FakeProcess.instances.append(FakeProcess([]))
        run_launcher._PROCESSES[run_id] = FakeProcess.instances[-1]
        write_run(self.runs, run_id, status="CREW1_COMPLETE_PENDING_GATE", live_updates=[
            ("stage", "crew1_preparation", "COMPLETED", None),
            ("comp", "Data Quality Specialist", "COMPLETED", None),
            ("comp", "Exploratory Data Analyst", "RUNNING", None)])
        at = self.app()
        cards = texts(at, "markdown")
        self.assertIn("**Data Quality Specialist**  \n✅ Completed", cards)
        self.assertIn("**Exploratory Data Analyst**  \n🔄 Running", cards)
        self.assertIn("**Business Insights & Contract Specialist**  \n⏳ Pending", cards)
        self.assertIn("**Independent Validation Gate**  \n⏳ Pending", cards)
        self.assertIn("Prepare clean_data.csv, per-run source profile, EDA: ✅ Completed", cards)
        self.assertNotIn("%", cards)  # no invented percentages

    def test_launch_that_never_created_a_run(self):
        at = self.upload(self.app())
        self.start(at)
        FakeProcess.instances[0].alive = False
        at.run()
        self.assertIn("did not start", texts(at, "error"))
        self.assertFalse(at.button(key="start").disabled)  # lock released; a new attempt is allowed

    # --- Validation outcomes (via an explicitly chosen past run) -------------
    def test_failed_gate_display_for_corrupted_workbook(self):
        run_id = "20261111T090000Z_bbbb0002"
        folder = write_run(self.runs, run_id, status="FAILED_VALIDATION", gate=CORRUPTED_GATE, live_updates=[
            ("comp", "Independent Validation Gate", "FAILED", "FAIL: CSV-007"),
            *[("comp", n, "BLOCKED", "Not started: no independent gate PASS") for n in (
                "Feature Engineering Specialist", "Machine Learning Engineer", "Model Evaluation Specialist")]])
        # A stale metrics file must never be shown for a failed run.
        (folder / "crew2").mkdir()
        (folder / "crew2" / "model_metrics.json").write_text(json.dumps({"winner": "STALE"}), encoding="utf-8")
        at = self.past(run_id)
        row = at.dataframe[0].value.iloc[0]
        self.assertEqual((row["Rule"], row["Column"], row["Observed value(s)"], row["Approved limit"]),
                         ("CSV-007", "SatisfactionScore", "9", "Approved maximum: 5"))
        metrics = {m.label: m.value for m in at.metric}
        self.assertEqual((metrics["Crew 2"], metrics["Model training"]), ("Blocked", "Not started"))
        self.assertIn("FAILED_VALIDATION", texts(at, "error"))
        self.assertIn("Source integrity: **VERIFIED**", texts(at, "success"))
        self.assertIn("⛔ Blocked", texts(at, "markdown"))
        self.assertNotIn("STALE", texts(at, "success", "markdown", "info"))
        self.assertIn("No verified completed-model metrics", texts(at, "info"))

    def test_gate_pass_display(self):
        run_id = "20261111T090000Z_cccc0003"
        write_run(self.runs, run_id, status="FULL_RUN_COMPLETE_NEEDS_HUMAN_REVIEW", gate=PASS_GATE,
                  crew2_reached=True, training=True)
        at = self.past(run_id)
        self.assertIn("Independent gate: **PASS**", texts(at, "success"))
        metrics = {m.label: m.value for m in at.metric}
        self.assertEqual((metrics["Crew 2"], metrics["Model training"]), ("Reached", "Started"))

    def test_interrupted_run_is_not_shown_as_complete(self):
        run_id = "20261111T090000Z_dddd0004"
        write_run(self.runs, run_id, status="TRAINING_IN_PROGRESS_NOT_DEPLOYED", crew2_reached=True)
        at = self.past(run_id)
        self.assertIn("INTERRUPTED", texts(at, "error"))
        self.assertNotIn("Completed automatically", texts(at, "info"))

    def test_invalid_upload_is_rejected_without_launch(self):
        at = self.upload(self.app(), name="fake.xlsx", data=b"definitely not a workbook")
        self.assertIn("Upload rejected", texts(at, "error"))
        at.checkbox(key="consent").check().run()
        self.assertTrue(at.button(key="start").disabled)
        self.assertEqual(FakeProcess.instances, [])

    def test_historical_v1_run_and_no_demo_interface(self):
        self.history()
        at = self.past(HISTORICAL_V1)
        self.assertNotIn("Live execution monitor", texts(at, "subheader"))
        self.assertIn("Pipeline: V1_PINNED_SOURCE", texts(at, "caption"))
        self.assertNotIn("Demonstrate validation failure", [b.label for b in at.button])
        self.assertEqual([t.label for t in at.tabs], [
            "Overview", "Crew 1 · Data & EDA", "Crew 2 · Models & reviews", "Validation gate report"])


if __name__ == "__main__":
    unittest.main()
