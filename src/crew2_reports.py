"""Crew 2: audit fixed model metrics and render the instructor's two required reports.

No AI calls, no training and no access to the raw workbook. Never overwrites prior
reports. The source of numeric claims is the immutable Crew 2 model_metrics.json.

Usage from repository root:
    python src/crew2_reports.py --run-dir runs/<id> --check
    python src/crew2_reports.py --run-dir runs/<id> --write
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EXPECTED_MODELS = ("majority_baseline", "logistic_regression", "random_forest")
FORBIDDEN_PREDICTORS = {"CustomerID", "Churn", "Gender", "MaritalStatus", "CityTier"}


def _json(path: Path) -> dict:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError(f"Expected JSON object: {path.name}")
    return obj


def validate_metrics(run_dir: Path) -> dict:
    """Validate persisted modeling evidence before generating narrative artifacts."""
    root = Path(run_dir).resolve()
    c2 = root / "crew2"
    required = (c2 / "model_metrics.json", c2 / "split_manifest.json",
                c2 / "features.csv", c2 / "model.joblib",
                root / "handoff_candidate" / "validation_report.json",
                root / "handoff_candidate" / "dataset_contract.json")
    missing = [str(p.relative_to(root)) for p in required if not p.is_file()]
    if missing:
        raise ValueError("Missing verified modeling evidence: " + ", ".join(missing))
    m, split, gate, contract = (_json(required[0]), _json(required[1]),
                                _json(required[4]), _json(required[5]))
    if (m.get("status") != "TRAINED_AND_EVALUATED_NOT_DEPLOYED"
        or m.get("run_id") != root.name or split.get("run_id") != root.name
        or gate.get("run_id") != root.name or contract.get("run_id") != root.name):
        raise ValueError("Modeling status or run identity mismatch")
    if (gate.get("status") != "PASS" or gate.get("failed_rules") != []
        or gate.get("authorizes_crew2_training") is not True):
        raise ValueError("Saved production gate does not authorize this run")
    if m.get("source_clean_data_sha256") != contract.get("prepared_data", {}).get("sha256"):
        raise ValueError("Training source hash does not match final handoff contract")
    predictors = m.get("approved_predictor_columns")
    if (not isinstance(predictors, list) or len(predictors) != 15
        or len(set(predictors)) != 15 or set(predictors) & FORBIDDEN_PREDICTORS
        or predictors != contract.get("model_input_columns")):
        raise ValueError("Predictors do not match the approved contract")
    with required[2].open("r", newline="", encoding="utf-8-sig") as handle:
        header = next(csv.reader(handle), None)
    if header != predictors:
        raise ValueError("features.csv header does not match approved predictors")
    if required[3].stat().st_size <= 0:
        raise ValueError("Saved model file is empty")
    rows, train, test = (m.get(k) for k in ("total_rows", "training_rows", "test_rows"))
    if any(type(z) is not int or z <= 0 for z in (rows, train, test)) or train + test != rows:
        raise ValueError("Invalid total / train / test row counts")
    a, b = split.get("train_row_indices"), split.get("test_row_indices")
    if (not isinstance(a, list) or not isinstance(b, list)
        or len(a) != train or len(b) != test
        or any(type(z) is not int for z in a + b)
        or len(set(a)) != train or len(set(b)) != test
        or set(a) & set(b) or set(a + b) != set(range(rows))):
        raise ValueError("Training/test indices are inconsistent or overlap")
    if not isinstance(m.get("holdout_once"), dict) or set(m["holdout_once"]) != set(EXPECTED_MODELS):
        raise ValueError("Expected three held-out model comparisons")
    if not isinstance(m.get("cv_training_only"), dict) or set(m["cv_training_only"]) != set(EXPECTED_MODELS[1:]):
        raise ValueError("Cross-validation results are missing")
    winner = m.get("winner")
    if winner not in EXPECTED_MODELS[1:]:
        raise ValueError("Invalid selected winner")
    cv = m["cv_training_only"]
    if any(not isinstance(cv[k].get("f1_mean"), (float, int)) for k in cv):
        raise ValueError("Missing numeric train-only CV F1")
    if winner != max(EXPECTED_MODELS[1:], key=lambda k: cv[k]["f1_mean"]):
        raise ValueError("Winner inconsistent with training-only CV model selection")
    if (m.get("selection_rule") != "Highest mean positive-class F1 across 3 stratified training-only CV folds"
        or m.get("test_use") != "Held-out test set evaluated once, after model selection; not used to select winner"):
        raise ValueError("Missing documented model-selection protocol")
    for name, values in m["holdout_once"].items():
        confusion = values.get("confusion_matrix")
        if (not isinstance(confusion, list) or len(confusion) != 2
            or any(not isinstance(row, list) or len(row) != 2 for row in confusion)):
            raise ValueError(f"Invalid confusion matrix for {name}")
        tn, fp = confusion[0]
        fn, tp = confusion[1]
        if any(type(n) is not int or n < 0 for n in (tn, fp, fn, tp)) or tn + fp + fn + tp != test:
            raise ValueError(f"Invalid held-out test totals for {name}")
        if tp + fn != m.get("test_churn_count"):
            raise ValueError(f"Held-out positive count mismatch for {name}")
        expected = {
            "accuracy": (tp + tn) / test,
            "precision": tp / (tp + fp) if tp + fp else 0.0,
            "recall": tp / (tp + fn) if tp + fn else 0.0,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0,
        }
        for key, computed in expected.items():
            actual = values.get(key)
            if not isinstance(actual, (float, int)) or not math.isfinite(actual) or abs(actual - computed) > 0.000002:
                raise ValueError(f"Inconsistent {key} for {name}")
        auc = values.get("roc_auc")
        if auc is not None and (not isinstance(auc, (float, int)) or not 0 <= auc <= 1):
            raise ValueError(f"Invalid ROC-AUC for {name}")
    return m


def _pct(n: float) -> str:
    return f"{n * 100:.1f}%"


def report_texts(metrics: dict) -> tuple[str, str]:
    """Render measured values only; AI is not allowed to change metric figures."""
    m = metrics
    hold = m["holdout_once"]
    best = hold[m["winner"]]
    tn, fp = best["confusion_matrix"][0]
    fn, tp = best["confusion_matrix"][1]
    lines = [
        "# Crew 2 — Evaluation Report", "",
        f"**Run:** `{m['run_id']}`  ",
        "**Status:** Trained and evaluated, **not deployed**  ",
        f"**Selection:** {m['selection_rule']}  ",
        f"**Data split:** {m['training_rows']:,} training / {m['test_rows']:,} held-out test customers; stratified and disjoint", "",
        "## Held-out evaluation", "",
        "| Model | Accuracy | Precision (churn) | Recall (churn) | F1 (churn) | ROC-AUC |", 
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in EXPECTED_MODELS:
        val = hold[name]
        auc_text = f"{val['roc_auc']:.3f}" if val['roc_auc'] is not None else "Not available"
        lines.append(f"| `{name}` | {_pct(val['accuracy'])} | {_pct(val['precision'])} | {_pct(val['recall'])} | {_pct(val['f1'])} | {auc_text} |")
    lines += [
        "", "## Training-only cross-validation (model selection)", "",
        "| Model | Mean churn F1 | Standard deviation |", "|---|---:|---:|",
    ]
    for name in EXPECTED_MODELS[1:]:
        entry = m["cv_training_only"][name]
        lines.append(f"| `{name}` | {entry['f1_mean']:.3f} | {entry['f1_std']:.3f} |")
    lines += [
        "", f"Selected **{m['winner']}** using training-fold CV only. No winner was selected by comparing held-out test scores.",
        "", "## Selected model's confusion matrix", "",
        f"Of **{m['test_churn_count']}** actual churners, the classifier detected **{tp}** and missed **{fn}**.",
        f"Of **{m['test_rows'] - m['test_churn_count']}** non-churners, it correctly cleared **{tn}** and falsely flagged **{fp}**.",
        f"Its churn recall is **{_pct(best['recall'])}**, which is not the same as overall accuracy (**{_pct(best['accuracy'])}**).",
        "", "## Evaluation safeguards and limitations", "",
        "- The held-out set was disjoint from training. Imputation and encoding are fitted within training folds / pipelines.",
        "- The baseline's strong accuracy arises from the non-churn majority; it detects no churners at the default class rule.",
        "- Classifier decision thresholds were not optimized against the held-out test set. Any future threshold selection requires training-only validation.",
        "- The label is historical churn; no forward prediction horizon is documented. These are retrospective test results, not operational guarantees.",
        "- Possible temporal ambiguity of behavioral predictors (such as complaint or satisfaction) remains a research limitation.",
        "- Correlations, associations and metrics cannot prove causality or effectiveness of retention interventions.",
        "- Do not deploy this model or claim prospective accuracy without external and temporal validation.",
        "", "## Provenance", "",
        f"- Source prepared-data SHA-256: `{m['source_clean_data_sha256']}`",
        "- Independent production gate PASS required before training.",
        "- All metrics are taken from persisted `model_metrics.json`, not estimated by an LLM.",
        "",
    ]
    auc_best = f"{best['roc_auc']:.3f}" if best["roc_auc"] is not None else "not available"
    card = [
        "# Crew 2 — Model Card", "",
        f"**Selected algorithm:** `{m['winner']}`  ",
        "**Type:** Historical binary churn classifier (0 = non-churn; 1 = churn)  ",
        "**Status:** Course prototype, trained and evaluated; **not deployed**", "",
        "## Purpose and intended use", "",
        "Study retrospective associations and demonstrate a safe contract-validated modeling workflow. Not for autonomous customer decisions or production deployment.",
        "", "## Dataset, provenance and governance", "",
        f"- Source: approved handoff for run `{m['run_id']}`. Total **{m['total_rows']:,}** customer rows.",
        f"- Split: **{m['training_rows']:,}** training and **{m['test_rows']:,}** held out; {m['test_churn_count']} positive churn labels in test.",
        f"- Approved input predictors: {', '.join('`' + x + '`' for x in m['approved_predictor_columns'])}.",
        f"- Excluded identifiers, target and demographic/location fields: {', '.join('`' + x + '`' for x in m['excluded_inputs'])}.",
        "- Missing values are imputed within training-fitted pipelines; categorical values remain unmerged until approved.",
        f"- Prepared input SHA-256: `{m['source_clean_data_sha256']}`.",
        "", "## Model selection and held-out results", "",
        "- Candidates: majority-class baseline, logistic regression, random forest.",
        f"- Selection: training-only three-fold stratified CV by positive-class F1; selected `{m['winner']}`.",
        f"- Holdout: accuracy **{_pct(best['accuracy'])}**, precision **{_pct(best['precision'])}**, recall **{_pct(best['recall'])}**, F1 **{_pct(best['f1'])}**, ROC-AUC **{auc_best}**.",
        f"- Churners detected: **{tp}**; churners missed: **{fn}**; false alarms: **{fp}**.",
        "", "## Risks, fairness and limitations", "",
        "- No future outcome horizon or prospectively verified units are established by the source; do not claim 30-day or 90-day prediction.",
        "- The source may contain time-leakage risks because some behavioral predictors' observation times are undocumented.",
        "- Excluding `Gender`, `MaritalStatus`, and `CityTier` reduces direct use of those variables but does **not** establish fairness; proxy and subgroup analysis remains pending.",
        "- Label imbalance (historical churn minority), false negatives, and threshold choices affect operational decisions.",
        "- Device categories and other possible aliases remain separate under the approved policy.",
        "- Performance is measured on one random holdout; temporal, external and deployment monitoring validations are not yet performed.",
        "- The model is not calibrated for monetary return and no intervention effect has been demonstrated.",
        "", "## Reproducibility", "",
        "- Fitted scikit-learn preprocessing and estimator are saved together in `model.joblib`.",
        "- `model_metrics.json` and `split_manifest.json` record metrics, cross-validation and sample partitioning.",
        "- Raw workbook, generated feature data and model binary remain local and Git-ignored; do not commit customer-data dumps.",
        "- This model card is assembled deterministically from checked metrics and remains subject to human review.",
        "",
    ]
    return "\n".join(lines), "\n".join(card)


def check(run_dir: Path) -> dict:
    m = validate_metrics(run_dir)
    return {"status": "CREW2_METRICS_VERIFIED_FOR_REPORTING", "run_id": m["run_id"],
            "winner": m["winner"], "held_out_test_rows": m["test_rows"],
            "reports_generated": False, "model_deployed": False}


def write_reports(run_dir: Path) -> dict:
    run_dir = Path(run_dir).resolve()
    m = validate_metrics(run_dir)
    evaluation, card = report_texts(m)
    outputs = [run_dir / "crew2" / "evaluation_report.md", run_dir / "crew2" / "model_card.md"]
    if any(p.exists() for p in outputs):
        raise FileExistsError("Will not overwrite existing reports: " + ", ".join(str(p) for p in outputs if p.exists()))
    try:
        outputs[0].write_text(evaluation, encoding="utf-8", newline="\n")
        outputs[1].write_text(card, encoding="utf-8", newline="\n")
    except BaseException:
        for item in outputs:
            item.unlink(missing_ok=True)
        raise
    return {"status": "REPORTS_CREATED_PENDING_HUMAN_REVIEW", "run_id": m["run_id"],
            "outputs": [str(p) for p in outputs], "model_deployed": False}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Crew 2 evidence-based evaluation and model card")
    p.add_argument("--run-dir", required=True, type=Path)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--write", action="store_true")
    args = p.parse_args(argv)
    try:
        result = check(args.run_dir) if args.check else write_reports(args.run_dir)
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, FileNotFoundError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc), "model_deployed": False}, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
