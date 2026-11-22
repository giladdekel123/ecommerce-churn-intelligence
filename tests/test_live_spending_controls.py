"""V3 live core: atomicity of reservations and the guarantee that interrupted runs cannot evade quotas.

These tests state what the ledger DOES guarantee. They also pin down what it does not (see
docs/v3_phase1/17_V3_SPENDING_CONTROLS_REVIEW.md): it limits reservations, not Google's actual bill.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_support import NOW, OWNER, Clock, Fixture, claims, token  # noqa: E402

from live.ledger import Ledger, usd_to_micro  # noqa: E402
from live.supervisor import Supervisor  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RID = "20270115T080000Z_00000001"


def running(_row):
    return "RUNNING"


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


class InterruptedRunsCannotEvadeQuotasTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "ledger.sqlite"
        self.clock = Clock()
        self.ledger = Ledger(self.path, now=self.clock)

    def start(self, reserve=2.0, budget=5.0, daily=99):
        return self.ledger.try_start(token(), OWNER, "pass", daily_limit=daily,
                                     monthly_budget_micro=usd_to_micro(budget),
                                     reserve_micro=usd_to_micro(reserve), classify_active=running)

    def test_crashing_before_the_outcome_is_recorded_does_not_make_runs_free(self):
        outcomes = []
        for _ in range(4):
            outcomes.append(self.start().outcome)       # reserve, then the service "crashes" (nothing recorded)
            self.clock.advance(121)                      # the reservation goes stale
        self.assertEqual(outcomes, ["RESERVED", "RESERVED", "BUDGET", "BUDGET"])   # 2 x $2.00 held; a third would pass $5
        self.assertEqual(self.ledger.usage(OWNER)["charged_micro_this_month"], 4_000_000)

    def test_crash_loops_also_exhaust_the_daily_limit(self):
        results = []
        for _ in range(3):
            results.append(self.start(reserve=0.1, daily=2).outcome)
            self.clock.advance(121)
        self.assertEqual(results, ["RESERVED", "RESERVED", "DAILY_LIMIT"])

    def test_a_dead_run_keeps_its_charge_and_its_daily_count(self):
        tok = token()
        self.ledger.try_start(tok, OWNER, "pass", daily_limit=2, monthly_budget_micro=5_000_000,
                              reserve_micro=500_000, classify_active=running)
        self.ledger.mark_started(tok, RID, 4242)
        self.ledger.try_start(token(), OWNER, "pass", daily_limit=2, monthly_budget_micro=5_000_000,
                              reserve_micro=500_000, classify_active=lambda row: "INTERRUPTED")
        row = self.ledger.get(tok)
        self.assertEqual((row["state"], row["charged_micro"]), ("INTERRUPTED", 500_000))
        self.assertEqual(self.ledger.usage(OWNER)["starts_today"], 2)

    def test_the_ledger_survives_a_restart_and_keeps_enforcing(self):
        fx_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(fx_tmp.cleanup)
        ledger = Ledger(Path(fx_tmp.name) / "l.sqlite", now=self.clock)
        tok = token()
        ledger.try_start(tok, OWNER, "pass", daily_limit=2, monthly_budget_micro=5_000_000,
                         reserve_micro=500_000, classify_active=running)
        ledger.mark_started(tok, RID, 777)
        terminated = []
        # A brand-new process: new Ledger and Supervisor objects, same files.
        reopened = Ledger(Path(fx_tmp.name) / "l.sqlite", now=self.clock)
        from live.control import KillSwitch
        sup = Supervisor(reopened, KillSwitch(Path(fx_tmp.name) / "KILL"), timeout_s=60, runs_root=fx_tmp.name,
                         now=self.clock, terminate=lambda pid: terminated.append(pid) or True,
                         run_state=lambda r, runs_root=None: {"state": "UNKNOWN"}, is_alive=lambda pid: True,
                         current_run=lambda root: None)
        self.clock.advance(61)
        self.assertEqual([a["action"] for a in sup.enforce()], ["TIMED_OUT"])
        self.assertEqual(terminated, [777])
        self.assertEqual(reopened.get(tok)["charged_micro"], 500_000)

    def test_timed_out_and_killed_runs_still_count_their_full_reservation(self):
        fx = Fixture(self, timeout=10, daily=99, budget=1.0, reserve=0.5)
        for _ in range(2):
            self.assertEqual(fx.service.start(claims(), "pass", token()).status, "STARTED")
            fx.clock.advance(11)
            self.assertEqual([a["action"] for a in fx.supervisor.enforce()], ["TIMED_OUT"])
        self.assertEqual(fx.service.start(claims(), "pass", token()).status, "BUDGET")


class OrphanAdoptionTests(unittest.TestCase):
    """The service died after the launcher started a run but before the ledger recorded it."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.clock = Clock()
        self.ledger = Ledger(self.base / "l.sqlite", now=self.clock)
        from live.control import KillSwitch
        self.kill = KillSwitch(self.base / "KILL")
        self.terminated = []
        self.lock = None   # what run_launcher.current_run would report

    def supervisor(self):
        return Supervisor(self.ledger, self.kill, timeout_s=60, runs_root=self.base, now=self.clock,
                          terminate=lambda pid: self.terminated.append(pid) or True,
                          run_state=lambda r, runs_root=None: {"state": "UNKNOWN"}, is_alive=lambda pid: True,
                          current_run=lambda root: self.lock)

    def reserve(self):
        tok = token()
        self.ledger.try_start(tok, OWNER, "pass", daily_limit=2, monthly_budget_micro=5_000_000,
                              reserve_micro=500_000, classify_active=running)
        return tok

    def test_a_live_unrecorded_run_is_adopted_and_then_obeys_the_timeout(self):
        tok = self.reserve()
        self.lock = {"run_id": RID, "pid": 4321, "alive": True, "started_at": iso(self.clock() + 1)}
        sup = self.supervisor()
        self.assertEqual(sup.enforce(), [])                                    # adopted, still within its limit
        row = self.ledger.get(tok)
        self.assertEqual((row["state"], row["run_id"], row["pid"]), ("STARTED", RID, 4321))
        self.clock.advance(61)
        self.assertEqual([(a["action"], a["stopped"]) for a in sup.enforce()], [("TIMED_OUT", True)])
        self.assertEqual(self.terminated, [4321])

    def test_nothing_is_adopted_when_the_evidence_does_not_match(self):
        tok = self.reserve()
        sup = self.supervisor()
        cases = {
            "no lock": None,
            "lock not alive": {"run_id": RID, "pid": 1, "alive": False, "started_at": iso(self.clock())},
            "no pid yet": {"run_id": RID, "pid": None, "alive": True, "started_at": iso(self.clock())},
            "bad run id": {"run_id": 5, "pid": 1, "alive": True, "started_at": iso(self.clock())},
            "started before the reservation": {"run_id": RID, "pid": 1, "alive": True, "started_at": iso(self.clock() - 500)},
            "garbage time": {"run_id": RID, "pid": 1, "alive": True, "started_at": "not a time"},
        }
        for name, lock in cases.items():
            self.lock = lock
            self.assertFalse(sup.adopt_orphan_run(), name)
            self.assertEqual(self.ledger.get(tok)["state"], "RESERVED", name)

    def test_a_run_already_owned_by_a_row_is_never_adopted_again(self):
        owned = self.reserve()
        self.ledger.mark_started(owned, RID, 4321)
        self.ledger.finish(owned, "FINISHED")
        second = self.reserve()
        self.lock = {"run_id": RID, "pid": 4321, "alive": True, "started_at": iso(self.clock() + 1)}
        self.assertFalse(self.supervisor().adopt_orphan_run())
        self.assertEqual(self.ledger.get(second)["state"], "RESERVED")

    def test_an_unrecorded_run_is_not_guessed_when_two_reservations_compete(self):
        # Cannot happen through the service (one slot) but the supervisor must not guess if the data says so.
        conn = self.ledger._connect()
        for i in range(2):
            conn.execute("INSERT INTO starts (request_token, sub, scenario, requested_at, day, month, state,"
                         " reserved_micro, charged_micro) VALUES (?,?,?,?,?,?, 'RESERVED', 1, 1)",
                         (f"tokenwaiting{i:010d}", OWNER, "pass", int(self.clock()), "2027-01-15", "2027-01", ))
        conn.close()
        self.lock = {"run_id": RID, "pid": 4321, "alive": True, "started_at": iso(self.clock() + 1)}
        self.assertFalse(self.supervisor().adopt_orphan_run())

    def test_without_adoption_the_stale_reservation_still_keeps_the_money(self):
        tok = self.reserve()
        sup = self.supervisor()
        self.clock.advance(121)
        sup.enforce()                                                          # nothing to adopt; reservation expires
        row = self.ledger.get(tok)
        self.assertEqual((row["state"], row["charged_micro"]), ("INTERRUPTED", 500_000))


class ClockAssumptionTests(unittest.TestCase):
    """Documented assumption: counters follow the server's UTC clock, so it must stay NTP-synchronized."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.clock = Clock()
        self.ledger = Ledger(Path(tmp.name) / "l.sqlite", now=self.clock)

    def start(self):
        r = self.ledger.try_start(token(), OWNER, "pass", daily_limit=1, monthly_budget_micro=5_000_000,
                                  reserve_micro=100_000, classify_active=lambda row: "FINISHED")
        if r.outcome == "RESERVED":                      # let the run complete so it no longer holds the slot
            self.ledger.mark_started(r.row["request_token"], RID, 1)
            self.ledger.finish(r.row["request_token"], "FINISHED")
        return r.outcome

    def test_moving_the_clock_forward_resets_the_daily_count_but_not_the_monthly_budget(self):
        self.assertEqual(self.start(), "RESERVED")
        self.assertEqual(self.start(), "DAILY_LIMIT")
        self.clock.advance(24 * 3600)                    # a clock jump (or attack on the clock) opens a new day
        self.assertEqual(self.start(), "RESERVED")       # daily count reset
        self.assertEqual(self.ledger.usage(OWNER)["charged_micro_this_month"], 200_000)   # but spending still accumulates

    def test_moving_the_clock_backwards_does_not_reopen_a_used_day(self):
        self.assertEqual(self.start(), "RESERVED")
        self.clock.advance(-3600)
        self.assertEqual(self.start(), "DAILY_LIMIT")


WORKER = r'''
import json, sys, time, uuid
sys.path.insert(0, sys.argv[1])
from live.ledger import Ledger
ledger = Ledger(sys.argv[2])
reserved = 0
max_active = 0
deadline = time.time() + 40
while time.time() < deadline:
    tok = "w" + uuid.uuid4().hex[:20]
    r = ledger.try_start(tok, "100000000000000000001", "pass", daily_limit=10_000, monthly_budget_micro=2_000_000,
                         reserve_micro=250_000, classify_active=lambda row: "RUNNING")
    if r.outcome == "RESERVED":
        reserved += 1
        max_active = max(max_active, len(ledger.active_rows()))
        ledger.mark_started(tok, "20270115T080000Z_%08x" % reserved, 1)
        ledger.finish(tok, "FINISHED")
    elif r.outcome == "BUDGET":
        break
print(json.dumps({"reserved": reserved, "max_active": max_active}))
'''


class MultiProcessAtomicityTests(unittest.TestCase):
    def test_separate_processes_cannot_over_reserve_the_budget_or_overlap(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = Path(tmp.name) / "shared.sqlite"
        Ledger(db)
        procs = [subprocess.Popen([sys.executable, "-c", WORKER, str(ROOT), str(db)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(6)]
        outputs = []
        for proc in procs:
            out, err = proc.communicate(timeout=120)
            self.assertEqual(proc.returncode, 0, err)
            outputs.append(json.loads(out.strip().splitlines()[-1]))
        total = sum(o["reserved"] for o in outputs)
        self.assertEqual(total, 8)                                            # $2.00 / $0.25: exactly the budget, never more
        self.assertLessEqual(max(o["max_active"] for o in outputs), 1)        # the single run slot was never double-booked
        reopened = Ledger(db)
        self.assertEqual(reopened.usage("100000000000000000001")["charged_micro_this_month"], 2_000_000)
        self.assertEqual(len(reopened.active_rows()), 0)


if __name__ == "__main__":
    unittest.main()
