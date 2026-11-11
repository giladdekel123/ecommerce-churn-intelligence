"""Bridge real CrewAI task events to per-agent live status (Architecture V2 Phase 4).

Mechanism (verified against installed crewai 1.15.37):
- crewai.task emits TaskStartedEvent before an agent works on a task,
  TaskCompletedEvent only after the output (and its output_file) is saved, and
  TaskFailedEvent when the task raises. Task.callback is NOT used: it runs before
  the output file is written, so it could report a task as done too early.
- The event bus runs sync handlers on a thread pool. Handlers are therefore
  matched by task_id, the status store is locked and forward-only, and the bus is
  flushed before the crew's result is judged.
- Only this function's own handlers are registered and removed (on/off).
  scoped_handlers() is avoided because it also detaches CrewAI's own listeners.
"""
from __future__ import annotations

from contextlib import contextmanager

FLUSH_TIMEOUT_SECONDS = 30.0


def _iso(timestamp) -> str | None:
    return timestamp.isoformat(timespec="seconds") if hasattr(timestamp, "isoformat") else None


@contextmanager
def track_crew_tasks(progress, task_components: dict):
    """Record real task events for {Task: component name} while the crew runs.

    progress: a LiveStatus (or None for V1 runs, where nothing is tracked).
    """
    if progress is None:
        yield
        return
    from crewai.events import (TaskCompletedEvent, TaskFailedEvent, TaskStartedEvent,
                               crewai_event_bus)

    by_task_id = {str(task.id): name for task, name in task_components.items()}

    def component(event):
        return by_task_id.get(str(getattr(event, "task_id", "") or getattr(getattr(event, "task", None), "id", "")))

    def on_started(source, event):
        name = component(event)
        if name:
            progress.set_component(name, "RUNNING", at=_iso(event.timestamp), source="crewai_task_event")

    def on_completed(source, event):
        name = component(event)
        if name:
            progress.set_component(name, "COMPLETED", at=_iso(event.timestamp), source="crewai_task_event")

    def on_failed(source, event):
        name = component(event)
        if name:
            progress.set_component(name, "FAILED", f"Task failed: {event.error}", at=_iso(event.timestamp),
                                   source="crewai_task_event")

    handlers = ((TaskStartedEvent, on_started), (TaskCompletedEvent, on_completed),
                (TaskFailedEvent, on_failed))
    for event_type, handler in handlers:
        crewai_event_bus.on(event_type)(handler)
    try:
        try:
            yield
        except BaseException as exc:
            flushed = crewai_event_bus.flush(timeout=FLUSH_TIMEOUT_SECONDS)
            for name in task_components.values():
                state = progress.component_state(name)
                if state == "RUNNING":  # the crew stopped while this task was in progress
                    progress.set_component(name, "FAILED", f"Crew stopped during this task: {exc}",
                                           source="crew_exception")
                elif state == "PENDING":  # genuinely never started
                    progress.set_component(name, "BLOCKED", "Not started: an earlier agent task failed",
                                           source="crew_exception")
            if not flushed:
                for name in task_components.values():
                    progress.note_component(name, "Event delivery timed out; state may be incomplete")
            raise
        else:
            if not crewai_event_bus.flush(timeout=FLUSH_TIMEOUT_SECONDS):
                for name in task_components.values():
                    if progress.component_state(name) != "COMPLETED":
                        progress.note_component(name, "Completion event not delivered within timeout")
    finally:
        for event_type, handler in handlers:
            crewai_event_bus.off(event_type, handler)


def downgrade_without_evidence(progress, expected: dict) -> None:
    """A finished task without its required tool call or output file is not complete.

    expected: {component name: reason string or None}; None means the evidence is present.
    """
    if progress is None:
        return
    for name, problem in expected.items():
        if problem:
            progress.set_component(name, "FAILED", "Required evidence missing: " + problem,
                                   source="evidence_check", downgrade=True)
