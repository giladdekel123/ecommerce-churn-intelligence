"""Shared Phase 5 test helpers: isolated folders, a fake process, synthetic runs.

Nothing here starts a real pipeline, calls Gemini, or touches the real runs/ folder.
"""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from src import run_launcher
from src.live_status import LiveStatus


class FakeProcess:
    """Stands in for subprocess.Popen; 'alive' is controlled by the test."""
    instances = []

    def __init__(self, argv, **kwargs):
        self.argv, self.kwargs, self.alive, self.pid = argv, kwargs, True, 40000 + len(FakeProcess.instances)
        FakeProcess.instances.append(self)

    def poll(self):
        return None if self.alive else 0


def isolate(test) -> Path:
    """Point the launcher at a temp tree and replace process creation with FakeProcess."""
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    base = Path(tmp.name)
    FakeProcess.instances = []
    run_launcher._PROCESSES.clear()
    test.addCleanup(run_launcher._PROCESSES.clear)
    for patcher in (patch.object(run_launcher, "RUNS_ROOT", base / "runs"),
                    patch.object(run_launcher, "UPLOADS_DIR", base / "outputs" / "uploads"),
                    patch.object(run_launcher, "LOGS_DIR", base / "outputs" / "logs"),
                    patch.object(run_launcher, "_popen", FakeProcess),
                    patch.object(run_launcher, "pid_alive", lambda pid: False)):
        patcher.start()
        test.addCleanup(patcher.stop)
    (base / "runs").mkdir()
    return base


def write_run(runs_root: Path, run_id: str, *, status: str, gate: dict | None = None,
              crew2_reached=False, training=False, live_updates=(), v2=True) -> Path:
    """Create a synthetic run folder with the same file shapes the pipeline writes."""
    folder = runs_root / run_id
    folder.mkdir(parents=True)
    manifest = {"run_id": run_id, "status": status, "stage": "X", "validation_status":
                (gate or {}).get("status", "NOT_RUN"),
                "failed_rule_ids": [r["rule_id"] for r in (gate or {}).get("failed_rules", [])],
                "crew2_reached": crew2_reached, "model_training_started": training, "model_deployed": False}
    if v2:
        manifest.update({"pipeline_mode": "V2_UPLOAD", "policy_version": "2.0.0"})
        live = LiveStatus(folder, run_id)
        for kind, name, state, detail in live_updates:
            if kind == "stage":
                live.set(name, state, detail)
            else:
                live.set_component(name, state, detail)
    (folder / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if gate is not None:
        (folder / "handoff_candidate").mkdir()
        (folder / "handoff_candidate" / "validation_report.json").write_text(json.dumps(gate), encoding="utf-8")
    return folder


CORRUPTED_GATE = {
    "status": "FAIL", "policy_version": "2.0.0", "contract_version": "1.0.0",
    "authorizes_crew2_training": False, "model_training_started": False,
    "source_integrity": {"mode": "run_receipt", "status": "VERIFIED", "verified_source_sha256": "1d37e8a6" * 8},
    "failed_rules": [{"rule_id": "CSV-007", "column": "SatisfactionScore",
                      "message": "Numeric max constraint violated (1 row(s))", "rows": [2], "observed": ["9"]}],
}
PASS_GATE = {"status": "PASS", "policy_version": "2.0.0", "contract_version": "1.0.0",
             "authorizes_crew2_training": True, "model_training_started": False, "failed_rules": [],
             "source_integrity": {"mode": "run_receipt", "status": "VERIFIED",
                                  "verified_source_sha256": "db70f1e3" * 8}}
