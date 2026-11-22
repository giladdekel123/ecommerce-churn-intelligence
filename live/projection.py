"""Sanitized, allow-listed view of one run for the browser.

The browser never receives raw files. Everything shown is rebuilt here from named fields:
no manifest, no absolute paths, no log, no receipt, no ledger identity (user ID, request token,
process ID). Every string is also passed through a redactor for paths, e-mail addresses and
key/token-shaped text, and a field that still looks unsafe is replaced by "[withheld]".
Reports appear only for agents whose recorded state is COMPLETED, as they become available.
"""
from __future__ import annotations

import re
from pathlib import Path

from src import run_launcher
from src.dashboard_data import gate_findings, run_folder, snapshot

MAX_REPORT_BYTES = 20_000
MAX_TEXT_CHARS = 20_000
MAX_DETAIL_CHARS = 200

REPORTS = {  # component name -> (file inside the run folder, title)
    "Data Quality Specialist": ("crew1/quality_review.md", "Data quality review"),
    "Exploratory Data Analyst": ("crew1/eda_interpretation.md", "Exploratory analysis interpretation"),
    "Business Insights & Contract Specialist": ("crew1/insights.md", "Business insights"),
    "Feature Engineering Specialist": ("crew2/agent_reviews/feature_engineering_review.md", "Feature governance review"),
    "Machine Learning Engineer": ("crew2/agent_reviews/model_selection_review.md", "Model selection review"),
    "Model Evaluation Specialist": ("crew2/agent_reviews/evaluation_review.md", "Held-out evaluation review"),
}

_LEAK = [
    re.compile(r"[A-Za-z]:[\\/][^\s\"'<>|`]*"),                                             # Windows path
    re.compile(r"\\\\[^\s\"'<>|`]+"),                                                       # UNC path
    re.compile(r"(?<![\w.:/-])/(?:home|Users|root|var|tmp|etc|mnt|opt|srv|usr|proc|run|media)(?:/[^\s\"'<>|`]*)?"),
    re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),                                            # e-mail
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),                                                 # Google API key
    re.compile(r"eyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}"),                                      # JWT
    re.compile(r"(?i)[\w-]*(?:api[_-]?key|secret|password|passwd)[\w-]*\s*[:=]\s*\S+"),     # names such as db_password=...
    re.compile(r"(?i)\b(?:access_|id_|refresh_|auth_)?token\b\s*[:=]\s*\S+"),               # token: ... (not total_tokens: 12)
    re.compile(r"(?i)\bBearer\s+[\w.\-]{16,}"),
]


def contains_leak(text) -> bool:
    return isinstance(text, str) and any(p.search(text) for p in _LEAK)


def sanitize_text(text, limit: int = MAX_TEXT_CHARS) -> str | None:
    if text is None:
        return None
    text = str(text)[:limit]
    for pattern in _LEAK:
        text = pattern.sub("[redacted]", text)
    return "[withheld]" if contains_leak(text) else text


def _short(text):
    return sanitize_text(text, MAX_DETAIL_CHARS)


def _stage(key, stage):
    return {"key": key, "label": _short(stage.get("label")), "kind": stage.get("kind"), "state": stage.get("state"),
            "started_at": stage.get("started_at"), "finished_at": stage.get("finished_at"),
            "detail": _short(stage.get("detail"))}


def _component(name, comp):
    return {"name": name, "crew": comp.get("crew"), "kind": comp.get("kind"), "state": comp.get("state"),
            "started_at": comp.get("started_at"), "finished_at": comp.get("finished_at"),
            "detail": _short(comp.get("detail"))}


def _gate(gate):
    if not gate:
        return None
    integrity = gate.get("source_integrity") or {}
    return {"status": gate.get("status"), "policy_version": _short(gate.get("policy_version")),
            "authorizes_crew2_training": gate.get("authorizes_crew2_training") is True,
            "source_integrity": integrity.get("status"),
            "findings": [{"rule_id": _short(f["rule_id"]), "column": _short(f["column"]),
                          "message": _short(f["message"]), "observed": _short(f["observed"]),
                          "csv_lines": _short(f["rows"]), "approved_label": _short(f["approved_label"]),
                          "approved_value": f["approved"] if isinstance(f["approved"], (int, float)) else _short(f["approved"])}
                         for f in gate_findings(gate)]}


_METRIC_KEYS = ("accuracy", "precision", "recall", "f1", "roc_auc", "confusion_matrix")


def _metrics(metrics):
    if not metrics:
        return None
    holdout = {name: {k: values.get(k) for k in _METRIC_KEYS}
               for name, values in (metrics.get("holdout_once") or {}).items() if isinstance(values, dict)}
    return {"winner": _short(metrics.get("winner")), "selection_rule": _short(metrics.get("selection_rule")),
            "holdout": holdout,
            **{k: metrics.get(k) for k in ("total_rows", "training_rows", "test_rows", "test_churn_count")},
            "note": "Retrospective experimental results, not evidence of production readiness."}


def _read_report(folder: Path, relative: str):
    target = (folder / relative).resolve()
    if not target.is_relative_to(folder.resolve()) or not target.is_file():
        return None
    if target.stat().st_size > MAX_REPORT_BYTES:
        return "[withheld: report larger than the display limit]"
    return sanitize_text(target.read_text(encoding="utf-8", errors="replace"))


def project_run(run_id: str, *, runs_root, ledger_row: dict | None = None) -> dict:
    """The browser-safe view of one run. Never raises for a run that has not written files yet."""
    runs_root = Path(runs_root)
    folder = run_folder(runs_root, run_id)  # validates the id and keeps the path inside runs_root
    out = {"run_id": run_id, "run_state": run_launcher.run_state(run_id, runs_root=runs_root).get("state"),
           "limit_state": None, "overall": None, "stages": [], "components": [], "gate": None,
           "outcome": None, "reports": [], "metrics": None}
    if ledger_row:
        out["limit_state"] = ledger_row.get("state")
        out["limit_detail"] = _short(ledger_row.get("detail"))
    try:
        snap = snapshot(runs_root, run_id)
    except ValueError:
        return out  # manifest not written yet: the run is still starting
    live = snap["live"] or {}
    manifest = snap["manifest"]
    out["overall"] = _short(live.get("overall") or manifest.get("status"))
    out["stages"] = [_stage(k, v) for k, v in (live.get("stages") or {}).items()]
    out["components"] = [_component(k, v) for k, v in (live.get("components") or {}).items()]
    out["gate"] = _gate(snap["gate"])
    out["outcome"] = {"validation_status": _short(manifest.get("validation_status")),
                      "crew2_reached": manifest.get("crew2_reached") is True,
                      "model_training_started": manifest.get("model_training_started") is True,
                      "model_deployed": False}  # the pipeline never deploys; shown explicitly
    completed = {c["name"] for c in out["components"] if c["state"] == "COMPLETED"}
    for name, (relative, title) in REPORTS.items():
        if name in completed:
            text = _read_report(folder, relative)
            if text is not None:
                out["reports"].append({"component": name, "title": title, "text": text})
    out["metrics"] = _metrics(snap["metrics"])
    return out
