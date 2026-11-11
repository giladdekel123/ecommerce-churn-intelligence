"""Create a safe, unapproved Crew 1 dataset-contract draft from verified artifacts.

This is NOT a production validator or an authorization to run Crew 2. The
independent policy in contracts/production_policy_proposed.json also remains
unapproved. Neither may self-authorize a model training run.

Usage from project root:
    python src/crew1_contract_draft.py --run-dir runs/<run-id>/crew1
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "data" / "processed" / "source_profile.json"
POLICY = ROOT / "contracts" / "production_policy_proposed.json"

# Explicitly proposed for later review: excludes identifiers, target, and
# demographic/location attributes. This is NOT an approved feature allowlist.
PROPOSED_PREDICTORS = [
    "Tenure", "PreferredLoginDevice", "WarehouseToHome", "PreferredPaymentMode",
    "HourSpendOnApp", "NumberOfDeviceRegistered", "PreferedOrderCat",
    "SatisfactionScore", "NumberOfAddress", "Complain",
    "OrderAmountHikeFromlastYear", "CouponUsed", "OrderCount",
    "DaySinceLastOrder", "CashbackAmount",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _dtype(inferred: str) -> str:
    if inferred.startswith("int"):
        return "integer"
    if inferred.startswith("float"):
        return "number"
    return "string"


def draft(run_dir: Path, profile_path: Path = PROFILE, policy_path: Path = POLICY) -> dict:
    """Assemble draft in memory; fail closed if evidence isn't self-consistent."""
    run_dir = Path(run_dir)
    audit = load_json(run_dir / "cleaning_audit.json")
    eda = load_json(run_dir / "eda_summary.json")
    profile = load_json(profile_path)
    policy = load_json(policy_path)
    csv_path = run_dir / "clean_data.csv"
    if not csv_path.is_file():
        raise FileNotFoundError(csv_path)
    if (audit.get("status"), eda.get("status")) != (
        "PREPARED_NOT_GATE_APPROVED", "EDA_DESCRIPTIVE_ONLY_NOT_GATE_APPROVED"
    ):
        raise ValueError("Unrecognized audit or EDA status")
    if any(obj.get("authorizes_crew2_training") is not False for obj in (audit, eda, policy)):
        raise ValueError("Unexpected authorization asserted in an input")
    if policy.get("formally_approved") is not False or policy.get("status") != "PROPOSED_NOT_APPROVED":
        raise ValueError("This builder only accepts the explicitly unapproved candidate policy")
    actual_csv_hash = sha256(csv_path)
    if len({actual_csv_hash, audit.get("clean_data_sha256"), eda.get("clean_data_sha256")}) != 1:
        raise ValueError("Prepared data hash mismatch")
    if len({audit.get("source_sha256"), profile.get("source_sha256")}) != 1:
        raise ValueError("Source workbook hash mismatch")
    if (audit.get("run_id") != eda.get("run_id")
            or audit.get("run_id") != run_dir.parent.name):
        raise ValueError("Run IDs disagree or are not tied to the output directory")
    cols = audit.get("column_names")
    required_cols = policy.get("required_columns")
    if (not isinstance(cols, list) or len(cols) != len(set(cols))
            or cols != required_cols or cols != profile.get("column_names")):
        raise ValueError("Column names/order do not match independent proposed policy")
    rows = audit.get("row_count_after")
    if (rows != eda.get("customers") or rows != profile.get("rows")
            or rows != audit.get("row_count_before") or not audit.get("identifier_unique")):
        raise ValueError("Customer count or identifier integrity mismatch")
    if (audit.get("missing_cells_retained") != eda.get("missing_cells")
            or audit.get("missing_by_column") != eda.get("missing_by_column")
            or audit.get("churn_counts") != eda.get("churn_counts")):
        raise ValueError("Missing-cell or churn evidence mismatch")
    if (audit.get("rows_dropped") != 0 or audit.get("nulls_imputed") != 0
            or audit.get("category_mappings_applied") != {}
            or audit.get("numeric_values_clipped") != 0
            or audit.get("columns_renamed") != {}):
        raise ValueError("Unapproved data modification detected")
    observed_missing = audit["missing_by_column"]
    nullable_proposals = set(policy["proposed_nullable_columns"])
    column_descriptions = {}
    for col in cols:
        inferred = profile["dtypes_inferred"].get(col)
        if not inferred:
            raise ValueError(f"Missing source dtype for {col}")
        role = "identifier" if col == "CustomerID" else ("target" if col == "Churn" else "feature_candidate")
        entry = {
            "type": _dtype(inferred),
            "inferred_source_dtype": inferred,
            "required": True,
            "observed_missing_count": observed_missing.get(col, 0),
            "proposed_nullable": col in nullable_proposals,
            "role": role,
            "constraints_status": "PROPOSED_NOT_APPROVED",
        }
        if col == "CustomerID":
            entry.update({"proposed_nullable": False, "unique": True})
        elif col == "Churn":
            entry.update({"proposed_nullable": False, "allowed_values": [0, 1]})
        if col in profile.get("category_observations", {}):
            entry["observed_categories_not_approved_allowlist"] = list(profile["category_observations"][col])
        if col in profile.get("numeric_observations", {}):
            entry["observed_range_not_policy_bounds"] = profile["numeric_observations"][col]
        column_descriptions[col] = entry
    if set(PROPOSED_PREDICTORS) - set(cols):
        raise ValueError("A proposed predictor is absent")
    if set(PROPOSED_PREDICTORS) & {"CustomerID", "Churn", "Gender", "MaritalStatus", "CityTier"}:
        raise ValueError("Proposed predictors include excluded identifier, target, or sensitive fields")
    return {
        "contract_version": "0.1.0-draft",
        "approved_policy_version": None,
        "proposed_policy_version": policy["policy_version"],
        "contract_status": "DRAFT_PENDING_INDEPENDENT_POLICY_AND_ALLOWLIST_APPROVAL",
        "run_id": audit["run_id"],
        "authorizes_crew2_training": False,
        "model_training_started": False,
        "source_id": "Ankit Verma — Ecommerce Customer Churn Analysis and Prediction (Kaggle)",
        "source_file": audit["source_file"],
        "source_sheet": audit["source_sheet"],
        "source_sha256": audit["source_sha256"],
        "prepared_data": {"filename": "clean_data.csv", "sha256": actual_csv_hash,
                          "rows": rows, "columns": len(cols),
                          "missing_cells": audit["missing_cells_retained"]},
        "cleaning_transformation_version": audit["preparation_version"],
        "cleaning_operations": {
            "rows_dropped": 0, "nulls_imputed": 0, "category_mappings_applied": {},
            "numeric_values_clipped": 0, "columns_renamed": {}
        },
        "columns": column_descriptions,
        "identifier_column": "CustomerID",
        "target_column": "Churn",
        "target_allowed_values": [0, 1],
        "model_input_columns": [],
        "proposed_model_input_columns_pending_approval": PROPOSED_PREDICTORS,
        "unit_assumptions": {col: "unknown_not_independently_verified" for col in cols
                             if column_descriptions[col]["type"] in ("integer", "number")
                             and col not in ("CustomerID", "Churn", "Complain", "CityTier", "SatisfactionScore")},
        "category_alias_mappings": {},
        "category_alias_mappings_status": "UNAPPROVED_NONE_APPLIED",
        "missing_value_strategy": "Preserve in Crew 1; fit imputation inside Crew 2 training folds only",
        "known_limitations": [
            "Churn is a historical flag with no documented forward prediction horizon",
            "Some measurement units are not independently verified",
            "Category alias mapping and numeric range decisions are pending",
            "Model-input allowlist and demographic feature treatment are pending approval",
            "A generated contract cannot approve its own policy",
        ],
        "gate_status": "NOT_RUN_PRODUCTION_GATE_UNAVAILABLE",
    }


RUN_PROFILE_NAME = "source_profile.json"


def resolve_profile_path(run_dir: Path, profile_path: Path | None = None) -> Path:
    """Choose the source profile describing THIS run's workbook.

    Order: an explicit path, then the run's own crew1/source_profile.json, then the
    legacy global profile (unchanged V1 behaviour). The choice grants nothing:
    draft() still fails closed unless the profile's SHA-256 equals the run audit's.
    """
    if profile_path is not None:
        return Path(profile_path)
    per_run = Path(run_dir) / RUN_PROFILE_NAME
    return per_run if per_run.is_file() else PROFILE


def run(run_dir: Path, profile_path: Path | None = None) -> Path:
    run_dir = Path(run_dir)
    contract = draft(run_dir, resolve_profile_path(run_dir, profile_path))
    path = run_dir / "dataset_contract.json"
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing dataset contract: {path}")
    path.write_text(json.dumps(contract, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Draft the Crew 1 handoff; NEVER authorize Crew 2")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--profile", type=Path, default=None,
                        help="Source profile for this run's workbook (default: run's own, else global)")
    args = parser.parse_args()
    path = run(args.run_dir, args.profile)
    print(json.dumps({"status": "DRAFT_CREATED_NOT_GATE_APPROVED", "path": str(path),
                      "authorizes_crew2_training": False}, indent=2))


if __name__ == "__main__":
    main()
