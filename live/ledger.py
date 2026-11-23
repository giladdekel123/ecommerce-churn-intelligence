"""Persistent quota, budget and idempotency ledger (SQLite, write-ahead log).

Every limit is checked and the start is recorded inside ONE `BEGIN IMMEDIATE` transaction, so two
simultaneous requests (two tabs, a double click, two users) cannot both pass a check that only one
should. Money is stored as integer micro-dollars (1 USD = 1,000,000) to avoid float drift.

This ledger is the dependable spend ceiling: it reserves a conservative cost per start BEFORE
anything runs. Google's billing data arrives later and is only a backstop.

States of a start:
  RESERVED -> STARTED -> FINISHED | TIMED_OUT | KILLED | INTERRUPTED
  RESERVED -> LAUNCH_FAILED | REFUSED_BUSY   (the launch is KNOWN to have failed: reservation released;
                                              REFUSED_BUSY is not counted)
  RESERVED -> INTERRUPTED                    (the service died or lost track before recording the outcome:
                                              a run MAY be executing, so the reservation is KEPT and counted.
                                              Releasing it would let a crash make a run free.)
Counting rules:
  - daily starts per user: every state except REFUSED_BUSY (failed attempts still count)
  - monthly charge: sum of charged_micro. It equals the reservation while a run is active or after
    it ended, is raised to the measured amount if that is higher, and is 0 once released.
Refusals made before a row exists (daily limit, budget, busy) are not stored, so they can be
retried; once a token has a row, repeating the token returns that row's outcome and never starts
a second run (idempotency; no retry loophole).
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
MICRO = 1_000_000
ACTIVE_STATES = ("RESERVED", "STARTED")
COUNTED_STATES = ("RESERVED", "STARTED", "FINISHED", "TIMED_OUT", "KILLED", "INTERRUPTED", "LAUNCH_FAILED")
END_STATES = ("FINISHED", "TIMED_OUT", "KILLED", "INTERRUPTED")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS starts (
    request_token TEXT PRIMARY KEY,
    sub           TEXT NOT NULL,
    scenario      TEXT NOT NULL,
    requested_at  INTEGER NOT NULL,
    day           TEXT NOT NULL,
    month         TEXT NOT NULL,
    state         TEXT NOT NULL,
    reserved_micro INTEGER NOT NULL,
    charged_micro  INTEGER NOT NULL,
    actual_micro   INTEGER,
    run_id        TEXT,
    pid           INTEGER,
    started_at    INTEGER,
    finished_at   INTEGER,
    detail        TEXT
);
CREATE INDEX IF NOT EXISTS starts_sub_day ON starts (sub, day);
CREATE INDEX IF NOT EXISTS starts_month ON starts (month);
CREATE INDEX IF NOT EXISTS starts_run ON starts (run_id);
"""


def usd_to_micro(usd: float) -> int:
    return int(round(float(usd) * MICRO))


@dataclass(frozen=True)
class Reservation:
    outcome: str   # RESERVED, EXISTING, DAILY_LIMIT, BUDGET, BUSY, FOREIGN_TOKEN, INVALID_TOKEN
    row: dict | None = None


def _stamp(now: float):
    moment = datetime.fromtimestamp(now, timezone.utc)
    return moment.strftime("%Y-%m-%d"), moment.strftime("%Y-%m")


class Ledger:
    def __init__(self, path, *, now=time.time):
        self.path = str(path)
        self._now = now
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
        finally:
            conn.close()
        try:
            os.chmod(self.path, 0o600)  # best effort; meaningful on Linux, harmless on Windows
        except OSError:
            pass

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    # ---- the atomic gate -------------------------------------------------------------------
    def try_start(self, token: str, sub: str, scenario: str, *, daily_limit: int, monthly_budget_micro: int,
                  reserve_micro: int, classify_active, stale_reserved_s: int = 120) -> Reservation:
        """Check every limit and, only if all pass, record the start as RESERVED.

        classify_active(row) -> "RUNNING" | "FINISHED" | "INTERRUPTED" settles active rows left
        over from a crash or a finished run before the busy check is made.
        """
        if not isinstance(token, str) or not TOKEN_RE.fullmatch(token):
            return Reservation("INVALID_TOKEN")
        now = int(self._now())
        day, month = _stamp(now)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute("SELECT * FROM starts WHERE request_token=?", (token,)).fetchone()
                if existing is not None:
                    conn.execute("COMMIT")
                    if existing["sub"] != sub:
                        return Reservation("FOREIGN_TOKEN")  # never reveal another user's request
                    return Reservation("EXISTING", dict(existing))
                self._settle_stale(conn, now, classify_active, stale_reserved_s)
                if conn.execute("SELECT 1 FROM starts WHERE state IN ('RESERVED','STARTED') LIMIT 1").fetchone():
                    conn.execute("COMMIT")
                    return Reservation("BUSY")
                placeholders = ",".join("?" * len(COUNTED_STATES))
                counted = conn.execute(
                    f"SELECT COUNT(*) FROM starts WHERE sub=? AND day=? AND state IN ({placeholders})",
                    (sub, day, *COUNTED_STATES)).fetchone()[0]
                if counted >= daily_limit:
                    conn.execute("COMMIT")
                    return Reservation("DAILY_LIMIT")
                charged = conn.execute("SELECT COALESCE(SUM(charged_micro), 0) FROM starts WHERE month=?",
                                       (month,)).fetchone()[0]
                if charged + reserve_micro > monthly_budget_micro:
                    conn.execute("COMMIT")
                    return Reservation("BUDGET")
                conn.execute(
                    "INSERT INTO starts (request_token, sub, scenario, requested_at, day, month, state,"
                    " reserved_micro, charged_micro) VALUES (?,?,?,?,?,?, 'RESERVED', ?, ?)",
                    (token, sub, scenario, now, day, month, reserve_micro, reserve_micro))
                row = conn.execute("SELECT * FROM starts WHERE request_token=?", (token,)).fetchone()
                conn.execute("COMMIT")
                return Reservation("RESERVED", dict(row))
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    @staticmethod
    def _expire_reservations(conn, now: int, stale_reserved_s: int) -> None:
        # Unknown outcome, so the cost stays charged (charged_micro is left as reserved).
        conn.execute("UPDATE starts SET state='INTERRUPTED', finished_at=?,"
                     " detail='reservation expired: launch outcome unknown, cost kept reserved'"
                     " WHERE state='RESERVED' AND requested_at < ?", (now, now - stale_reserved_s))

    def expire_reservations(self, stale_reserved_s: int = 120) -> None:
        """Settle reservations nobody completed (called by the supervisor, not only on a new start)."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._expire_reservations(conn, int(self._now()), stale_reserved_s)
            conn.execute("COMMIT")
        finally:
            conn.close()

    def _settle_stale(self, conn, now: int, classify_active, stale_reserved_s: int) -> None:
        self._expire_reservations(conn, now, stale_reserved_s)
        for row in conn.execute("SELECT * FROM starts WHERE state='STARTED'").fetchall():
            verdict = classify_active(dict(row))
            if verdict in ("FINISHED", "INTERRUPTED"):
                conn.execute("UPDATE starts SET state=?, finished_at=?, charged_micro=MAX(reserved_micro, ?)"
                             " WHERE request_token=? AND state='STARTED'",
                             (verdict, now, row["actual_micro"] or 0, row["request_token"]))

    # ---- transitions (each only from the expected state; returns whether it applied) ---------
    def _transition(self, sql: str, params: tuple) -> bool:
        conn = self._connect()
        try:
            return conn.execute(sql, params).rowcount == 1
        finally:
            conn.close()

    def mark_started(self, token: str, run_id: str, pid: int | None) -> bool:
        return self._transition("UPDATE starts SET state='STARTED', run_id=?, pid=?, started_at=?"
                                " WHERE request_token=? AND state='RESERVED'",
                                (run_id, pid, int(self._now()), token))

    def mark_refused_busy(self, token: str) -> bool:
        return self._transition("UPDATE starts SET state='REFUSED_BUSY', charged_micro=0, finished_at=?"
                                " WHERE request_token=? AND state='RESERVED'", (int(self._now()), token))

    def mark_launch_failed(self, token: str, detail: str = "launch failed") -> bool:
        return self._transition("UPDATE starts SET state='LAUNCH_FAILED', charged_micro=0, finished_at=?, detail=?"
                                " WHERE request_token=? AND state='RESERVED'",
                                (int(self._now()), detail[:200], token))

    def finish(self, token: str, state: str, *, actual_micro: int | None = None, detail: str | None = None) -> bool:
        if state not in END_STATES:
            raise ValueError(f"Not an end state: {state}")
        return self._transition(
            "UPDATE starts SET state=?, finished_at=?, actual_micro=?, detail=?,"
            " charged_micro=MAX(reserved_micro, COALESCE(?, 0)) WHERE request_token=? AND state='STARTED'",
            (state, int(self._now()), actual_micro, (detail or "")[:200] or None, actual_micro, token))

    # ---- reads --------------------------------------------------------------------------------
    def _one(self, sql: str, params: tuple) -> dict | None:
        conn = self._connect()
        try:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get(self, token: str) -> dict | None:
        return self._one("SELECT * FROM starts WHERE request_token=?", (token,))

    def by_run_id(self, run_id: str) -> dict | None:
        return self._one("SELECT * FROM starts WHERE run_id=?", (run_id,))

    def latest_for_sub(self, sub: str) -> dict | None:
        """The caller's most recent request that got a run (lets a refreshed browser re-attach)."""
        return self._one("SELECT * FROM starts WHERE sub=? AND run_id IS NOT NULL"
                         " ORDER BY requested_at DESC, rowid DESC LIMIT 1", (sub,))

    def active_rows(self) -> list[dict]:
        conn = self._connect()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM starts WHERE state IN ('RESERVED','STARTED') ORDER BY requested_at")]
        finally:
            conn.close()

    def usage(self, sub: str) -> dict:
        day, month = _stamp(int(self._now()))
        conn = self._connect()
        try:
            marks = ",".join("?" * len(COUNTED_STATES))
            starts_today = conn.execute(
                f"SELECT COUNT(*) FROM starts WHERE sub=? AND day=? AND state IN ({marks})",
                (sub, day, *COUNTED_STATES)).fetchone()[0]
            charged = conn.execute("SELECT COALESCE(SUM(charged_micro), 0) FROM starts WHERE month=?",
                                   (month,)).fetchone()[0]
            return {"day": day, "starts_today": starts_today, "month": month, "charged_micro_this_month": charged}
        finally:
            conn.close()
