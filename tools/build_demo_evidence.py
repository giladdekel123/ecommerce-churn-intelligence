"""Build the public-demo evidence bundle from two genuine local runs.

LOCAL, MANUAL TOOL. It is not imported by the demo and never runs in the cloud.
Strict allow-list: every published field is named explicitly below, so a new
field in a run artifact can never leak. Excerpts must be verbatim substrings of
the source report. The output is scanned for local paths, secrets and e-mail
addresses and the build fails if any are found.

    python tools/build_demo_evidence.py
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.dashboard_data import gate_findings  # noqa: E402  (production loader, reused read-only)

OUT = ROOT / "demo" / "evidence"
RUNS = ROOT / "runs"
SCENARIOS = {
    "pass": {
        "run_id": "20261111T094219Z_1fbdc4d8",
        "title": "PASS — original dataset",
        "source_description": "The original, unmodified public dataset workbook.",
    },
    "fail": {
        "run_id": "20261111T104100Z_ab39b7ad",
        "title": "FAIL — deliberately corrupted workbook",
        "source_description": "A controlled test workbook in which one SatisfactionScore was set to 9 "
                              "(approved range 1–5), to prove the gate fails closed.",
    },
}

# (scenario, component, source file inside the run, verbatim excerpt)
EXCERPTS = [
    ("pass", "Data Quality Specialist", "crew1/quality_review.md",
     "**Row Count & Identifier Integrity:** The dataset contains exactly 5,630 rows both before and after "
     "preparation (`row_count_before: 5630`, `row_count_after: 5630`), with 0 rows dropped."),
    ("pass", "Data Quality Specialist", "crew1/quality_review.md",
     "**Risk of Categorical Fragmentation:** Unconsolidated category labels (e.g., separate entries for "
     "abbreviations vs. full names in payment modes and devices) will artificially inflate feature "
     "dimensionality and fragment grouping logic."),
    ("pass", "Exploratory Data Analyst", "crew1/eda_interpretation.md",
     "**Association vs. Causation:** These observed differences in historical churn rates across login devices "
     "describe statistical associations only."),
    ("pass", "Business Insights & Contract Specialist", "crew1/insights.md",
     "**Pending Independent Gate:** Crew 2 authorization is **pending the independent deterministic gate**, "
     "which runs after this review. No agent can claim Crew 2 is approved or permanently blocked at this stage."),
    ("pass", "Feature Engineering Specialist", "crew2/agent_reviews/feature_engineering_review.md",
     "**Prevention of Data Leakage:** Fitting transformers across the entire dataset exposes the training "
     "procedure to summary statistics (means, standard deviations, category distributions) of the test set."),
    ("pass", "Machine Learning Engineer", "crew2/agent_reviews/model_selection_review.md",
     "Model selection was performed strictly via 3-fold stratified cross-validation on the training dataset "
     "(`4,504` rows, `758` churn cases) without access to the held-out test set."),
    ("pass", "Model Evaluation Specialist", "crew2/agent_reviews/evaluation_review.md",
     "Held-out test metrics reflect static historical distributions under closed-world assumptions. They do "
     "not constitute operational guarantees or field deployment warranties in the presence of concept drift, "
     "shifting macroeconomic factors, or changing customer behaviors."),
    ("fail", "Data Quality Specialist", "crew1/quality_review.md",
     "Maintain `authorizes_crew2_training: false` until category mappings, range rules, and missing data "
     "strategies are fully signed off."),
    ("fail", "Exploratory Data Analyst", "crew1/eda_interpretation.md",
     "Crew 2 training is strictly unauthorized (`authorizes_crew2_training: false`)."),
    ("fail", "Business Insights & Contract Specialist", "crew1/insights.md",
     "Crew 2 training authorization is **pending the independent deterministic gate**, which runs after "
     "this review."),
]
# The three Crew 2 agents never ran in the FAIL scenario, so no excerpts exist for them.

FORBIDDEN = [r"[A-Za-z]:[\\/]", r"/Users/", r"/home/", r"\\\\", r"API[_-]?KEY", r"AIza", r"\.env",
             r"password", r"secret", r"token", r"[\w.+-]+@[\w-]+\.[\w.]+"]
MANIFEST_KEYS = ["status", "validation_status", "failed_rule_ids", "crew2_reached", "model_training_started",
                 "model_deployed", "policy_version", "pipeline_mode", "started_at", "updated_at"]
COMPONENT_KEYS = ["crew", "kind", "state", "started_at", "finished_at", "detail"]
STAGE_KEYS = ["kind", "label", "state", "started_at", "finished_at"]


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def build(key: str, spec: dict) -> dict:
    run = RUNS / spec["run_id"]
    manifest = load(run / "run_manifest.json")
    receipt = load(run / "source_receipt.json")
    live = load(run / "live_status.json")
    gate = load(run / "handoff_candidate" / "validation_report.json")
    assert manifest["run_id"] == spec["run_id"] == gate["run_id"] == receipt["run_id"]

    findings = [{"rule_id": f["rule_id"], "column": f["column"], "message": f["message"],
                 "observed": f["observed"], "csv_lines": f["rows"],
                 "approved_label": f["approved_label"], "approved_value": f["approved"]}
                for f in gate_findings(gate)]
    out = {
        "scenario": key,
        "title": spec["title"],
        "run_id": spec["run_id"],
        "source_description": spec["source_description"],
        "source_sha256_short": receipt["source_sha256"][:12],
        "manifest": {k: manifest.get(k) for k in MANIFEST_KEYS},
        "gate": {"status": gate["status"], "policy_version": gate["policy_version"],
                 "authorizes_crew2_training": gate["authorizes_crew2_training"],
                 "source_integrity_status": gate["source_integrity"]["status"],
                 "findings": findings},
        "stages": {n: {k: s.get(k) for k in STAGE_KEYS} for n, s in live["stages"].items()},
        "components": [{"name": n, **{k: c.get(k) for k in COMPONENT_KEYS}}
                      for n, c in live["components"].items()],
        "metrics": None,
        "excerpts": [],
    }
    if manifest.get("crew2_reached"):
        m = load(run / "crew2" / "model_metrics.json")
        out["metrics"] = {
            "winner": m["winner"], "selection_rule": m["selection_rule"], "test_use": m["test_use"],
            "cv_training_only": m["cv_training_only"], "holdout_once": m["holdout_once"],
            "total_rows": m["total_rows"], "training_rows": m["training_rows"], "test_rows": m["test_rows"],
            "training_churn_count": m["training_churn_count"], "test_churn_count": m["test_churn_count"],
            "limitations": m["limitations"],
        }
    for scen, component, rel, text in EXCERPTS:
        if scen != key:
            continue
        source = (run / rel).read_text(encoding="utf-8")
        if text not in source:
            raise SystemExit(f"Excerpt is not verbatim in {spec['run_id']}/{rel}: {text[:60]}...")
        out["excerpts"].append({"component": component, "source_report": rel, "text": text})
    return out


def scan(name: str, text: str) -> None:
    for pattern in FORBIDDEN:
        hit = re.search(pattern, text, re.IGNORECASE)
        if hit:
            raise SystemExit(f"Leak scan failed in {name}: /{pattern}/ matched {hit.group(0)!r}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    digests = {}
    for key, spec in SCENARIOS.items():
        data = build(key, spec)
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        scan(f"{key}.json", text)
        (OUT / f"{key}.json").write_text(text, encoding="utf-8", newline="\n")
        digests[f"{key}.json"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    # The honest FAIL narrative ("the agents did not notice, the gate did") must stay true.
    fail_text = (OUT / "fail.json").read_text(encoding="utf-8")
    fail_run = RUNS / SCENARIOS["fail"]["run_id"] / "crew1"
    for report in fail_run.glob("*.md"):
        assert "SatisfactionScore" not in report.read_text(encoding="utf-8"), report.name
    assert "CSV-007" in fail_text
    (OUT / "MANIFEST.json").write_text(json.dumps({"sha256": digests}, indent=2) + "\n", encoding="utf-8",
                                       newline="\n")
    print("Wrote", ", ".join(digests), "to", OUT)


if __name__ == "__main__":
    main()
