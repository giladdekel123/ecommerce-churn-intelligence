"""Independent enforcement of the execution timeout and the kill switch.

This must not depend on a browser session: Streamlit sessions come and go, so the supervisor is
a plain object with enforce() that a separate service (or a timer) calls every few seconds.
It also settles the ledger when a run ends, so quotas reflect reality.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path

from src import run_launcher

from live.control import KillSwitch, terminate_process_tree


class Supervisor:
    def __init__(self, ledger, kill_switch: KillSwitch, *, timeout_s: int, runs_root, now=time.time,
                 terminate=terminate_process_tree, run_state=run_launcher.run_state,
                 is_alive=run_launcher.pid_alive, current_run=run_launcher.current_run):
        self.ledger, self.kill_switch, self.timeout_s = ledger, kill_switch, timeout_s
        self.runs_root, self._now = Path(runs_root), now
        self._terminate, self._run_state, self._is_alive = terminate, run_state, is_alive
        self._current_run = current_run

    def classify(self, row: dict) -> str:
        """RUNNING, FINISHED or INTERRUPTED for a STARTED ledger row, from the run's own evidence."""
        state = self._run_state(row.get("run_id"), runs_root=self.runs_root).get("state")
        if state == "FINISHED":
            return "FINISHED"
        if state == "RUNNING":
            return "RUNNING"
        pid = row.get("pid")
        return "RUNNING" if isinstance(pid, int) and self._is_alive(pid) else "INTERRUPTED"

    def adopt_orphan_run(self) -> bool:
        """Attach a running pipeline that the ledger lost track of to its waiting reservation.

        If the service dies after the launcher started a run but before the ledger recorded it, the
        row stays RESERVED while a real process runs. The launcher's lock file still names that live
        run, so, when exactly one reservation is waiting and the lock names a live run that no ledger
        row owns and that began after the reservation, the two belong together: record the run so
        the timeout and kill switch apply to it. Otherwise nothing is adopted.
        """
        waiting = [r for r in self.ledger.active_rows() if r["state"] == "RESERVED"]
        if len(waiting) != 1:
            return False
        row = waiting[0]
        info = self._current_run(self.runs_root) or {}
        run_id, pid = info.get("run_id"), info.get("pid")
        if not info.get("alive") or not isinstance(run_id, str) or not isinstance(pid, int):
            return False
        if self.ledger.by_run_id(run_id) is not None:
            return False
        try:
            began = datetime.fromisoformat(str(info.get("started_at"))).timestamp()
        except ValueError:
            return False
        if began < row["requested_at"] - 1:   # the lock belongs to a run older than this reservation
            return False
        return self.ledger.mark_started(row["request_token"], run_id, pid)

    def enforce(self) -> list[dict]:
        """One pass. Returns what it did, e.g. [{'token': ..., 'action': 'TIMED_OUT', 'stopped': True}]."""
        actions = []
        now = self._now()
        self.adopt_orphan_run()
        self.ledger.expire_reservations()
        for row in self.ledger.active_rows():
            if row["state"] != "STARTED":
                continue
            verdict = self.classify(row)
            if verdict in ("FINISHED", "INTERRUPTED"):
                self.ledger.finish(row["request_token"], verdict)
                actions.append({"token": row["request_token"], "action": verdict, "stopped": True})
                continue
            reason = state = None
            if self.kill_switch.engaged():
                reason, state = "stopped by the kill switch", "KILLED"
            elif now - (row["started_at"] or now) > self.timeout_s:
                reason, state = f"exceeded the {self.timeout_s} s execution limit", "TIMED_OUT"
            if not state:
                continue
            stopped = bool(row.get("pid")) and self._terminate(row["pid"])
            if stopped:  # only settle the ledger once the process is really gone; otherwise retry next pass
                self.ledger.finish(row["request_token"], state, detail=reason)
            actions.append({"token": row["request_token"], "action": state, "stopped": stopped})
        return actions

    def run_forever(self, interval_s: float = 2.0, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        while not stop.is_set():
            self.enforce()
            stop.wait(interval_s)
