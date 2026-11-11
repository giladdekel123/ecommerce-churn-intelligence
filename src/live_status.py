"""Run-scoped, atomically written execution status (runs/<run_id>/live_status.json).

Records only transitions that are actually observed:
- "stages": coarse progress, including deterministic steps (preparation, contract
  drafting, training/reports) that are not agent work;
- "components": the seven tracked components. Agent states come from real CrewAI
  task events (TaskStarted/Completed/Failed) with the events' own timestamps, or
  from the pipeline's evidence checks; the gate state comes from the gate result.
No percentages or durations are invented. CrewAI delivers events on worker
threads, so every update is locked and component states only move forward.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

STATUS_NAME = "live_status.json"
STATES = {"PENDING", "RUNNING", "COMPLETED", "FAILED", "BLOCKED", "NOT_APPLICABLE"}
GATE = "Independent Validation Gate"
COMPONENTS = (  # (name, crew, kind) in display order
    ("Data Quality Specialist", "crew1", "agent"),
    ("Exploratory Data Analyst", "crew1", "agent"),
    ("Business Insights & Contract Specialist", "crew1", "agent"),
    (GATE, "gate", "gate"),
    ("Feature Engineering Specialist", "crew2", "agent"),
    ("Machine Learning Engineer", "crew2", "agent"),
    ("Model Evaluation Specialist", "crew2", "agent"),
)
# Forward-only transitions. Events may be delivered out of order by CrewAI's
# thread pool, so e.g. a late "started" after "completed" is ignored.
_ALLOWED = {
    "PENDING": {"RUNNING", "COMPLETED", "FAILED", "BLOCKED"},
    "RUNNING": {"COMPLETED", "FAILED"},
    "COMPLETED": set(),  # only an explicit evidence downgrade may turn this into FAILED
    "FAILED": set(),
    "BLOCKED": set(),
}
STAGES = (
    ("source_intake", "deterministic", "Copy workbook, verify SHA-256, write source receipt"),
    ("crew1_preparation", "deterministic", "Prepare clean_data.csv, per-run source profile, EDA"),
    ("crew1_agents", "crew", "Crew 1 agent reviews (crew as a whole)"),
    ("contract_draft", "deterministic", "Draft dataset_contract.json at the end of Crew 1"),
    ("independent_gate", "gate", "Finalize contract and run the independent production gate"),
    ("crew2_modeling", "deterministic", "Crew 2 training and canonical reports"),
    ("crew2_agents", "crew", "Crew 2 agent reviews (crew as a whole)"),
)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class LiveStatus:
    def __init__(self, run_dir: Path, run_id: str, *, not_applicable=()):
        self.path = Path(run_dir) / STATUS_NAME
        if self.path.exists():
            raise FileExistsError(f"Refusing to overwrite live status: {self.path}")
        self._lock = threading.RLock()
        self.data = {
            "run_id": run_id, "updated_at": _utc(), "overall": "RUNNING",
            "agent_tracking": "crewai_task_events",
            "stages": {name: {"kind": kind, "label": label,
                              "state": "NOT_APPLICABLE" if name in not_applicable else "PENDING",
                              "started_at": None, "finished_at": None, "detail": None}
                       for name, kind, label in STAGES},
            "components": {name: {"crew": crew, "kind": kind, "state": "PENDING",
                                  "started_at": None, "finished_at": None, "detail": None, "source": None}
                           for name, crew, kind in COMPONENTS},
        }
        self._write()

    def set(self, stage: str, state: str, detail: str | None = None) -> None:
        if stage not in self.data["stages"] or state not in STATES:
            raise ValueError(f"Unknown stage or state: {stage}/{state}")
        with self._lock:
            entry = self.data["stages"][stage]
            entry["state"] = state
            if state == "RUNNING":
                entry["started_at"] = _utc()
            elif state in ("COMPLETED", "FAILED"):
                entry["finished_at"] = _utc()
            if detail is not None:
                entry["detail"] = str(detail)[:500]
            self._write()

    def set_component(self, name: str, state: str, detail: str | None = None, *, at: str | None = None,
                      source: str = "pipeline", downgrade: bool = False) -> bool:
        """Apply one observed transition; returns False when it is not a forward move.

        at: the observed time (e.g. the CrewAI event's own timestamp).
        downgrade: COMPLETED -> FAILED, only when the pipeline's evidence check fails.
        """
        if name not in self.data["components"] or state not in STATES:
            raise ValueError(f"Unknown component or state: {name}/{state}")
        with self._lock:
            entry = self.data["components"][name]
            current = entry["state"]
            when = at or _utc()
            if state == "RUNNING" and current in ("COMPLETED", "FAILED") and entry["started_at"] is None:
                entry["started_at"] = when  # late-delivered start event: keep its real time only
                self._write()
                return False
            if not (state in _ALLOWED[current] or (downgrade and current == "COMPLETED" and state == "FAILED")):
                return False
            entry["state"] = state
            entry["source"] = source
            if state == "RUNNING":
                entry["started_at"] = when
            elif state in ("COMPLETED", "FAILED"):
                entry["finished_at"] = entry["finished_at"] if downgrade and entry["finished_at"] else when
            if detail is not None:
                entry["detail"] = str(detail)[:500]
            self._write()
            return True

    def note_component(self, name: str, detail: str) -> None:
        """Attach an observation without changing state."""
        with self._lock:
            self.data["components"][name]["detail"] = str(detail)[:500]
            self._write()

    def component_state(self, name: str) -> str:
        with self._lock:
            return self.data["components"][name]["state"]

    def running(self) -> list[str]:
        with self._lock:
            return [n for n, e in self.data["stages"].items() if e["state"] == "RUNNING"]

    def block_unstarted(self, detail: str) -> None:
        """Mark everything that never started as BLOCKED (true once the run has stopped)."""
        with self._lock:
            for name, entry in self.data["stages"].items():
                if entry["state"] == "PENDING":
                    self.set(name, "BLOCKED", detail)
            for name, entry in self.data["components"].items():
                if entry["state"] == "PENDING":
                    self.set_component(name, "BLOCKED", detail)

    def finish(self, overall: str) -> None:
        with self._lock:
            self.data["overall"] = overall
            self._write()

    def _write(self) -> None:
        with self._lock:  # one writer at a time; readers only ever see a complete file
            self.data["updated_at"] = _utc()
            temp = self.path.with_name("." + STATUS_NAME + ".tmp")
            temp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            temp.replace(self.path)
