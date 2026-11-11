"""Process control for Streamlit-launched V2 runs (Architecture V2 Phase 5).

- stage_upload(): validates and stores uploaded bytes under outputs/uploads/<sha256>.xlsx
  (Git-ignored, content-addressed so different files never overwrite each other).
  It never parses the workbook; Crew 1 and the pipeline intake do that.
- launch(): starts src/full_pipeline.py --run --upload ... in a separate process,
  with an explicit argument list (no shell), the current interpreter, and a log
  under outputs/logs/. A single exclusive lock file prevents concurrent launches
  from repeated clicks, refreshes or other browser sessions.
- run_state(): classifies a run as RUNNING, FINISHED, INTERRUPTED or NOT_STARTED
  from the process and the run's own manifest, never from guesses.
The policy is chosen by the pipeline (registered V2), never by the upload.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS_ROOT = ROOT / "runs"
UPLOADS_DIR = ROOT / "outputs" / "uploads"
LOGS_DIR = ROOT / "outputs" / "logs"
PIPELINE = ROOT / "src" / "full_pipeline.py"
LOCK_NAME = ".active_run.lock"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
FINAL_SUCCESS = "FULL_RUN_COMPLETE_NEEDS_HUMAN_REVIEW"
_popen = subprocess.Popen          # patched in tests; never shell=True
_PROCESSES: dict = {}              # run_id -> Popen started by this server process


class UploadError(ValueError):
    """The uploaded file cannot be accepted."""


class AlreadyRunning(RuntimeError):
    """Another analysis is already running."""


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_final(status) -> bool:
    return isinstance(status, str) and (status == FINAL_SUCCESS or status.startswith("FAILED_"))


def stage_upload(name: str, data: bytes) -> dict:
    """Store an uploaded workbook. Does NOT start anything."""
    name = Path(str(name or "")).name
    if not name.lower().endswith(".xlsx"):
        raise UploadError("Only Excel .xlsx workbooks are accepted")
    if not data:
        raise UploadError("The uploaded file is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadError(f"The uploaded file exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    if not data.startswith(b"PK\x03\x04"):
        raise UploadError("The file is not a valid .xlsx workbook (not an Office Open XML package)")
    digest = hashlib.sha256(data).hexdigest()
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    target = UPLOADS_DIR / f"{digest}.xlsx"
    if not target.is_file() or _sha256(target) != digest:
        temp = target.with_name(target.name + ".tmp")
        temp.write_bytes(data)
        temp.replace(target)
    return {"name": name, "size": len(data), "sha256": digest, "path": str(target)}


def lock_path(runs_root: Path | None = None) -> Path:
    return Path(runs_root or RUNS_ROOT) / LOCK_NAME


def _read_json(path: Path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def pid_alive(pid) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _alive(info: dict) -> bool:
    process = _PROCESSES.get(info.get("run_id"))
    if process is not None:
        return process.poll() is None
    return pid_alive(info.get("pid"))


def current_run(runs_root: Path | None = None) -> dict | None:
    """The locked run, if any, with 'alive'. A lock whose process ended is released."""
    path = lock_path(runs_root)
    info = _read_json(path)
    if info is None:
        if path.exists():  # unreadable lock: being written right now, treat as active
            return {"run_id": None, "alive": True}
        return None
    if info.get("pid") is None:  # launch in progress (lock taken, process not yet recorded)
        info["alive"] = True
        return info
    info["alive"] = _alive(info)
    if not info["alive"]:
        try:
            path.unlink()
        except OSError:
            pass
    return info


def _write_lock(path: Path, info: dict) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def launch(upload: dict, *, runs_root: Path | None = None, python: str | None = None) -> dict:
    """Start one V2 run for a staged upload. Raises AlreadyRunning if one is active."""
    from src.full_pipeline import _new_id

    source = Path(upload.get("path", "")).resolve()
    if not source.is_relative_to(UPLOADS_DIR.resolve()) or not source.is_file():
        raise UploadError("The uploaded workbook is no longer available; upload it again")
    if _sha256(source) != upload.get("sha256"):
        raise UploadError("The stored upload no longer matches its fingerprint; upload it again")
    runs_root = Path(runs_root or RUNS_ROOT)
    runs_root.mkdir(parents=True, exist_ok=True)
    active = current_run(runs_root)  # also clears a stale lock
    if active and active.get("alive"):
        raise AlreadyRunning(f"Analysis {active.get('run_id')} is already running")
    run_id = _new_id()
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOGS_DIR / f"{run_id}.log"
    info = {"run_id": run_id, "pid": None, "started_at": _utc(), "log": str(log_file),
            "source_name": upload.get("name"), "source_sha256": upload.get("sha256")}
    path = lock_path(runs_root)
    try:  # exclusive create: exactly one launcher wins, across sessions and processes
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise AlreadyRunning("Another analysis is starting or running") from None
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(info))
    argv = [python or sys.executable, str(PIPELINE), "--run", "--upload", str(source),
            "--expected-sha256", upload["sha256"], "--run-id", run_id]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    try:
        with log_file.open("wb") as log:
            process = _popen(argv, cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=log,
                             stderr=subprocess.STDOUT, env=env, creationflags=flags)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    _PROCESSES[run_id] = process
    info.update({"pid": process.pid, "argv": argv})
    _write_lock(path, info)
    (LOGS_DIR / f"{run_id}.launch.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    return info


def log_tail(run_id: str, limit: int = 4000) -> str:
    path = LOGS_DIR / f"{run_id}.log"
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-limit:]
    except OSError:
        return ""


def run_state(run_id: str, *, runs_root: Path | None = None) -> dict:
    """Truthful state of one run: RUNNING / FINISHED / INTERRUPTED / NOT_STARTED / UNKNOWN."""
    runs_root = Path(runs_root or RUNS_ROOT)
    folder = runs_root / str(run_id)
    manifest = _read_json(folder / "run_manifest.json")
    live = _read_json(folder / "live_status.json")
    active = current_run(runs_root)
    running = bool(active and active.get("alive") and active.get("run_id") == run_id)
    status = manifest.get("status") if manifest else None
    if running:
        state = "RUNNING"
    elif is_final(status):
        state = "FINISHED"
    elif manifest is not None:
        state = "INTERRUPTED"  # process gone, but no final status was ever recorded
    elif (LOGS_DIR / f"{run_id}.launch.json").is_file():
        state = "NOT_STARTED"  # process ended before creating the run (e.g. readiness BLOCKED)
    else:
        state = "UNKNOWN"
    return {"run_id": run_id, "state": state, "status": status, "manifest": manifest, "live": live}
