"""Global kill switch and process termination for the live service."""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

from src import run_launcher


class KillSwitch:
    """A flag file. While it exists nothing may start and any running job is stopped.

    File-based on purpose: it can be set by hand over SSH (`touch`), by a script or by the app,
    needs no database, and survives restarts. If the flag's state cannot be read the switch is
    treated as ENGAGED (fail closed).
    """

    def __init__(self, path):
        self.path = Path(path)

    def engaged(self) -> bool:
        try:
            return self.path.exists()
        except OSError:
            return True

    def engage(self, reason: str = "") -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text((reason or "engaged")[:200] + "\n", encoding="utf-8")

    def release(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def _running(pid: int) -> bool:
    """Alive and not a zombie child of this process (a zombie still answers signal 0)."""
    if os.name != "nt":
        try:
            done, _ = os.waitpid(pid, os.WNOHANG)  # reaps it if it is our own finished child
            if done == pid:
                return False
        except ChildProcessError:
            pass  # not our child: fall through to the generic check
    return run_launcher.pid_alive(pid)


def terminate_process_tree(pid: int, *, grace_s: float = 5.0, wait_s: float = 5.0) -> bool:
    """Stop a process (and, on Windows, its children). True once it is gone."""
    if not isinstance(pid, int) or pid <= 0 or pid == os.getpid():
        return False  # never signal ourselves or a nonsense id
    if not _running(pid):
        return True
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=20, check=False)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return True
        deadline = time.monotonic() + grace_s
        while time.monotonic() < deadline and _running(pid):
            time.sleep(0.1)
        if _running(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                return True
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if not _running(pid):
            return True
        time.sleep(0.1)
    return not _running(pid)
