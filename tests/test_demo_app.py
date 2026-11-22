"""Public demo tests: read-only replay, no execution paths, no leaks."""
import ast
import hashlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "demo"))
DEMO = ROOT / "demo"
APP = str(DEMO / "demo_app.py")
LAB = DEMO / "policy_lab.py"
POLICY = ROOT / "contracts" / "production_policy_v2.json"
PASS_SHA256 = "bf3ad3db150632ac4abc6308ffd94d47947f79f670ac5c1939d9b80180341fad"  # genuine recorded evidence
FAIL_SHA256 = "6a48bdcde8fdad39085910d4b22b73e8f600f3ef97b9814345fc745ec079fc0d"
RESET = "Reset to valid default"
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


def status_table(at):
    """The 'Seven recorded statuses' table (the only one with a '#' column)."""
    return next(df.value for df in at.dataframe if "#" in df.value.columns)


def run_app():
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    return at


class SourceSafetyTests(unittest.TestCase):
    def test_no_execution_capable_imports(self):
        for path in (Path(APP), LAB):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported |= {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom):
                    imported.add((node.module or "").split(".")[0])
            self.assertFalse(imported & FORBIDDEN_IMPORTS, (path.name, imported & FORBIDDEN_IMPORTS))

    def test_no_secret_or_upload_or_launch_api(self):
        for path in (Path(APP), LAB):
            source = path.read_text(encoding="utf-8")
            for needle in ("st.secrets", "getenv", "environ", "file_uploader", "text_input", "chat_input",
                           "subprocess", "Popen", "run_launcher", "genai", "fit(", "joblib"):
                self.assertNotIn(needle, source, (path.name, needle))
        # The only button in the public demo is the lab's reset control.
        source = Path(APP).read_text(encoding="utf-8")
        self.assertEqual(source.count("st.button("), 1)
        self.assertIn(RESET, source)

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

    def test_recorded_evidence_is_unchanged(self):
        # Genuine recorded PASS/FAIL evidence is pinned (line endings normalized). Changing it needs a
        # deliberate edit here, never a side effect of a feature.
        for name, expected in (("pass.json", PASS_SHA256), ("fail.json", FAIL_SHA256)):
            raw = (EVIDENCE / name).read_bytes().replace(b"\r\n", b"\n")
            self.assertEqual(hashlib.sha256(raw).hexdigest(), expected, name)

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
        self.assertEqual([b.label for b in at.button], [RESET])
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
        table = status_table(at)
        self.assertEqual(list(table["Recorded status"][:2]), ["✅ COMPLETED"] * 2)
        self.assertTrue(all("not yet reached" in s for s in table["Recorded status"][2:]))

    def test_fail_scenario_statuses(self):
        at = run_app()
        at.radio(key="scenario").set_value("FAIL — corrupted workbook").run()
        table = status_table(at)
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


def real_gate_rule_ids(value) -> list:
    """Run one SatisfactionScore value through the REAL production gate, using the real policy's column spec."""
    from src.production_gate import validate_production_handoff
    spec = json.loads(POLICY.read_text(encoding="utf-8"))["columns"]["SatisfactionScore"]
    columns = {
        "CustomerID": {"type": "integer", "required": True, "nullable": False, "unique": True, "role": "identifier"},
        "Churn": {"type": "integer", "required": True, "nullable": False, "allowed_values": [0, 1], "role": "target"},
        "SatisfactionScore": dict(spec),
    }
    common = {"model_input_columns": ["SatisfactionScore"], "identifier_column": "CustomerID",
              "target_column": "Churn", "unit_assumptions": {"SatisfactionScore": "unknown_not_independently_verified"},
              "missing_value_strategy": "Preserve in Crew 1; fit imputation inside Crew 2 training folds only",
              "cleaning_transformation_version": "0.1.0-conservative", "source_sha256": "synthetic-source-test-hash"}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        csv = root / "clean_data.csv"
        csv.write_text(f"CustomerID,Churn,SatisfactionScore\n1,0,{value}\n", encoding="utf-8")
        policy = {"policy_version": "1.0.0", "status": "APPROVED", "formally_approved": True,
                  "approved_by": "Test-only reviewer", "approval_date": "2026-11-07",
                  "required_columns": list(columns), "columns": columns, **common}
        contract = {"contract_version": "1.0.0", "approved_policy_version": "1.0.0",
                    "contract_status": "READY_FOR_GATE_VALIDATION", "columns": columns, **common,
                    "cleaning_operations": {"rows_dropped": 0, "nulls_imputed": 0, "category_mappings_applied": {},
                                           "numeric_values_clipped": 0, "columns_renamed": {}},
                    "category_alias_mappings": {},
                    "prepared_data": {"rows": 1, "columns": 3, "missing_cells": 0,
                                      "sha256": hashlib.sha256(csv.read_bytes()).hexdigest()}}
        (root / "policy.json").write_text(json.dumps(policy), encoding="utf-8")
        (root / "contract.json").write_text(json.dumps(contract), encoding="utf-8")
        report = validate_production_handoff(root / "policy.json", root / "contract.json", csv)
    return [f["rule_id"] for f in report["failed_rules"] if f.get("column") == "SatisfactionScore"], report["status"]


class ValidationLabRuleTests(unittest.TestCase):
    def setUp(self):
        import policy_lab
        self.lab = policy_lab
        self.spec = policy_lab.load_policy()["spec"]

    def test_required_values(self):
        for value, status, rule in ((1, "PASS", None), (5, "PASS", None), (6, "FAIL", "CSV-007"),
                                    (9, "FAIL", "CSV-007"), (10, "FAIL", "CSV-007")):
            result = self.lab.evaluate(value, self.spec)
            self.assertEqual((result["status"], result["rule_id"]), (status, rule), value)
        self.assertEqual(self.lab.evaluate(6, self.spec)["limit"], 5)

    def test_limits_come_from_the_approved_policy_file(self):
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        self.assertEqual(self.spec, policy["columns"]["SatisfactionScore"])
        self.assertEqual((self.spec["min"], self.spec["max"]), (1, 5))
        # No hard-coded copy: changing the policy's bound changes the verdict.
        self.assertEqual(self.lab.evaluate(6, {**self.spec, "max": 7})["status"], "PASS")
        self.assertEqual(self.lab.evaluate(3, {**self.spec, "max": 2})["rule_id"], "CSV-007")
        self.assertEqual(self.lab.evaluate(1, {**self.spec, "min": 2})["rule_id"], "CSV-006")

    def test_agrees_with_the_real_production_gate(self):
        for value in range(0, 12):
            ids, status = real_gate_rule_ids(value)
            result = self.lab.evaluate(value, self.spec)
            self.assertEqual(result["status"], status, value)
            self.assertEqual([result["rule_id"]] if result["rule_id"] else [], ids, value)

    def test_policy_has_no_constraint_the_lab_ignores(self):
        # If the policy gains a new constraint on this column this fails, so the lab cannot silently diverge.
        self.assertEqual(self.lab.unsupported_constraints(self.spec), [])
        self.assertEqual(self.lab.evaluate(3, {**self.spec, "allowed_values": [1, 2]})["status"], "UNSUPPORTED")

    def test_default_is_valid(self):
        self.assertEqual(self.lab.evaluate(self.lab.default_value(self.spec), self.spec)["status"], "PASS")


class LabFailSafeTests(unittest.TestCase):
    """A missing or unusable policy disables the lab; it never falls back to a built-in rule or crashes."""

    def run_with_policy_file(self, content):
        import policy_lab
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            if content is not None:
                path.write_text(content, encoding="utf-8")
            from unittest import mock
            with mock.patch.object(policy_lab, "POLICY_FILE", path):
                return run_app()

    def assert_disabled(self, at):
        self.assertFalse(at.exception)
        self.assertEqual(len(at.slider), 1)  # only the replay slider; no lab slider
        self.assertFalse(any(e.key == "lab_score" for e in at.slider))
        self.assertTrue(any("lab is disabled" in str(e.value) or "refuses to give a verdict" in str(e.value)
                            for e in at.error))
        self.assertFalse(any("Single-rule PASS" in str(e.value) for e in at.success))

    def test_missing_policy_file(self):
        self.assert_disabled(self.run_with_policy_file(None))

    def test_malformed_policy_files(self):
        good = json.loads(POLICY.read_text(encoding="utf-8"))
        broken = [
            "{not json",
            json.dumps({"policy_version": "2.0.0", "columns": []}),
            json.dumps({"policy_version": "2.0.0", "columns": {}}),
            json.dumps({"policy_version": "2.0.0", "columns": {"SatisfactionScore": "oops"}}),
            json.dumps({"policy_version": "2.0.0", "columns": {"SatisfactionScore": {"type": "integer"}}}),
            json.dumps({"policy_version": "2.0.0", "columns": {"SatisfactionScore": {"min": 1, "max": "5"}}}),
            json.dumps({"policy_version": "2.0.0", "columns": {"SatisfactionScore": {"min": 5, "max": 1}}}),
            json.dumps({"columns": good["columns"]}),
        ]
        for content in broken:
            self.assert_disabled(self.run_with_policy_file(content))

    def test_unsupported_constraint_refuses_verdict(self):
        good = json.loads(POLICY.read_text(encoding="utf-8"))
        good["columns"]["SatisfactionScore"]["allowed_values"] = [1, 2, 3, 4, 5]
        at = self.run_with_policy_file(json.dumps(good))
        self.assert_disabled(at)
        self.assertIn("allowed_values", " ".join(str(e.value) for e in at.error))

    def test_policy_path_does_not_depend_on_working_directory(self):
        import os
        import policy_lab
        self.assertTrue(policy_lab.POLICY_FILE.is_absolute())
        self.assertEqual(policy_lab.POLICY_FILE, POLICY)
        previous = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                self.assertEqual(policy_lab.load_policy()["policy_version"], "2.0.0")
            finally:
                os.chdir(previous)


def lab_text(at):
    return " ".join(str(e.value) for kind in ("success", "error", "warning", "markdown", "caption")
                    for e in getattr(at, kind))


class ValidationLabUiTests(unittest.TestCase):
    def set_value(self, at, value):
        at.slider(key="lab_score").set_value(value).run()
        self.assertFalse(at.exception)

    def test_slider_range_and_default(self):
        at = run_app()
        slider = at.slider(key="lab_score")
        self.assertEqual((slider.min, slider.max), (1, 10))
        self.assertEqual(slider.value, 3)

    def test_pass_values(self):
        for value in (1, 5):
            at = run_app()
            self.set_value(at, value)
            self.assertTrue(any("Single-rule PASS" in str(e.value) for e in at.success), value)
            self.assertFalse(any("CSV-007" in str(e.value) and "FAIL ·" in str(e.value) for e in at.error), value)

    def test_fail_values_identify_csv_007(self):
        for value in (6, 9, 10):
            at = run_app()
            self.set_value(at, value)
            errors = " ".join(str(e.value) for e in at.error)
            self.assertIn("FAIL · CSV-007.**", errors, value)
            self.assertIn(f"{value} exceeds the approved maximum of 5", errors, value)
            self.assertFalse(any("Single-rule PASS" in str(e.value) for e in at.success), value)

    def test_single_rule_limitation_always_prominent(self):
        for value in (1, 5, 6, 10):
            at = run_app()
            self.set_value(at, value)
            warnings = " ".join(str(e.value) for e in at.warning)
            self.assertIn("One value, one rule", warnings)
            self.assertIn("does **not** mean a dataset has passed the independent gate", warnings)
        at = run_app()
        self.set_value(at, 4)
        self.assertIn("does not mean any dataset passed the gate", " ".join(str(e.value) for e in at.success))

    def test_reset_restores_valid_default(self):
        at = run_app()
        self.set_value(at, 9)
        self.assertTrue(any("CSV-007" in str(e.value) for e in at.error))
        at.button(key="lab_reset").click().run()
        self.assertEqual(at.slider(key="lab_score").value, 3)
        self.assertTrue(any("Single-rule PASS" in str(e.value) for e in at.success))

    def test_lab_does_not_alter_recorded_scenarios(self):
        at = run_app()
        self.set_value(at, 10)
        self.assertIn("PASS", [m.value for m in at.metric])  # scenario still PASS, gate verdict untouched
        at.radio(key="scenario").set_value("FAIL — corrupted workbook").run()
        self.assertIn("FAIL", [m.value for m in at.metric])
        self.assertIn("CSV-007", " ".join(str(df.value) for df in at.dataframe))

    def test_lab_mentions_recorded_fail_rule(self):
        at = run_app()
        self.assertIn("The recorded FAIL scenario failed on exactly this rule", lab_text(at))


class GuidedReviewTests(unittest.TestCase):
    STEPS = ["1 · Crew 1 agents", "2 · Independent gate", "3 · Crew 2 agents", "4 · Training implications"]

    def guided_tables(self, at):
        return [df.value for df in at.dataframe if "Recorded start" in df.value.columns]

    def test_labelled_guided_review_not_live(self):
        at = run_app()
        text = " ".join(str(e.value) for e in at.info)
        self.assertIn("Guided Review", text)
        self.assertIn("not a live or simulated execution", text)
        self.assertIn("no durations", text)

    def test_steps_present_in_both_scenarios(self):
        at = run_app()
        self.assertEqual(list(at.radio(key="guided_pass").options), self.STEPS)
        at.radio(key="scenario").set_value("FAIL — corrupted workbook").run()
        self.assertEqual(list(at.radio(key="guided_fail").options), self.STEPS)

    def test_pass_steps(self):
        at = run_app()
        crew1 = self.guided_tables(at)[0]
        self.assertEqual(list(crew1["Component"]), ["Data Quality Specialist", "Exploratory Data Analyst",
                                                    "Business Insights & Contract Specialist"])
        self.assertTrue(all("COMPLETED" in s for s in crew1["Recorded status"]))
        at.radio(key="guided_pass").set_value(self.STEPS[2]).run()
        crew2 = self.guided_tables(at)[0]
        self.assertTrue(all("COMPLETED" in s for s in crew2["Recorded status"]))
        at.radio(key="guided_pass").set_value(self.STEPS[3]).run()
        self.assertIn("Started", [m.value for m in at.metric])

    def test_fail_steps_show_blocked_crew2_and_no_training(self):
        at = run_app()
        at.radio(key="scenario").set_value("FAIL — corrupted workbook").run()
        at.radio(key="guided_fail").set_value(self.STEPS[1]).run()
        self.assertIn("CSV-007", " ".join(str(e.value) for e in at.error))
        at.radio(key="guided_fail").set_value(self.STEPS[2]).run()
        crew2 = self.guided_tables(at)[0]
        self.assertEqual(len(crew2), 3)
        self.assertTrue(all("BLOCKED" in s for s in crew2["Recorded status"]))
        self.assertTrue(all(start == "—" and end == "—" for start, end in
                            zip(crew2["Recorded start"], crew2["Recorded finish"])))  # nothing invented for blocked agents
        at.radio(key="guided_fail").set_value(self.STEPS[3]).run()
        self.assertIn("Not started", [m.value for m in at.metric])
        self.assertIn("No model was trained", " ".join(str(e.value) for e in at.error))

    def test_only_recorded_timestamps_are_shown(self):
        for name, key in (("pass", "pass"), ("fail", "fail")):
            d = json.loads((EVIDENCE / f"{name}.json").read_text(encoding="utf-8"))
            recorded = {c[f][11:19] + " UTC" for c in d["components"] for f in ("started_at", "finished_at") if c[f]}
            at = run_app()
            if name == "fail":
                at.radio(key="scenario").set_value("FAIL — corrupted workbook").run()
            for step in self.STEPS[:3]:
                at.radio(key=f"guided_{key}").set_value(step).run()
                for table in self.guided_tables(at):
                    for column in ("Recorded start", "Recorded finish"):
                        for value in table[column]:
                            self.assertTrue(value == "—" or value in recorded, (name, step, value))


if __name__ == "__main__":
    unittest.main()
