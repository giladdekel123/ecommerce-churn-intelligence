"""V3 live core: the service that is the only way to start a run. Fake launcher; no process, no Gemini."""
import ast
import re
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_support import NOW, OTHER, OWNER, Fixture, claims, fake_xlsx, run_id, token  # noqa: E402

from src import run_launcher  # noqa: E402

LIVE = Path(__file__).resolve().parents[1] / "live"


class StartAuthorizationTests(unittest.TestCase):
    def test_unauthorized_callers_cannot_start_and_leave_no_trace(self):
        fx = Fixture(self)
        bad = [None, {}, claims(sub=OTHER), claims(email_verified=False), claims(exp=NOW - 5),
               claims(aud="wrong"), claims(iss="https://evil.example"), claims(iat=NOW - 20 * 3600),
               claims(is_logged_in=False)]
        for c in bad:
            r = fx.service.start(c, "pass", token())
            self.assertEqual(r.status, "DENIED", c)
            self.assertIsNone(r.run_id)
        self.assertEqual(fx.launches, [])
        self.assertEqual(fx.ledger.active_rows(), [])
        self.assertEqual(fx.ledger.usage(OWNER)["starts_today"], 0)

    def test_denial_message_is_identical_for_every_reason(self):
        fx = Fixture(self)
        messages = {fx.service.start(c, "pass", token()).message
                    for c in (None, claims(sub=OTHER), claims(exp=NOW - 1), claims(email_verified=False))}
        self.assertEqual(len(messages), 1)                     # a visitor cannot probe which check failed

    def test_authorization_is_rechecked_on_every_start(self):
        fx = Fixture(self)
        self.assertEqual(fx.service.start(claims(), "pass", token()).status, "STARTED")
        fx.finish_active()
        fx.clock.advance(14 * 3600)                            # same cookie, now older than the session limit
        stale = claims(iat=NOW - 60, exp=NOW + 99 * 3600)
        self.assertEqual(fx.service.start(stale, "pass", token()).status, "DENIED")

    def test_removing_a_user_from_the_allow_list_takes_effect_at_once(self):
        fx = Fixture(self, allowed=(OWNER, OTHER))
        self.assertEqual(fx.service.start(claims(sub=OTHER), "pass", token()).status, "STARTED")
        fx.finish_active()
        fx2 = Fixture(self, allowed=(OWNER,))
        self.assertEqual(fx2.service.start(claims(sub=OTHER), "pass", token()).status, "DENIED")


class StartRequestValidationTests(unittest.TestCase):
    def test_only_registered_scenarios_can_be_requested(self):
        fx = Fixture(self)
        for bad in ("../pass", "pass.xlsx", "PASS", "", None, 5, ["pass"], "pass; rm -rf /", "C:\\x.xlsx"):
            self.assertEqual(fx.service.start(claims(), bad, token()).status, "INVALID_REQUEST", repr(bad))
        self.assertEqual(fx.launches, [])

    def test_malformed_tokens_are_refused_without_recording(self):
        fx = Fixture(self)
        for bad in (None, "", "abc", "x" * 200, "has spaces in the token", "../../token/12345678", 123):
            self.assertEqual(fx.service.start(claims(), "pass", bad).status, "INVALID_REQUEST", repr(bad))
        self.assertEqual(fx.ledger.usage(OWNER)["starts_today"], 0)

    def test_no_visitor_file_ever_reaches_the_launcher(self):
        fx = Fixture(self)
        fx.service.start(claims(), "pass", token())
        upload = fx.launches[0]
        self.assertEqual(Path(upload["path"]).name, upload["sha256"] + ".xlsx")             # content-addressed
        self.assertEqual(upload["sha256"], fx.registry.get("pass").sha256)                  # the pinned workbook


class StartExecutionControlTests(unittest.TestCase):
    def test_successful_start_records_a_started_run(self):
        fx = Fixture(self)
        r = fx.service.start(claims(), "fail", token())
        self.assertEqual(r.status, "STARTED")
        row = fx.ledger.by_run_id(r.run_id)
        self.assertEqual((row["state"], row["scenario"], row["sub"]), ("STARTED", "fail", OWNER))
        self.assertEqual(len(fx.launches), 1)

    def test_repeating_the_same_request_attaches_instead_of_starting_again(self):
        fx = Fixture(self)
        tok = token()
        first = fx.service.start(claims(), "pass", tok)
        again = fx.service.start(claims(), "pass", tok)
        self.assertEqual((first.status, again.status), ("STARTED", "REATTACHED"))
        self.assertEqual(again.run_id, first.run_id)
        self.assertEqual(len(fx.launches), 1)

    def test_a_refresh_after_the_run_finished_still_does_not_rerun(self):
        fx = Fixture(self)
        tok = token()
        first = fx.service.start(claims(), "pass", tok)
        fx.finish_active()
        self.assertEqual(fx.service.start(claims(), "pass", tok).status, "REATTACHED")
        self.assertEqual(len(fx.launches), 1)
        self.assertEqual(fx.service.start(claims(), "pass", tok).run_id, first.run_id)

    def test_simultaneous_double_click_starts_exactly_one_run(self):
        fx = Fixture(self)
        tok = token()
        barrier = threading.Barrier(12)
        out = []

        def click():
            barrier.wait()
            out.append(fx.service.start(claims(), "pass", tok).status)

        threads = [threading.Thread(target=click) for _ in range(12)]
        [t.start() for t in threads]
        [t.join(30) for t in threads]
        self.assertEqual(out.count("STARTED"), 1, out)
        self.assertEqual(out.count("REATTACHED"), 11, out)
        self.assertEqual(len(fx.launches), 1)

    def test_only_one_run_globally_even_with_distinct_requests_and_users(self):
        fx = Fixture(self, allowed=(OWNER, OTHER))
        barrier = threading.Barrier(10)
        out = []

        def click(i):
            barrier.wait()
            who = claims(sub=OWNER if i % 2 else OTHER)
            out.append(fx.service.start(who, "pass", token()).status)

        threads = [threading.Thread(target=click, args=(i,)) for i in range(10)]
        [t.start() for t in threads]
        [t.join(30) for t in threads]
        self.assertEqual(out.count("STARTED"), 1, out)
        self.assertEqual(out.count("BUSY"), 9, out)
        self.assertEqual(len(fx.launches), 1)

    def test_second_start_while_busy_is_refused_and_costs_nothing(self):
        fx = Fixture(self)
        fx.service.start(claims(), "pass", token())
        self.assertEqual(fx.service.start(claims(), "fail", token()).status, "BUSY")
        self.assertEqual(fx.ledger.usage(OWNER)["starts_today"], 1)
        self.assertEqual(len(fx.launches), 1)

    def test_daily_limit_is_two_and_resets_the_next_day(self):
        fx = Fixture(self)
        for _ in range(2):
            self.assertEqual(fx.service.start(claims(), "pass", token()).status, "STARTED")
            fx.finish_active()
        self.assertEqual(fx.service.start(claims(), "pass", token()).status, "DAILY_LIMIT")
        fx.clock.advance(24 * 3600)
        self.assertEqual(fx.service.start(claims(iat=fx.clock() - 60, exp=fx.clock() + 3000), "pass", token()).status,
                         "STARTED")

    def test_monthly_budget_stops_starts(self):
        fx = Fixture(self, daily=99, budget=1.0, reserve=0.5)
        for _ in range(2):
            self.assertEqual(fx.service.start(claims(), "pass", token()).status, "STARTED")
            fx.finish_active()
        r = fx.service.start(claims(), "pass", token())
        self.assertEqual(r.status, "BUDGET")
        self.assertEqual(len(fx.launches), 2)

    def test_kill_switch_blocks_new_starts_and_releasing_it_restores_service(self):
        fx = Fixture(self)
        fx.kill.engage()
        r = fx.service.start(claims(), "pass", token())
        self.assertEqual(r.status, "DISABLED")
        self.assertEqual((fx.launches, fx.ledger.usage(OWNER)["starts_today"]), ([], 0))
        fx.kill.release()
        self.assertEqual(fx.service.start(claims(), "pass", token()).status, "STARTED")

    def test_kill_switch_wins_even_for_a_repeated_request(self):
        fx = Fixture(self)
        tok = token()
        fx.service.start(claims(), "pass", tok)
        fx.kill.engage()
        self.assertEqual(fx.service.start(claims(), "pass", tok).status, "DISABLED")


class ScenarioIntegrityTests(unittest.TestCase):
    def test_a_tampered_workbook_cannot_be_started_and_costs_nothing(self):
        fx = Fixture(self)
        (fx.base / "scenarios" / "pass.xlsx").write_bytes(fake_xlsx("pass") + b"!")
        r = fx.service.start(claims(), "pass", token())
        self.assertEqual(r.status, "SCENARIO_UNAVAILABLE")
        self.assertEqual((fx.launches, fx.ledger.usage(OWNER)["starts_today"]), ([], 0))

    def test_a_missing_workbook_cannot_be_started(self):
        fx = Fixture(self)
        (fx.base / "scenarios" / "fail.xlsx").unlink()
        self.assertEqual(fx.service.start(claims(), "fail", token()).status, "SCENARIO_UNAVAILABLE")
        self.assertEqual(fx.service.start(claims(), "pass", token()).status, "STARTED")   # the other one still works


class LauncherFailureTests(unittest.TestCase):
    def test_launcher_lock_collision_is_busy_and_not_counted(self):
        def refuse(upload):
            raise run_launcher.AlreadyRunning("locked")
        fx = Fixture(self, launch_fn=refuse)
        self.assertEqual(fx.service.start(claims(), "pass", token()).status, "BUSY")
        self.assertEqual(fx.ledger.usage(OWNER), {**fx.ledger.usage(OWNER), "starts_today": 0,
                                                  "charged_micro_this_month": 0})
        self.assertEqual(fx.ledger.active_rows(), [])

    def test_other_launch_errors_release_the_money_but_count_the_attempt(self):
        def boom(upload):
            raise OSError("disk full at /secret/path/leaks.txt")
        fx = Fixture(self, launch_fn=boom)
        r = fx.service.start(claims(), "pass", token())
        self.assertEqual(r.status, "LAUNCH_FAILED")
        self.assertNotIn("secret", r.message + r.run_id.__str__())             # the visitor never sees internals
        self.assertNotIn("/secret", r.reason)                                   # nor does the log reason carry the text
        usage = fx.ledger.usage(OWNER)
        self.assertEqual((usage["starts_today"], usage["charged_micro_this_month"]), (1, 0))
        self.assertEqual(fx.ledger.active_rows(), [])

    def test_a_launcher_that_returns_garbage_is_a_failed_launch(self):
        for info in ({}, {"run_id": "../../etc"}, {"run_id": None}, {"run_id": 7}):
            fx = Fixture(self, launch_fn=lambda upload, info=info: info)
            self.assertEqual(fx.service.start(claims(), "pass", token()).status, "LAUNCH_FAILED", info)
            self.assertEqual(fx.ledger.active_rows(), [])


class StatusAccessTests(unittest.TestCase):
    def test_only_the_owner_of_a_run_started_here_can_read_it(self):
        fx = Fixture(self, allowed=(OWNER, OTHER))
        r = fx.service.start(claims(), "pass", token())
        view = fx.service.status(claims(), r.run_id)
        self.assertEqual(view["run_id"], r.run_id)
        with self.assertRaises(PermissionError):
            fx.service.status(claims(sub=OTHER), r.run_id)

    def test_historical_and_malformed_run_ids_are_never_served(self):
        fx = Fixture(self)
        for bad in ("20261111T094219Z_1fbdc4d8", "../runs", "..\\..\\x", "", None, 3, "20270101T000000Z_zzzzzzzz"):
            with self.assertRaises(PermissionError, msg=repr(bad)):
                fx.service.status(claims(), bad)

    def test_unauthorized_callers_get_nothing(self):
        fx = Fixture(self)
        r = fx.service.start(claims(), "pass", token())
        for c in (None, claims(sub=OTHER), claims(exp=NOW - 1)):
            with self.assertRaises(PermissionError):
                fx.service.status(c, r.run_id)
            with self.assertRaises(PermissionError):
                fx.service.usage(c)

    def test_usage_report(self):
        fx = Fixture(self)
        fx.service.start(claims(), "pass", token())
        usage = fx.service.usage(claims())
        self.assertEqual((usage["starts_today"], usage["daily_limit"], usage["monthly_budget_usd"]), (1, 2, 5.0))
        self.assertEqual(usage["month_charged_usd"], 0.5)
        self.assertFalse(usage["kill_switch_engaged"])


class StaticSafetyTests(unittest.TestCase):
    """The live core must not be able to call Gemini, open sockets, read secrets or take uploads."""

    def sources(self):
        return {p.name: p.read_text(encoding="utf-8") for p in LIVE.glob("*.py")}

    def test_forbidden_imports_and_calls(self):
        banned_imports = {"socket", "requests", "urllib", "http", "httpx", "google", "genai", "crewai", "litellm",
                          "streamlit", "sklearn", "joblib", "dotenv", "ftplib", "smtplib"}
        for name, text in self.sources().items():
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    mods = {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom):
                    mods = {(node.module or "").split(".")[0]}
                else:
                    continue
                self.assertFalse(mods & banned_imports, (name, mods & banned_imports))

    def test_no_secret_access_and_no_shell(self):
        for name, text in self.sources().items():
            for needle in ("getenv", "environ", "st.secrets", "shell=True", "os.system", "eval(", "exec(",
                           "file_uploader", "GEMINI_API_KEY", "GOOGLE_API_KEY", "api_key"):
                self.assertNotIn(needle, text, (name, needle))

    def test_only_the_control_module_may_start_a_subprocess(self):
        for name, text in self.sources().items():
            uses = re.search(r"^\s*(import subprocess|from subprocess)", text, re.M) is not None
            self.assertEqual(uses, name == "control.py", name)


if __name__ == "__main__":
    unittest.main()
