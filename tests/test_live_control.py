"""V3 live core: kill switch, process termination and the independent supervisor."""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_support import Fixture, OWNER, claims, token  # noqa: E402

from live.control import KillSwitch, terminate_process_tree  # noqa: E402


class KillSwitchTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.switch = KillSwitch(Path(tmp.name) / "state" / "KILL")

    def test_engage_and_release(self):
        self.assertFalse(self.switch.engaged())
        self.switch.engage("maintenance")
        self.assertTrue(self.switch.engaged())
        self.switch.release()
        self.assertFalse(self.switch.engaged())
        self.switch.release()  # releasing twice is harmless

    def test_an_unreadable_flag_state_fails_closed(self):
        with patch.object(Path, "exists", side_effect=PermissionError("denied")):
            self.assertTrue(self.switch.engaged())

    def test_a_flag_created_by_hand_is_honoured(self):
        self.switch.path.parent.mkdir(parents=True)
        self.switch.path.touch()       # what `touch KILL` over SSH does
        self.assertTrue(self.switch.engaged())


class TerminateTests(unittest.TestCase):
    def spawn(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        return proc

    def test_a_running_process_is_stopped(self):
        proc = self.spawn()
        self.assertIsNone(proc.poll())
        self.assertTrue(terminate_process_tree(proc.pid, grace_s=3, wait_s=10))
        self.assertIsNotNone(proc.wait(10))

    def test_an_already_finished_process_counts_as_stopped(self):
        proc = self.spawn()
        proc.kill()
        proc.wait(10)
        self.assertTrue(terminate_process_tree(proc.pid))

    def test_refuses_nonsense_and_its_own_process(self):
        for bad in (0, -1, None, "123", os.getpid()):
            self.assertFalse(terminate_process_tree(bad), repr(bad))


class SupervisorTests(unittest.TestCase):
    def started(self, fx, **kw):
        result = fx.service.start(claims(), "pass", token())
        self.assertEqual(result.status, "STARTED")
        return fx.ledger.by_run_id(result.run_id)

    def test_timeout_stops_the_run_and_frees_the_slot(self):
        fx = Fixture(self, timeout=300)
        row = self.started(fx)
        fx.clock.advance(299)
        self.assertEqual(fx.supervisor.enforce(), [])
        fx.clock.advance(2)
        actions = fx.supervisor.enforce()
        self.assertEqual([(a["action"], a["stopped"]) for a in actions], [("TIMED_OUT", True)])
        self.assertEqual(fx.terminated, [row["pid"]])
        done = fx.ledger.get(row["request_token"])
        self.assertEqual(done["state"], "TIMED_OUT")
        self.assertIn("300 s", done["detail"])
        self.assertEqual(fx.service.start(claims(), "pass", token()).status, "STARTED")   # slot is free again

    def test_kill_switch_stops_a_running_job_immediately(self):
        fx = Fixture(self)
        row = self.started(fx)
        fx.kill.engage("stop now")
        self.assertEqual([a["action"] for a in fx.supervisor.enforce()], ["KILLED"])
        self.assertEqual(fx.ledger.get(row["request_token"])["state"], "KILLED")
        self.assertEqual(fx.service.start(claims(), "pass", token()).status, "DISABLED")

    def test_a_process_that_will_not_die_is_not_declared_dead(self):
        fx = Fixture(self)
        row = self.started(fx)
        fx.terminate_succeeds = False
        fx.kill.engage()
        self.assertEqual([(a["action"], a["stopped"]) for a in fx.supervisor.enforce()], [("KILLED", False)])
        self.assertEqual(fx.ledger.get(row["request_token"])["state"], "STARTED")        # still occupies the slot
        fx.terminate_succeeds = True
        fx.supervisor.enforce()                                                           # retried on the next pass
        self.assertEqual(fx.ledger.get(row["request_token"])["state"], "KILLED")

    def test_finished_and_dead_runs_are_settled_without_killing_anything(self):
        fx = Fixture(self)
        row = self.started(fx)
        fx.states[row["run_id"]] = "FINISHED"
        self.assertEqual([a["action"] for a in fx.supervisor.enforce()], ["FINISHED"])
        self.assertEqual(fx.terminated, [])
        row2 = self.started(fx)
        fx.states[row2["run_id"]] = "UNKNOWN"
        fx.alive.discard(row2["pid"])
        self.assertEqual([a["action"] for a in fx.supervisor.enforce()], ["INTERRUPTED"])
        self.assertEqual(fx.terminated, [])

    def test_a_healthy_run_within_its_limit_is_left_alone(self):
        fx = Fixture(self)
        self.started(fx)
        fx.clock.advance(100)
        self.assertEqual(fx.supervisor.enforce(), [])
        self.assertEqual(fx.terminated, [])

    def test_enforcement_does_not_need_a_browser_session(self):
        # The supervisor holds no user identity: it acts from the ledger alone.
        fx = Fixture(self, timeout=10)
        self.started(fx)
        fx.clock.advance(11)
        self.assertEqual([a["action"] for a in fx.supervisor.enforce()], ["TIMED_OUT"])


class RealProcessEnforcementTests(unittest.TestCase):
    def test_timeout_really_kills_a_real_process(self):
        fx = Fixture(self, timeout=5)
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        rid = "20270115T080000Z_0000abcd"
        tok = token()
        fx.ledger.try_start(tok, OWNER, "pass", daily_limit=2, monthly_budget_micro=5_000_000,
                            reserve_micro=500_000, classify_active=lambda r: "RUNNING")
        fx.ledger.mark_started(tok, rid, proc.pid)
        from live.supervisor import Supervisor
        from src import run_launcher
        sup = Supervisor(fx.ledger, fx.kill, timeout_s=5, runs_root=fx.base / "runs", now=fx.clock,
                         run_state=lambda r, runs_root=None: {"state": "UNKNOWN"}, is_alive=run_launcher.pid_alive)
        fx.clock.advance(6)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and fx.ledger.get(tok)["state"] == "STARTED":
            sup.enforce()
        self.assertEqual(fx.ledger.get(tok)["state"], "TIMED_OUT")
        self.assertIsNotNone(proc.wait(10), "the real process must be gone")


if __name__ == "__main__":
    unittest.main()
