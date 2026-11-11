"""Read-only, run-scoped data loader for the Streamlit course dashboard.

No raw workbook, cleaned CSV, feature CSV or model binary is opened here.
The selected run ID is always explicit; a failed run cannot inherit another
run's metrics or report content.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

RUN_ID_RE = re.compile(r"^\d{8}T\d{6}Z_[0-9a-f]{8}$")
MAX_TEXT_BYTES = 2_000_000
REPORTS = {
    "quality_review": "crew1/quality_review.md",
    "eda_interpretation": "crew1/eda_interpretation.md",
    "insights": "crew1/insights.md",
    "evaluation_report": "crew2/evaluation_report.md",
    "model_card": "crew2/model_card.md",
    "feature_engineering_review": "crew2/agent_reviews/feature_engineering_review.md",
    "model_selection_review": "crew2/agent_reviews/model_selection_review.md",
    "evaluation_review": "crew2/agent_reviews/evaluation_review.md",
}


def _json_file(path: Path) -> dict[str, Any] | None:
    if not path.is_file() or path.stat().st_size > MAX_TEXT_BYTES:
        return None
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    return result if isinstance(result, dict) else None


def run_folder(runs_root: Path, run_id: str) -> Path:
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValueError("Invalid run ID")
    runs_root = Path(runs_root).resolve()
    folder = (runs_root / run_id).resolve()
    if folder.parent != runs_root:
        raise ValueError("Run folder must be directly below runs/")
    return folder


def list_runs(runs_root: Path) -> list[str]:
    root = Path(runs_root)
    if not root.is_dir():
        return []
    matches = []
    for folder in root.iterdir():
        if folder.is_dir() and RUN_ID_RE.fullmatch(folder.name) and (folder / "run_manifest.json").is_file():
            matches.append(folder.name)
    return sorted(matches, reverse=True)


def snapshot(runs_root: Path, run_id: str) -> dict[str, Any]:
    folder = run_folder(runs_root, run_id)
    manifest = _json_file(folder / "run_manifest.json")
    if not manifest or manifest.get("run_id") != run_id:
        raise ValueError("Missing or mismatched run manifest")
    status = str(manifest.get("status", "UNKNOWN"))
    # All these files come from the selected run directory only.
    gate = _json_file(folder / "handoff_candidate" / "validation_report.json")
    eda = _json_file(folder / "crew1" / "eda_summary.json")
    # A failed/incomplete run never inherits model statistics from an older success.
    complete = (status == "FULL_RUN_COMPLETE_NEEDS_HUMAN_REVIEW"
                and manifest.get("validation_status") == "PASS"
                and manifest.get("crew2_reached") is True)
    metrics = _json_file(folder / "crew2" / "model_metrics.json") if complete else None
    live = _json_file(folder / "live_status.json")  # V2 runs only; None for V1 history
    return {"run_id": run_id, "dir": folder, "manifest": manifest, "live": live,
            "gate": gate, "eda": eda, "metrics": metrics, "complete": complete}


_LIMIT_RULES = {"CSV-006": ("min", "Approved minimum"), "CSV-007": ("max", "Approved maximum"),
                "CSV-005": ("allowed_values", "Approved values"),
                "CHURN-003": ("allowed_values", "Approved values")}


def gate_findings(gate: dict | None) -> list[dict]:
    """Readable rows for each failed gate rule.

    The approved limit is read from the REGISTERED policy named in the gate
    report, never from the run's own contract, so it cannot be relaxed by a run.
    """
    if not gate or not isinstance(gate.get("failed_rules"), list):
        return []
    columns = {}
    try:
        from src.policy_registry import approved_policy_path
        policy = json.loads(approved_policy_path(gate.get("policy_version")).read_text(encoding="utf-8"))
        columns = policy.get("columns", {})
    except (OSError, ValueError, TypeError):
        pass
    rows = []
    for rule in gate["failed_rules"]:
        if not isinstance(rule, dict):
            continue
        column = rule.get("column")
        key, label = _LIMIT_RULES.get(rule.get("rule_id"), (None, None))
        approved = columns.get(column, {}).get(key) if key and column else None
        rows.append({
            "rule_id": rule.get("rule_id"), "column": column, "message": rule.get("message"),
            "observed": ", ".join(map(str, rule.get("observed") or [])) or None,
            "rows": ", ".join(map(str, rule.get("rows") or [])) or None,
            "approved_label": label, "approved": approved,
        })
    return rows


def read_report(run: dict[str, Any], report_name: str) -> str | None:
    if report_name not in REPORTS:
        raise ValueError("Unknown report type")
    relative = REPORTS[report_name]
    # Restrict every report to the current selected run directory.
    root = Path(run["dir"]).resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return None
    if target.stat().st_size > MAX_TEXT_BYTES:
        return None
    # Crew 2 reports should not be displayed as new results for a failed run.
    if relative.startswith("crew2/") and not run["complete"]:
        return None
    return target.read_text(encoding="utf-8")


def eda_html(run: dict[str, Any]) -> bytes | None:
    file = run["dir"] / "crew1" / "eda_report.html"
    if not file.is_file() or file.stat().st_size > MAX_TEXT_BYTES:
        return None
    return file.read_bytes()
