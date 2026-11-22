"""Public demo tests: read-only replay, no execution paths, no leaks."""
import ast
import hashlib
import json
import re
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo"
APP = str(DEMO / "demo_app.py")
EVIDENCE = DEMO / "evidence"
FORBIDDEN_IMPORTS = {"subprocess", "os", "sys", "crewai", "google", "genai", "sklearn", "joblib", "dotenv",
                     "src", "run_launcher", "multiprocessing", "socket", "requests", "urllib"}
LEAK_PATTERNS = [r"[A-Za-z]:[\\/]", r"/Users/", r"API[_-]?KEY", r"AIza", r"\.env", r"password", r"secret",
                 r"[\w.+-]+@[\w-]+\.[\w.]+"]


def all_text(at):
    parts = []
    for kind in ("markdown", "caption", "error", "warning", "success", "info", "subheader", "title", "metric"):
        parts += [str(getattr(e, "value", "")) for e in getattr(at, kind)]
    return " ".join(parts)


def run_app():
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    return at


class SourceSafetyTests(unittest.TestCase):
    def test_no_execution_capable_imports(self):
        tree = ast.parse(Path(APP).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        self.assertFalse(imported & FORBIDDEN_IMPORTS, imported & FORBIDDEN_IMPORTS)

    def test_no_secret_or_upload_or_launch_api(self):
        source = Path(APP).read_text(encoding="utf-8")
        for needle in ("st.secrets", "getenv", "environ", "file_uploader", "st.button", "text_input",
                       "subprocess", "Popen", "run_launcher", "genai", "fit(", "joblib"):
            self.assertNotIn(needle, source, needle)

    def test_demo_requirements_are_lightweight(self):
        text = (DEMO / "requirements.txt").read_text(encoding="utf-8").lower()
        for heavy in ("crewai", "google-genai", "scikit-learn", "matplotlib", "openpyxl", "dotenv"):
            self.assertNotIn(heavy, text)
        self.assertIn("streamlit==", text)


class EvidenceTests(unittest.TestCase):
    def test_allow_listed_files_only(self):
        self.assertEqual({p.name for p in EVIDENCE.iterdir()}, {"pass.json", "fail.json", "MANIFEST.json"})

    def test_manifest_matches(self):
        digests = json.loads((EVIDENCE / "MANIFEST.json").read_text(encoding="utf-8"))["sha256"]
        for name, digest in digests.items():
            self.assertEqual(hashlib.sha256((EVIDENCE / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest(), digest)

    def test_no_leaks(self):
        for path in EVIDENCE.iterdir():
            text = path.read_text(encoding="utf-8")
            for pattern in LEAK_PATTERNS:
                self.assertIsNone(re.search(pattern, text, re.IGNORECASE), f"{path.name}: {pattern}")

    def test_outcomes_are_consistent_and_genuine(self):
        p = json.loads((EVIDENCE / "pass.json").read_text(encoding="utf-8"))
        f = json.loads((EVIDENCE / "fail.json").read_text(encoding="utf-8"))
        self.assertEqual(p["gate"]["status"], "PASS")
        self.assertTrue(p["manifest"]["crew2_reached"])
        self.assertFalse(p["manifest"]["model_deployed"])
        self.assertIsNotNone(p["metrics"])
        self.assertEqual(f["gate"]["status"], "FAIL")
        self.assertEqual(f["manifest"]["failed_rule_ids"], ["CSV-007"])
        self.assertFalse(f["manifest"]["crew2_reached"])
        self.assertFalse(f["manifest"]["model_training_started"])
        self.assertIsNone(f["metrics"])  # a failed run must not inherit another run's metrics
        self.assertEqual(f["components"][3]["state"], "FAILED")
        self.assertEqual({c["state"] for c in f["components"][4:]}, {"BLOCKED"})
        self.assertEqual(len(p["components"]), 7)
        self.assertEqual(len(f["components"]), 7)

    def test_excerpts_are_short(self):
        for name in ("pass", "fail"):
            d = json.loads((EVIDENCE / f"{name}.json").read_text(encoding="utf-8"))
            for e in d["excerpts"]:
                self.assertLess(len(e["text"]), 500)


class AppBehaviourTests(unittest.TestCase):
    def test_renders_with_replay_label_and_no_live_claim(self):
        at = run_app()
        text = all_text(at).lower()
        self.assertIn("historical execution replay", text)
        self.assertIn("no agents are running", text)
        for claim in ("agents are running now", "live agents", "running live", "currently running"):
            self.assertNotIn(claim, text)

    def test_no_launch_or_upload_widgets(self):
        at = run_app()
        self.assertEqual(len(at.button), 0)
        self.assertEqual(len(at.text_input), 0)
        self.assertEqual(len(at.get("file_uploader")), 0)
        self.assertEqual(len(at.get("chat_input")), 0)

    def test_pass_then_fail_switch(self):
        at = run_app()
        self.assertIn("PASS", [m.value for m in at.metric])
        at.radio(key="scenario").set_value("FAIL — corrupted workbook").run()
        self.assertFalse(at.exception)
        values = [m.value for m in at.metric]
        self.assertIn("FAIL", values)
        self.assertIn("Not started", values)
        text = all_text(at)
        self.assertIn("CSV-007", " ".join(str(df.value) for df in at.dataframe))
        self.assertIn("stopped the run", text)
        self.assertIn("No model was trained", text)
        self.assertNotIn("0.981", " ".join(str(df.value) for df in at.dataframe))

    def test_replay_slider_hides_unreached_components(self):
        at = run_app()
        at.slider(key="step_pass").set_value(2).run()
        table = at.dataframe[0].value
        self.assertEqual(list(table["Recorded status"][:2]), ["✅ COMPLETED"] * 2)
        self.assertTrue(all("not yet reached" in s for s in table["Recorded status"][2:]))

    def test_fail_scenario_statuses(self):
        at = run_app()
        at.radio(key="scenario").set_value("FAIL — corrupted workbook").run()
        table = at.dataframe[0].value
        statuses = list(table["Recorded status"])
        self.assertEqual(statuses[3], "❌ FAILED")
        self.assertTrue(all("BLOCKED" in s for s in statuses[4:]))

    def test_agent_excerpt_selection(self):
        at = run_app()
        at.selectbox(key="agent_pass").set_value("Machine Learning Engineer").run()
        quotes = " ".join(str(m.value) for m in at.markdown)
        self.assertIn("3-fold stratified cross-validation", quotes)

    def test_attribution_and_noncommercial_notice(self):
        at = run_app()
        text = " ".join(str(m.value) for m in at.markdown)
        for needle in ("Ankit Verma", "CC BY-NC-SA 4.0", "non-commercial", "share-alike", "Transformations"):
            self.assertIn(needle, text)

    def test_metrics_are_labelled_retrospective(self):
        at = run_app()
        self.assertIn("not evidence of production readiness", all_text(at))


if __name__ == "__main__":
    unittest.main()
