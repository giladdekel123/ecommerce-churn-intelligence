"""V3 Phase 2: the protected Streamlit page, driven headlessly against the real dispatcher (no socket, no Gemini)."""
import ast
import json
import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_support import OTHER, OTHER_KEY, OWNER, ClaimsClient, Fixture, claims, sign_token, transport_for  # noqa: E402

from streamlit.testing.v1 import AppTest  # noqa: E402
from live import web_client  # noqa: E402
from live.ipc import WorkerClient  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
APP = str(ROOT / "live" / "live_app.py")
START = "Start Live Analysis"
FORBIDDEN_IMPORT_ROOTS = {"crewai", "google", "genai", "sklearn", "joblib", "dotenv", "litellm", "openai", "pandas",
                          "numpy", "subprocess", "multiprocessing", "run_launcher"}
FORBIDDEN_LIVE_MODULES = {"live.service", "live.ledger", "live.supervisor", "live.worker", "live.control",
                          "live.projection", "live.config", "live.oidc", "live.auth", "live.scenarios"}


def texts(at):
    out = []
    for kind in ("markdown", "caption", "error", "warning", "success", "info", "subheader", "title", "text"):
        out += [str(getattr(e, "value", "")) for e in getattr(at, kind)]
    return " ".join(out)


def button(at, label):
    return next((b for b in at.button if b.label == label), None)


class AppCase(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture(self)
        self.client = WorkerClient(transport_for(self.fx.service))   # the real dispatcher and token verifier
        self.direct = ClaimsClient(transport_for(self.fx.service))
        self.current = claims()
        self.token_override = "UNSET"
        def id_token():
            if self.token_override != "UNSET":
                return self.token_override
            return sign_token(self.current) if self.current is not None else None
        p1 = patch.object(web_client, "current_id_token", id_token)
        p4 = patch.object(web_client, "is_logged_in", lambda: self.current is not None)
        p4.start()
        self.addCleanup(p4.stop)
        p2 = patch.object(web_client, "get_client", lambda: self.client)
        p3 = patch.dict(os.environ, {}, clear=False)
        for p in (p1, p2, p3):
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop("GEMINI_API_KEY", None)
        os.environ.pop("GOOGLE_API_KEY", None)

    def open(self):
        at = AppTest.from_file(APP, default_timeout=30)
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        return at

    def start_run(self, at, scenario="pass"):
        at.radio[0].set_value(scenario)
        at.checkbox[0].set_value(True)
        at.run()
        button(at, START).click()
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        return at


class SignInTests(AppCase):
    def test_a_visitor_who_is_not_signed_in_sees_only_a_sign_in_button(self):
        self.current = None
        at = self.open()
        self.assertIsNotNone(button(at, "Sign in with Google"))
        self.assertIsNone(button(at, START))
        self.assertEqual(len(at.radio), 0)
        self.assertEqual(self.fx.launches, [])

    def test_a_signed_in_but_unauthorized_visitor_cannot_start(self):
        self.current = claims(OTHER)
        at = self.open()
        self.assertIn("not authorized", texts(at).lower())
        self.assertIsNone(button(at, START))
        self.assertEqual(self.fx.launches, [])

    def test_a_forged_or_expired_token_is_refused_with_a_sign_in_again_message(self):
        for override in (sign_token(claims(), key=OTHER_KEY), sign_token(claims(exp=1)), "not.a.token"):
            self.token_override = override
            at = self.open()
            self.assertIn("sign in again", texts(at).lower())
            self.assertIsNone(button(at, START))
        self.assertEqual(self.fx.launches, [])

    def test_a_signed_in_visitor_without_an_exposed_token_cannot_continue(self):
        self.token_override = None
        at = self.open()
        self.assertIn("expose_tokens", texts(at))
        self.assertIsNone(button(at, START))
        self.assertEqual(self.fx.launches, [])

    def test_the_page_never_keeps_the_token_in_session_state_or_on_screen(self):
        at = self.open()
        token_value = sign_token(self.current)
        shown = texts(at) + repr([at.session_state[k] for k in ("request_token", "run_id") if k in at.session_state])
        self.assertIsNone(re.search(r"session_state.*(id_token|tokens)", (ROOT / "live" / "live_app.py").read_text(encoding="utf-8")))
        self.assertNotIn(token_value.split(".")[2], shown)
        self.assertNotIn(token_value.split(".")[1], shown)

    def test_the_page_clearly_says_it_is_live_not_a_replay(self):
        at = self.open()
        self.assertIn("LIVE EXECUTION", texts(at))
        self.assertIn("not the historical replay", texts(at))

    def test_a_visible_gemini_key_stops_the_page(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "x" * 30}):
            at = self.open()
        self.assertIn("misconfigured", texts(at))
        self.assertIsNone(button(at, START))
        self.assertEqual(self.fx.launches, [])


class StartFlowTests(AppCase):
    def test_only_the_two_predefined_scenarios_are_offered_and_nothing_can_be_uploaded(self):
        at = self.open()
        self.assertEqual(len(at.radio[0].options), 2)
        self.assertEqual(len(at.get("file_uploader")), 0)
        self.assertEqual(len(at.text_input), 0)

    def test_start_needs_the_explicit_confirmation(self):
        at = self.open()
        self.assertTrue(button(at, START).disabled)
        at.checkbox[0].set_value(True)
        at.run()
        self.assertFalse(button(at, START).disabled)
        self.assertEqual(self.fx.launches, [])

    def test_starting_launches_one_run_and_shows_progress(self):
        at = self.start_run(self.open())
        self.assertEqual(len(self.fx.launches), 1)
        self.assertIn("Live run", texts(at))
        self.assertTrue(button(at, START).disabled)           # cannot start another while one is active
        self.assertIn("in progress", texts(at))

    def test_a_refresh_reattaches_and_does_not_start_again(self):
        self.start_run(self.open())
        fresh = self.open()                                    # a brand-new browser session (refresh / second tab)
        self.assertIn("Live run", texts(fresh))
        self.assertTrue(button(fresh, START).disabled)
        self.assertEqual(len(self.fx.launches), 1)

    def test_a_double_click_with_the_same_token_cannot_launch_twice(self):
        at = self.open()
        at.radio[0].set_value("pass")
        at.checkbox[0].set_value(True)
        at.run()
        token = at.session_state["request_token"]
        self.direct.start(self.current, "pass", token)         # a second tab already submitted this exact request
        button(at, START).click()
        at.run()
        self.assertEqual(len(self.fx.launches), 1)

    def test_refusals_are_shown_plainly(self):
        self.fx.kill.engage("test")
        at = self.open()
        self.assertIn("switched off", texts(at))
        at.checkbox[0].set_value(True)
        at.run()
        button(at, START).click()
        at.run()
        self.assertIn("switched off", texts(at))
        self.assertEqual(self.fx.launches, [])

    def test_the_worker_being_down_is_reported_not_crashed(self):
        self.client = web_client.WorkerClient(lambda message: {"ok": False, "error": "UNAVAILABLE"})
        at = self.open()
        self.assertIn("not available", texts(at))
        self.assertIsNone(button(at, START))


class ProgressViewTests(AppCase):
    """PASS permits Crew 2, FAIL blocks it, as rendered from the worker's projection."""

    def view(self, **over):
        base = {"run_id": "20270115T080000Z_00000001", "run_state": "FINISHED", "limit_state": "FINISHED", "overall": "x",
                "stages": [], "components": [], "gate": None, "outcome": {}, "reports": [], "metrics": None}
        base.update(over)
        return base

    def render(self, view):
        AT = AppTest
        script = ("import sys\nsys.path.insert(0, r'%s')\nimport streamlit as st\nimport json\n"
                  "from live import live_app\nlive_app.render_view(json.loads(%r))\n" % (ROOT, json.dumps(view)))
        at = AT.from_string(script, default_timeout=30)
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        return at

    def test_pass_view(self):
        comps = [{"name": f"Agent {i}", "crew": "Crew 2", "kind": "agent", "state": "COMPLETED", "detail": None} for i in range(3)]
        at = self.render(self.view(components=comps, gate={"status": "PASS", "policy_version": "v2", "findings": []},
                                   outcome={"crew2_reached": True, "model_training_started": True, "model_deployed": False},
                                   reports=[{"component": "Agent 0", "title": "Review", "text": "<script>alert(1)</script> body"}]))
        self.assertTrue(any("PASS" in e.value for e in at.success))
        self.assertIn("Crew 2 may train", texts(at))
        self.assertEqual(len(at.expander), 1)
        self.assertEqual(len(at.get("html")), 0)                # report text is shown as plain text, never as HTML

    def test_fail_view_shows_the_finding_and_blocked_agents(self):
        comps = [{"name": "Machine Learning Engineer", "crew": "Crew 2", "kind": "agent", "state": "BLOCKED",
                  "detail": "Not started: no independent gate PASS"}]
        finding = {"rule_id": "CSV-007", "column": "SatisfactionScore", "message": "above maximum", "observed": "9",
                   "csv_lines": "5", "approved_label": "maximum", "approved_value": 5}
        at = self.render(self.view(components=comps, gate={"status": "FAIL", "policy_version": "v2", "findings": [finding]},
                                   outcome={"crew2_reached": False, "model_training_started": False, "model_deployed": False}))
        self.assertTrue(any("FAIL" in e.value for e in at.error))
        self.assertIn("CSV-007", texts(at))
        self.assertIn("BLOCKED", texts(at))
        self.assertIn("no model was trained", texts(at))


class StaticSafetyTests(unittest.TestCase):
    def imports(self, path):
        names = set()
        for node in ast.walk(ast.parse(Path(path).read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                names.add(node.module or "")
                names |= {f"{node.module}.{a.name}" for a in node.names}
        return names

    def test_the_web_side_cannot_import_anything_that_calls_gemini_or_reads_state(self):
        for name in ("live_app.py", "web_client.py", "ipc.py"):
            imported = self.imports(ROOT / "live" / name)
            roots = {n.split(".")[0] for n in imported}
            self.assertEqual(roots & FORBIDDEN_IMPORT_ROOTS, set(), name)
            self.assertEqual(imported & FORBIDDEN_LIVE_MODULES, set(), name)
            self.assertFalse({n for n in imported if n.startswith("src")}, name)

    def test_the_web_side_never_reads_or_names_the_key(self):
        for name in ("live_app.py", "web_client.py"):
            source = (ROOT / "live" / name).read_text(encoding="utf-8")
            self.assertNotIn("subprocess", source)
            self.assertNotIn("dotenv", source)
            self.assertNotIn("os.environ[", source)
            self.assertIsNone(re.search(r"\.env(?!iron)", source), name)
        # the only reference is the guard that insists the key is absent
        self.assertEqual(sorted(web_client.FORBIDDEN_ENV), ["GEMINI_API_KEY", "GOOGLE_API_KEY"])
        self.assertEqual(web_client.forbidden_secrets_present({"PATH": "x"}), [])
        self.assertEqual(web_client.forbidden_secrets_present({"GEMINI_API_KEY": "k"}), ["GEMINI_API_KEY"])

    def test_importing_the_web_modules_does_not_load_crewai_or_a_gemini_client(self):
        import subprocess
        code = ("import sys; sys.path.insert(0, %r); import streamlit, live.ipc, live.web_client, live.catalog; "
                "bad=[m for m in sys.modules if m.split('.')[0] in ('crewai','litellm','sklearn','openai','dotenv') or "
                "m.startswith(('google.genai','google.generativeai','google.auth','google.api_core'))]; "
                "print(bad)" % str(ROOT))
        out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(out.stdout.strip(), "[]", out.stderr[-300:])

    def test_the_public_catalog_matches_the_pinned_registry(self):
        from live.catalog import PUBLIC_SCENARIOS
        from live.scenarios import DEFAULT_SCENARIOS
        self.assertEqual([tuple(s) for s in PUBLIC_SCENARIOS], [(s.id, s.title, s.description) for s in DEFAULT_SCENARIOS])

    def test_the_demo_and_original_app_are_not_imported_by_the_live_page(self):
        imported = self.imports(ROOT / "live" / "live_app.py")
        self.assertFalse({n for n in imported if n.split(".")[0] in ("demo", "app")})


if __name__ == "__main__":
    unittest.main()
