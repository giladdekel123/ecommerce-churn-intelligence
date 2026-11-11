"""Crew 2 deterministic modeling foundation — NO AI inference in this file.

Run from the repository root with an existing, independently validated Crew 1 run:
  python src/crew2_train.py --run-dir runs/<run-id> --check
  python src/crew2_train.py --run-dir runs/<run-id> --run

Guardrails:
- Re-runs the independent production gate before accessing customer model inputs.
- Requires the saved PASS report for the same run, policy, contract and CSV.
- Reads only approved Crew 1 handoff files (never the raw workbook).
- Fits all learned preprocessing inside cross-validation/training folds.
- Selects a model by *training-fold* cross-validation, not test performance.
- Refuses overwriting an existing Crew 2 run directory.
- Does not create the final CrewAI agent reports or deploy a model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FORBIDDEN_INPUTS = {"CustomerID", "Churn", "Gender", "MaritalStatus", "CityTier"}
POLICY_PATH = ROOT / "contracts" / "production_policy_v1.json"


def _read_json(path: Path) -> dict:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError(f"JSON object required: {path}")
    return obj


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _same_file_in_report(value: str, expected: Path) -> bool:
    supplied = Path(value)
    if not supplied.is_absolute():
        supplied = ROOT / supplied
    return supplied.resolve() == expected.resolve()


def verify_handoff(run_dir: Path, policy_path: Path | None = None) -> dict:
    """Fail closed: verify the saved proof *and* independently revalidate actual bytes.

    policy_path: an explicit registered policy, or None to use the version the
    run's contract declares. Either way only a registered approved policy
    (src/policy_registry.py) is accepted; a contract cannot supply a path or rules.
    """
    from src.policy_registry import approved_policy_path, require_registered
    from src.production_gate import integrity_mode, validate_production_handoff
    from src.source_receipt import receipt_path

    run_dir = Path(run_dir).resolve()
    approved_runs_dir = (ROOT / "runs").resolve()
    if not run_dir.is_relative_to(approved_runs_dir) or run_dir == approved_runs_dir:
        raise ValueError("Run directory must be inside this project's runs/ folder")

    csv_path = run_dir / "crew1" / "clean_data.csv"
    contract_path = run_dir / "handoff_candidate" / "dataset_contract.json"
    report_path = run_dir / "handoff_candidate" / "validation_report.json"
    if policy_path is not None:
        policy_path = require_registered(policy_path).resolve()
    else:
        if not contract_path.is_file():
            raise FileNotFoundError(f"Missing required handoff input: {contract_path}")
        policy_path = approved_policy_path(
            _read_json(contract_path).get("approved_policy_version")).resolve()
    # Do not create any output before these checks pass.
    for path in (policy_path, csv_path, contract_path, report_path):
        if not path.is_file():
            raise FileNotFoundError(f"Missing required handoff input: {path}")

    policy = _read_json(policy_path)
    contract = _read_json(contract_path)
    report = _read_json(report_path)

    if (report.get("status") != "PASS" or report.get("authorizes_crew2_training") is not True
            or report.get("model_training_started") is not False
            or report.get("failed_rules") != []):
        raise ValueError("Saved validation report is not a clean, training-authorizing PASS")
    report_inputs = report.get("inputs", {})
    for key, expected in (("policy_path", policy_path), ("contract_path", contract_path),
                          ("csv_path", csv_path)):
        value = report_inputs.get(key)
        if not isinstance(value, str) or not _same_file_in_report(value, expected):
            raise ValueError(f"Validation report does not refer to this run's {key}")
    if (report.get("run_id") != run_dir.name
            or contract.get("run_id") != run_dir.name
            or report.get("policy_version") != policy.get("policy_version")
            or report.get("contract_version") != contract.get("contract_version")):
        raise ValueError("Run identity or policy/contract version mismatch")

    receipt = receipt_path(run_dir) if integrity_mode(policy) == "run_receipt" else None
    current = validate_production_handoff(policy_path, contract_path, csv_path, source_receipt=receipt)
    if (current.get("status") != "PASS" or current.get("authorizes_crew2_training") is not True
            or current.get("failed_rules") != []):
        raise ValueError("Independent production gate rejected current handoff: "
                         + json.dumps(current.get("failed_rules"), ensure_ascii=False))

    names = policy.get("model_input_columns")
    if (not isinstance(names, list) or len(names) != 15 or len(set(names)) != 15
            or set(names) & FORBIDDEN_INPUTS
            or contract.get("model_input_columns") != names):
        raise ValueError("Approved 15-column predictor allowlist is missing or unsafe")
    columns = policy.get("columns", {})
    if not isinstance(columns, dict) or not all(name in columns for name in names):
        raise ValueError("Predictor specifications missing from policy")
    types = {name: columns[name].get("type") for name in names}
    if not set(types.values()).issubset({"integer", "number", "string"}):
        raise ValueError("Unsupported approved predictor type")
    if contract.get("prepared_data", {}).get("sha256") != _sha256(csv_path):
        raise ValueError("Prepared customer dataset hash changed")
    return {"run_dir": run_dir, "policy": policy, "contract": contract, "csv_path": csv_path,
            "features": names, "types": types,
            "source_sha256": contract["prepared_data"]["sha256"]}


def create_pipeline(types: dict, kind: str, *, random_state: int = 42,
                    forest_trees: int = 100):
    """Build an *unfitted* preprocessing + estimator pipeline."""
    from sklearn.compose import ColumnTransformer
    from sklearn.dummy import DummyClassifier
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    numeric = [name for name, typ in types.items() if typ in ("integer", "number")]
    categorical = [name for name, typ in types.items() if typ == "string"]
    if not numeric and not categorical:
        raise ValueError("No approved predictors")
    if kind not in ("majority_baseline", "logistic_regression", "random_forest"):
        raise ValueError(f"Unknown estimator: {kind}")
    preprocessing = ColumnTransformer(
        transformers=[
            ("numeric", Pipeline([
                ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
                ("scaler", StandardScaler()),
            ]), numeric),
            ("category", Pipeline([
                ("imputer", SimpleImputer(strategy="most_frequent")),
                ("one_hot", OneHotEncoder(handle_unknown="ignore")),
            ]), categorical),
        ],
        remainder="drop",
    )
    estimators = {
        "majority_baseline": DummyClassifier(strategy="most_frequent"),
        "logistic_regression": LogisticRegression(max_iter=2500, random_state=random_state),
        "random_forest": RandomForestClassifier(
            n_estimators=forest_trees, min_samples_leaf=3,
            n_jobs=-1, random_state=random_state),
    }
    return Pipeline([("preprocessing", preprocessing), ("classifier", estimators[kind])])


def build_feature_data(df, names: list[str], types: dict):
    """Select only approved inputs; never include identifier, label or demographic exclusions."""
    import pandas as pd

    if (not names or len(names) != len(set(names)) or set(names) & FORBIDDEN_INPUTS
            or set(names) != set(types) or any(name not in df.columns for name in names)
            or "Churn" not in df.columns):
        raise ValueError("Unsafe or incomplete model-input allowlist")
    X = df.loc[:, names].copy()
    for name in names:
        if types[name] in ("integer", "number"):
            X[name] = pd.to_numeric(X[name], errors="raise")
        elif types[name] == "string":
            X[name] = X[name].astype("string").replace({pd.NA: None})
        else:
            raise ValueError(f"Unsupported predictor type: {types[name]}")
    y = pd.to_numeric(df["Churn"], errors="raise")
    if y.isna().any() or not y.isin([0, 1]).all():
        raise ValueError("Target contains invalid values")
    return X, y.astype(int)


def _metrics(estimator, X_test, y_test) -> dict:
    from sklearn.metrics import (accuracy_score, confusion_matrix, f1_score,
                                 precision_score, recall_score, roc_auc_score)

    predicted = estimator.predict(X_test)
    result = {
        "accuracy": round(float(accuracy_score(y_test, predicted)), 6),
        "precision": round(float(precision_score(y_test, predicted, zero_division=0)), 6),
        "recall": round(float(recall_score(y_test, predicted, zero_division=0)), 6),
        "f1": round(float(f1_score(y_test, predicted, zero_division=0)), 6),
        "confusion_matrix": confusion_matrix(y_test, predicted, labels=[0, 1]).tolist(),
        "roc_auc": None,
    }
    if len(set(y_test)) == 2 and hasattr(estimator, "predict_proba"):
        probas = estimator.predict_proba(X_test)[:, 1]
        result["roc_auc"] = round(float(roc_auc_score(y_test, probas)), 6)
    return result


def fit_models(X, y, types: dict, *, random_state: int = 42,
               test_fraction: float = 0.20, cv_folds: int = 3,
               forest_trees: int = 100) -> dict:
    """Split once, select by train-CV F1, then evaluate models on the holdout once."""
    import numpy as np
    from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split

    counts = y.value_counts()
    if len(counts) != 2 or counts.min() < 8:
        raise ValueError("Both churn classes need at least eight records")
    indexes = np.arange(len(X))
    train_idx, test_idx = train_test_split(
        indexes, test_size=test_fraction, random_state=random_state, stratify=y)
    X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
    y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]
    if y_train.value_counts().min() < cv_folds:
        raise ValueError("Insufficient minority-class training examples for CV")
    cv = StratifiedKFold(n_splits=cv_folds, shuffle=True, random_state=random_state)
    order = ("logistic_regression", "random_forest")
    cv_results = {}
    for name in order:
        model = create_pipeline(types, name, random_state=random_state, forest_trees=forest_trees)
        scores = cross_val_score(model, X_train, y_train, scoring="f1", cv=cv, n_jobs=1,
                                 error_score="raise")
        cv_results[name] = {"f1_mean": round(float(scores.mean()), 6),
                            "f1_std": round(float(scores.std()), 6),
                            "fold_scores": [round(float(z), 6) for z in scores]}
    # Deterministic tie-breaker: first in the documented candidate order.
    winner = max(order, key=lambda name: cv_results[name]["f1_mean"])
    holdout = {}
    winner_pipeline = None
    for name in ("majority_baseline",) + order:
        pipeline = create_pipeline(types, name, random_state=random_state,
                                   forest_trees=forest_trees)
        pipeline.fit(X_train, y_train)
        holdout[name] = _metrics(pipeline, X_test, y_test)
        if name == winner:
            winner_pipeline = pipeline
    return {
        "winner": winner, "pipeline": winner_pipeline,
        "cv": cv_results, "holdout": holdout,
        "train_indices": train_idx.tolist(), "test_indices": test_idx.tolist(),
        "train_positive": int(y_train.sum()), "test_positive": int(y_test.sum()),
        "seed": random_state, "test_fraction": test_fraction,
        "selection_rule": "Highest mean positive-class F1 across 3 stratified training-only CV folds",
        "test_use": "Held-out test set evaluated once, after model selection; not used to select winner",
    }


def train_run(run_dir: Path, *, forest_trees: int = 100, on_training_start=None) -> dict:
    """Train from gate-verified handoff; publish outputs only after successful completion.

    on_training_start: optional callable invoked exactly once, immediately before
    model fitting begins (after handoff re-verification and data checks pass).
    """
    import joblib
    import pandas as pd

    evidence = verify_handoff(run_dir)
    root = evidence["run_dir"]
    dest = root / "crew2"
    if dest.exists():
        raise FileExistsError(f"Crew 2 artifacts already exist, refusing overwrite: {dest}")
    data = pd.read_csv(evidence["csv_path"], low_memory=False)
    X, y = build_feature_data(data, evidence["features"], evidence["types"])
    if on_training_start is not None:
        on_training_start()
    outcome = fit_models(X, y, evidence["types"], forest_trees=forest_trees)
    staging = Path(tempfile.mkdtemp(prefix=".crew2_staging_", dir=root))
    try:
        X.to_csv(staging / "features.csv", index=False, lineterminator="\n")
        joblib.dump(outcome["pipeline"], staging / "model.joblib")
        metrics = {
            "status": "TRAINED_AND_EVALUATED_NOT_DEPLOYED",
            "run_id": root.name, "winner": outcome["winner"],
            "selection_rule": outcome["selection_rule"], "test_use": outcome["test_use"],
            "cv_training_only": outcome["cv"], "holdout_once": outcome["holdout"],
            "total_rows": len(y), "training_rows": len(outcome["train_indices"]),
            "test_rows": len(outcome["test_indices"]),
            "training_churn_count": outcome["train_positive"],
            "test_churn_count": outcome["test_positive"],
            "source_clean_data_sha256": evidence["source_sha256"],
            "approved_predictor_columns": evidence["features"],
            "excluded_inputs": sorted(FORBIDDEN_INPUTS),
            "limitations": [
                "Historical churn labels have no documented future prediction horizon",
                "Held-out metrics are descriptive research measurements, not field deployment guarantees",
                "Category aliases remain distinct; missing values are imputed only on training folds",
                "Demographic and location-tier columns excluded from predictors by approved policy",
            ],
        }
        (staging / "model_metrics.json").write_text(
            json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
        split = {"run_id": root.name, "seed": outcome["seed"],
                 "test_fraction": outcome["test_fraction"],
                 "train_row_indices": outcome["train_indices"],
                 "test_row_indices": outcome["test_indices"],
                 "split_method": "stratified train_test_split before any fitted preprocessing"}
        (staging / "split_manifest.json").write_text(
            json.dumps(split, indent=2) + "\n", encoding="utf-8")
        staging.rename(dest)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {"status": "TRAINED_AND_EVALUATED_NOT_DEPLOYED", "winner": outcome["winner"],
            "output_dir": str(dest), "models_compared": list(outcome["holdout"]),
            "train_rows": len(outcome["train_indices"]),
            "test_rows": len(outcome["test_indices"])}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Gate-verified deterministic Crew 2 modeling")
    parser.add_argument("--run-dir", required=True, type=Path,
                        help="Run root, e.g. runs/20261107T121309Z_4799c1be")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="Gate preflight without training")
    action.add_argument("--run", action="store_true", help="Train and publish local artifacts")
    args = parser.parse_args(argv)
    try:
        if args.check:
            evidence = verify_handoff(args.run_dir)
            result = {"status": "GATE_VERIFIED_READY_FOR_CREW2", "run_id": evidence["run_dir"].name,
                      "feature_count": len(evidence["features"]),
                      "model_training_started": False}
        else:
            result = train_run(args.run_dir)
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        print(json.dumps({"status": "FAIL", "model_training_started": False,
                          "error": str(exc)}, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
