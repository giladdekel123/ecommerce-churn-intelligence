"""V3 live core: the sanitized, allow-listed projection the browser receives."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phase5_support import CORRUPTED_GATE, PASS_GATE, write_run  # noqa: E402

from src import run_launcher  # noqa: E402
from live.projection import MAX_REPORT_BYTES, REPORTS, contains_leak, project_run, sanitize_text  # noqa: E402

RUN = "20270115T080000Z_00000001"
CREW1 = ["Data Quality Specialist", "Exploratory Data Analyst", "Business Insights & Contract Specialist"]
CREW2 = ["Feature Engineering Specialist", "Machine Learning Engineer", "Model Evaluation Specialist"]
# Fake credential-shaped values are assembled at run time so that no key-shaped literal sits in the repository
# (secret scanners would flag one even though it is fake).
FAKE_GOOGLE_KEY = "AI" + "zaSyD-" + "1234567890abcdefghijklmnopqrstu"
FAKE_JWT = ".".join(["ey" + "JhbGciOiJSUzI1NiJ9", "ey" + "JzdWIiOiIxMjM0NTYifQ", "c2lnbmF0dXJlMTIz"])
FORBIDDEN_IN_OUTPUT = ("AI" + "za", "@example.com", "Bearer ", "ey" + "J")


class SanitizerTests(unittest.TestCase):
    def test_paths_emails_and_keys_are_redacted(self):
        samples = [
            r"C:\Users\Someone\Documents\AI Final Churn\runs\x\crew1\clean_data.csv",
            "c:/Users/Someone/project/data.csv",
            "/home/deploy/live/runs/20270115T080000Z_00000001/live_status.json",
            "/var/tmp/v3/secret.txt", "/tmp/tmpab12/runs", "/Users/me/x", "/etc/passwd", "/mnt/c/Users/x",
            r"\\fileserver\share\x.xlsx",
            "contact owner@example.com now", "a.b+c@sub.example.co.uk",
            FAKE_GOOGLE_KEY,
            FAKE_JWT,
            "api_key=abc123", "GEMINI_API_KEY: sk-xyz", "GOOGLE_API_KEY=abc", "client_secret: xyz",
            "db_password=hunter2", "password = hunter2", "token: abcdef", "access_token=abc", "secret=foo",
            "Authorization: Bearer abcdefghijklmnopqrstuv",
        ]
        for text in samples:
            clean = sanitize_text(f"before {text} after")
            self.assertFalse(contains_leak(clean), (text, clean))
            self.assertIn("before", clean)
            self.assertIn("[redacted]", clean, text)

    def test_ordinary_report_text_is_preserved(self):
        text = ("**Row Count:** 5,630 rows (`row_count_before: 5630`). CSV-007 on `SatisfactionScore` = 9. "
                "Crew1/Crew2, and/or 12/34, F1 = 0.7578, SHA-256 db70f1e34bc5326887ea3fb3d2a0854e82c577d55ef3a76fdfb25328631bd44d.")
        self.assertEqual(sanitize_text(text), text)
        self.assertFalse(contains_leak(text))

    def test_token_counts_are_not_mistaken_for_secrets(self):
        text = "total_tokens: 1500, completion_tokens: 400, max_output_tokens: 8192"
        self.assertEqual(sanitize_text(text), text)

    def test_none_and_non_strings(self):
        self.assertIsNone(sanitize_text(None))
        self.assertEqual(sanitize_text(12345), "12345")

    def test_length_is_capped(self):
        self.assertEqual(len(sanitize_text("x" * 100_000, 500)), 500)


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.runs = self.base / "runs"
        self.runs.mkdir()
        for patcher in (patch.object(run_launcher, "RUNS_ROOT", self.runs),
                        patch.object(run_launcher, "LOGS_DIR", self.base / "logs"),
                        patch.object(run_launcher, "pid_alive", lambda pid: False)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def report(self, folder, relative, text):
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def make(self, **kw):
        return write_run(self.runs, kw.pop("run_id", RUN), **kw)

    def finished_pass(self, run_id=RUN):
        updates = [("component", n, "COMPLETED", None) for n in CREW1 + ["Independent Validation Gate"] + CREW2]
        folder = self.make(run_id=run_id, status="FULL_RUN_COMPLETE_NEEDS_HUMAN_REVIEW", gate=PASS_GATE,
                           crew2_reached=True, training=True, live_updates=updates)
        for name, (relative, _) in REPORTS.items():
            self.report(folder, relative, f"Review by {name}.")
        (folder / "crew2").mkdir(exist_ok=True)
        (folder / "crew2" / "model_metrics.json").write_text(json.dumps({
            "winner": "random_forest", "selection_rule": "CV F1", "total_rows": 40, "training_rows": 32, "test_rows": 8,
            "test_churn_count": 2, "customer_rows_should_never_appear": [[1, 2, 3]], "source_clean_data_sha256": "x",
            "holdout_once": {"random_forest": {"accuracy": 0.9, "precision": 0.8, "recall": 0.7, "f1": 0.75,
                                               "roc_auc": 0.9, "confusion_matrix": [[5, 1], [1, 1]], "extra": "no"}}}),
            encoding="utf-8")
        return folder

    def test_pass_run_projection(self):
        self.finished_pass()
        view = project_run(RUN, runs_root=self.runs)
        self.assertEqual(view["gate"]["status"], "PASS")
        self.assertEqual(len(view["components"]), 7)
        self.assertEqual({c["state"] for c in view["components"]}, {"COMPLETED"})
        self.assertEqual({r["component"] for r in view["reports"]}, set(REPORTS))
        self.assertEqual(view["outcome"], {"validation_status": "PASS", "crew2_reached": True,
                                           "model_training_started": True, "model_deployed": False})
        self.assertEqual(view["metrics"]["winner"], "random_forest")
        self.assertEqual(set(view["metrics"]["holdout"]["random_forest"]),
                         {"accuracy", "precision", "recall", "f1", "roc_auc", "confusion_matrix"})

    def test_fail_run_shows_the_finding_blocked_agents_and_no_metrics(self):
        updates = [("component", n, "COMPLETED", None) for n in CREW1]
        updates += [("component", "Independent Validation Gate", "FAILED", "FAIL: CSV-007")]
        updates += [("component", n, "BLOCKED", "Not started: no independent gate PASS") for n in CREW2]
        folder = self.make(status="FAILED_VALIDATION", gate=CORRUPTED_GATE, live_updates=updates)
        for name in CREW1:
            self.report(folder, REPORTS[name][0], f"Review by {name}.")
        view = project_run(RUN, runs_root=self.runs)
        self.assertEqual(view["gate"]["status"], "FAIL")
        finding = view["gate"]["findings"][0]
        self.assertEqual((finding["rule_id"], finding["column"], finding["observed"]), ("CSV-007", "SatisfactionScore", "9"))
        self.assertEqual(finding["approved_value"], 5)
        self.assertEqual([c["state"] for c in view["components"] if c["name"] in CREW2], ["BLOCKED"] * 3)
        self.assertEqual({r["component"] for r in view["reports"]}, set(CREW1))
        self.assertIsNone(view["metrics"])
        self.assertFalse(view["outcome"]["crew2_reached"])

    def test_reports_appear_only_for_completed_agents_as_they_finish(self):
        updates = [("component", CREW1[0], "COMPLETED", None), ("component", CREW1[1], "RUNNING", None)]
        folder = self.make(status="RUNNING", live_updates=updates)
        for name in CREW1:  # a half-written file for the running agent must not be shown
            self.report(folder, REPORTS[name][0], f"Review by {name}.")
        view = project_run(RUN, runs_root=self.runs)
        self.assertEqual([r["component"] for r in view["reports"]], [CREW1[0]])

    def test_a_failed_agents_leftover_file_is_not_shown(self):
        updates = [("component", CREW1[0], "FAILED", "Required evidence missing: quality_review.md not written")]
        folder = self.make(status="FAILED_CREW1", live_updates=updates)
        self.report(folder, REPORTS[CREW1[0]][0], "stale text")
        self.assertEqual(project_run(RUN, runs_root=self.runs)["reports"], [])

    def test_run_that_has_not_written_its_manifest_yet(self):
        (self.runs / RUN).mkdir()
        view = project_run(RUN, runs_root=self.runs)
        self.assertEqual((view["components"], view["reports"], view["gate"]), ([], [], None))

    def test_invalid_run_ids_are_rejected(self):
        for bad in ("../x", "20270115T080000Z_ZZZZZZZZ", "", "runs", "20270115T080000Z_00000001/../../x"):
            with self.assertRaises(ValueError, msg=bad):
                project_run(bad, runs_root=self.runs)

    def test_ledger_identity_is_not_exposed_but_the_limit_state_is(self):
        self.finished_pass()
        row = {"state": "TIMED_OUT", "detail": "exceeded the 300 s execution limit", "sub": "1000000000000000099",
               "request_token": "secrettokenvalue123456", "pid": 4242}
        view = project_run(RUN, runs_root=self.runs, ledger_row=row)
        self.assertEqual((view["limit_state"], view["limit_detail"]), ("TIMED_OUT", "exceeded the 300 s execution limit"))
        dumped = json.dumps(view)
        for secret in ("1000000000000000099", "secrettokenvalue123456", "4242"):
            self.assertNotIn(secret, dumped)

    def test_oversized_report_is_withheld_not_truncated_silently(self):
        updates = [("component", CREW1[0], "COMPLETED", None)]
        folder = self.make(status="RUNNING", live_updates=updates)
        self.report(folder, REPORTS[CREW1[0]][0], "x" * (MAX_REPORT_BYTES + 1))
        text = project_run(RUN, runs_root=self.runs)["reports"][0]["text"]
        self.assertTrue(text.startswith("[withheld"))

    def test_everything_that_could_leak_is_scrubbed_end_to_end(self):
        updates = [("component", CREW1[0], "COMPLETED", r"see C:\Users\Owner\runs\x and owner@example.com")]
        folder = self.make(status="RUNNING", live_updates=updates)
        self.report(folder, REPORTS[CREW1[0]][0],
                    f"Found /home/owner/live/.env with {FAKE_GOOGLE_KEY} and a@example.com")
        raw = (folder / "run_manifest.json").read_text(encoding="utf-8")  # the manifest has local fields
        view = project_run(RUN, runs_root=self.runs)
        dumped = json.dumps(view)
        for needle in FORBIDDEN_IN_OUTPUT + (str(self.base), "/home/owner", "C:\\\\Users", "run_manifest", "live_status.json"):
            self.assertNotIn(needle, dumped, needle)
        self.assertFalse(contains_leak(dumped))
        self.assertIn(RUN, dumped)
        self.assertTrue(raw)

    def test_output_has_only_allow_listed_top_level_keys(self):
        self.finished_pass()
        view = project_run(RUN, runs_root=self.runs)
        self.assertEqual(set(view), {"run_id", "run_state", "limit_state", "overall", "stages", "components", "gate",
                                     "outcome", "reports", "metrics"})
        self.assertEqual(set(view["components"][0]), {"name", "crew", "kind", "state", "started_at", "finished_at", "detail"})
        self.assertEqual(set(view["gate"]), {"status", "policy_version", "authorizes_crew2_training",
                                             "source_integrity", "findings"})
        self.assertNotIn("customer_rows_should_never_appear", json.dumps(view))


if __name__ == "__main__":
    unittest.main()
