"""V3 live core: the persistent quota, budget and idempotency ledger (real SQLite, real threads)."""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_support import NOW, OTHER, OWNER, Clock, token  # noqa: E402

from live.ledger import Ledger, usd_to_micro  # noqa: E402

LIMITS = dict(daily_limit=2, monthly_budget_micro=usd_to_micro(5.0), reserve_micro=usd_to_micro(0.5))


def running(_row):
    return "RUNNING"


class LedgerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "ledger.sqlite"
        self.clock = Clock()
        self.ledger = Ledger(self.path, now=self.clock)

    _FRESH = object()

    def start(self, tok=_FRESH, sub=OWNER, classify=running, **over):
        tok = token() if tok is self._FRESH else tok   # None / "" must reach the ledger unchanged
        return self.ledger.try_start(tok, sub, "pass", classify_active=classify, **{**LIMITS, **over})

    def complete(self, tok, state="FINISHED", **kw):
        self.assertTrue(self.ledger.mark_started(tok, "20270115T080000Z_00000001", 4242))
        self.assertTrue(self.ledger.finish(tok, state, **kw))

    # ---- basics -----------------------------------------------------------------------------
    def test_reserves_and_records_a_start(self):
        tok = token()
        r = self.start(tok)
        self.assertEqual(r.outcome, "RESERVED")
        self.assertEqual((r.row["state"], r.row["reserved_micro"], r.row["charged_micro"]), ("RESERVED", 500_000, 500_000))
        self.assertEqual(r.row["day"], "2027-01-15")

    def test_invalid_tokens_are_refused_and_not_recorded(self):
        for bad in (None, "", "short", "has space in it 12345", "../../etc/passwd/xx", "x" * 65, 12345):
            self.assertEqual(self.start(bad).outcome, "INVALID_TOKEN", repr(bad))
        self.assertEqual(self.ledger.active_rows(), [])

    def test_data_survives_a_restart(self):
        tok = token()
        self.start(tok)
        reopened = Ledger(self.path, now=self.clock)
        self.assertEqual(reopened.get(tok)["state"], "RESERVED")
        self.assertEqual(self.start(token()).outcome, "BUSY")   # the active run is still known after a restart

    @unittest.skipIf(os.name == "nt", "POSIX file modes")
    def test_database_file_is_private(self):
        self.assertEqual(os.stat(self.path).st_mode & 0o077, 0)

    # ---- idempotency -------------------------------------------------------------------------
    def test_repeating_a_token_never_creates_a_second_run(self):
        tok = token()
        self.assertEqual(self.start(tok).outcome, "RESERVED")
        again = self.start(tok)
        self.assertEqual((again.outcome, again.row["request_token"]), ("EXISTING", tok))
        self.assertEqual(len(self.ledger.active_rows()), 1)

    def test_a_token_stays_final_after_a_failed_attempt(self):
        tok = token()
        self.start(tok)
        self.ledger.mark_launch_failed(tok)
        self.assertEqual(self.start(tok).outcome, "EXISTING")   # no retry loophole: a new click needs a new token
        self.assertEqual(self.start(token()).outcome, "RESERVED")

    def test_another_users_token_is_foreign_and_reveals_nothing(self):
        tok = token()
        self.start(tok, sub=OWNER)
        r = self.start(tok, sub=OTHER)
        self.assertEqual((r.outcome, r.row), ("FOREIGN_TOKEN", None))

    # ---- one active run ----------------------------------------------------------------------
    def test_only_one_active_run_globally_even_for_different_users(self):
        self.assertEqual(self.start(sub=OWNER).outcome, "RESERVED")
        self.assertEqual(self.start(sub=OTHER).outcome, "BUSY")
        self.assertEqual(self.start(sub=OWNER).outcome, "BUSY")

    def test_busy_refusals_are_not_stored_and_not_counted(self):
        self.assertEqual(self.start().outcome, "RESERVED")
        for _ in range(5):
            self.assertEqual(self.start().outcome, "BUSY")
        self.assertEqual(self.ledger.usage(OWNER)["starts_today"], 1)

    def test_slot_frees_when_the_run_ends(self):
        tok = token()
        self.start(tok)
        self.complete(tok)
        self.assertEqual(self.start().outcome, "RESERVED")

    # ---- daily limit -------------------------------------------------------------------------
    def test_third_start_of_the_day_is_refused_and_next_day_resets(self):
        for _ in range(2):
            tok = token()
            self.assertEqual(self.start(tok).outcome, "RESERVED")
            self.complete(tok)
        self.assertEqual(self.start().outcome, "DAILY_LIMIT")
        self.clock.advance(24 * 3600)
        self.assertEqual(self.start().outcome, "RESERVED")

    def test_limit_is_per_user_but_the_run_slot_is_global(self):
        for _ in range(2):
            tok = token()
            self.start(tok, sub=OWNER)
            self.complete(tok)
        self.assertEqual(self.start(sub=OWNER).outcome, "DAILY_LIMIT")
        self.assertEqual(self.start(sub=OTHER).outcome, "RESERVED")

    def test_failed_launches_count_but_busy_refusals_by_the_launcher_do_not(self):
        a, b = token(), token()
        self.start(a)
        self.ledger.mark_launch_failed(a)
        self.assertEqual(self.ledger.usage(OWNER)["starts_today"], 1)
        self.start(b)
        self.ledger.mark_refused_busy(b)
        self.assertEqual(self.ledger.usage(OWNER)["starts_today"], 1)

    def test_day_boundary_is_utc(self):
        self.clock.t = NOW + (24 * 3600 - 8 * 3600 - 1)      # 23:59:59 UTC the same day
        first = token()
        self.assertEqual(self.start(first, daily_limit=99).row["day"], "2027-01-15")
        self.complete(first)
        self.clock.advance(2)
        self.assertEqual(self.start(daily_limit=99).row["day"], "2027-01-16")

    # ---- monthly budget ----------------------------------------------------------------------
    def test_budget_stops_starts_when_the_next_reserve_would_exceed_it(self):
        over = dict(daily_limit=99, reserve_micro=usd_to_micro(2.0))
        for _ in range(2):
            tok = token()
            self.assertEqual(self.start(tok, **over).outcome, "RESERVED")
            self.complete(tok)
        self.assertEqual(self.start(**over).outcome, "BUDGET")             # 4.00 + 2.00 > 5.00
        self.assertEqual(self.start(daily_limit=99, reserve_micro=usd_to_micro(1.0)).outcome, "RESERVED")

    def test_exact_budget_is_allowed_and_one_micro_dollar_more_is_not(self):
        over = dict(daily_limit=99, monthly_budget_micro=usd_to_micro(1.0))
        self.assertEqual(self.start(reserve_micro=usd_to_micro(1.0), **over).outcome, "RESERVED")
        tok = self.ledger.active_rows()[0]["request_token"]
        self.complete(tok)
        self.assertEqual(self.start(reserve_micro=1, **over).outcome, "BUDGET")

    def test_released_reservations_return_the_money(self):
        over = dict(daily_limit=99, reserve_micro=usd_to_micro(2.5))
        a = token()
        self.assertEqual(self.start(a, **over).outcome, "RESERVED")
        self.ledger.mark_launch_failed(a)                                   # never ran: money released
        self.assertEqual(self.ledger.usage(OWNER)["charged_micro_this_month"], 0)
        b = token()
        self.assertEqual(self.start(b, **over).outcome, "RESERVED")
        self.complete(b)
        self.assertEqual(self.start(**over).outcome, "RESERVED")           # 2.5 + 2.5 <= 5.0

    def test_measured_cost_above_the_reserve_is_charged_instead(self):
        tok = token()
        self.start(tok)
        self.complete(tok, actual_micro=usd_to_micro(1.25))
        self.assertEqual(self.ledger.get(tok)["charged_micro"], 1_250_000)
        tok2 = token()
        self.start(tok2)
        self.complete(tok2, actual_micro=usd_to_micro(0.01))               # cheaper than reserved: keep the reserve
        self.assertEqual(self.ledger.get(tok2)["charged_micro"], 500_000)

    def test_budget_resets_with_the_calendar_month(self):
        over = dict(daily_limit=99, reserve_micro=usd_to_micro(5.0))
        tok = token()
        self.assertEqual(self.start(tok, **over).outcome, "RESERVED")
        self.complete(tok)
        self.assertEqual(self.start(**over).outcome, "BUDGET")
        self.clock.advance(20 * 24 * 3600)                                  # into February 2027
        self.assertEqual(self.start(**over).outcome, "RESERVED")

    # ---- crash recovery ----------------------------------------------------------------------
    def test_stale_reservation_frees_the_slot_but_keeps_the_cost(self):
        stuck = token()
        self.start(stuck)                                                   # the service died before recording the outcome
        self.clock.advance(121)
        self.assertEqual(self.start().outcome, "RESERVED")                  # the slot is not jammed forever
        row = self.ledger.get(stuck)
        # A run may have been launched, so the reservation is kept and the attempt counted.
        self.assertEqual((row["state"], row["charged_micro"]), ("INTERRUPTED", 500_000))

    def test_ended_or_dead_runs_are_settled_before_the_busy_check(self):
        for verdict in ("FINISHED", "INTERRUPTED"):
            tok = token()
            self.assertEqual(self.start(tok, daily_limit=99, classify=lambda r: verdict).outcome, "RESERVED")
            self.ledger.mark_started(tok, "20270115T080000Z_00000001", 1)
            self.assertEqual(self.start(daily_limit=99, classify=lambda r: verdict).outcome, "RESERVED")
            self.assertEqual(self.ledger.get(tok)["state"], verdict)
            for row in self.ledger.active_rows():
                self.ledger.mark_launch_failed(row["request_token"])

    def test_a_run_that_is_still_running_keeps_blocking(self):
        tok = token()
        self.start(tok)
        self.ledger.mark_started(tok, "20270115T080000Z_00000001", 1)
        self.assertEqual(self.start(classify=running).outcome, "BUSY")

    # ---- transitions are guarded ---------------------------------------------------------------
    def test_transitions_only_apply_from_the_expected_state(self):
        tok = token()
        self.start(tok)
        self.assertFalse(self.ledger.finish(tok, "FINISHED"))              # not started yet
        self.assertTrue(self.ledger.mark_started(tok, "20270115T080000Z_00000001", 7))
        self.assertFalse(self.ledger.mark_started(tok, "20270115T080000Z_00000002", 8))
        self.assertFalse(self.ledger.mark_launch_failed(tok))
        self.assertTrue(self.ledger.finish(tok, "KILLED", detail="kill switch"))
        self.assertFalse(self.ledger.finish(tok, "FINISHED"))              # an ended run stays ended
        self.assertEqual(self.ledger.get(tok)["state"], "KILLED")
        with self.assertRaises(ValueError):
            self.ledger.finish(tok, "STARTED")

    # ---- real concurrency ----------------------------------------------------------------------
    def run_threads(self, count, target):
        barrier = threading.Barrier(count)
        results = [None] * count

        def worker(i):
            barrier.wait()
            results[i] = target(i)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
        [t.start() for t in threads]
        [t.join(30) for t in threads]
        return results

    def test_concurrent_requests_with_different_tokens_yield_exactly_one_run(self):
        tokens = [token() for _ in range(16)]
        results = self.run_threads(16, lambda i: self.start(tokens[i], daily_limit=99).outcome)
        self.assertEqual(sorted(results).count("RESERVED"), 1, results)
        self.assertEqual(sorted(results).count("BUSY"), 15, results)
        self.assertEqual(len(self.ledger.active_rows()), 1)

    def test_concurrent_requests_with_the_same_token_yield_exactly_one_run(self):
        tok = token()
        results = self.run_threads(16, lambda i: self.start(tok, daily_limit=99).outcome)
        self.assertEqual(sorted(results).count("RESERVED"), 1, results)
        self.assertEqual(sorted(results).count("EXISTING"), 15, results)

    def test_concurrent_instances_on_one_file_share_the_same_gate(self):
        other = Ledger(self.path, now=self.clock)                           # a second process would look like this
        toks = [token(), token()]
        results = self.run_threads(2, lambda i: (self.ledger if i == 0 else other).try_start(
            toks[i], OWNER, "pass", classify_active=running, **LIMITS).outcome)
        self.assertEqual(sorted(results), ["BUSY", "RESERVED"])


if __name__ == "__main__":
    unittest.main()
