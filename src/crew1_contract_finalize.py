"""Finalize an already reviewed Crew 1 draft against the *independent* pinned policy.

This does NOT approve policy or start training. The policy is a separate static,
version-controlled artifact reviewed by the project owner; this code only
checks it and prepares a candidate handoff in a NEW folder.

Run from project root:
  python src/crew1_contract_finalize.py --run-dir runs/<run-id>/crew1 \
      --policy contracts/production_policy_v1.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_POLICY = ROOT / "contracts" / "production_policy_v1.json"
GOVERNED_FIELDS = ("type", "required", "nullable", "unique", "role", "allowed_values", "min", "max")
EXCLUDED_PREDICTORS = {"CustomerID", "Churn", "Gender", "MaritalStatus", "CityTier"}
EXPECTED_DRAFT_STATUS = "DRAFT_PENDING_INDEPENDENT_POLICY_AND_ALLOWLIST_APPROVAL"
EXPECTED_OPERATIONS = {
    "rows_dropped": 0, "nulls_imputed": 0, "category_mappings_applied": {},
    "numeric_values_clipped": 0, "columns_renamed": {},
}


def _load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for buf in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(buf)
    return h.hexdigest()


def prepare_finalized_contract(run_dir: Path, policy_path: Path) -> dict:
    """Build candidate in memory, fail closed before writing any output."""
    run_dir, policy_path = Path(run_dir), Path(policy_path)
    policy = _load_json(policy_path)
    draft = _load_json(run_dir / "dataset_contract.json")
    audit = _load_json(run_dir / "cleaning_audit.json")
    csv_path = run_dir / "clean_data.csv"

    if (policy.get("status") != "APPROVED" or policy.get("formally_approved") is not True
        or not str(policy.get("approved_by") or "").strip()
        or not str(policy.get("approval_date") or "").strip()):
        raise ValueError("Independent policy is not explicitly approved")
    if (draft.get("contract_status") != EXPECTED_DRAFT_STATUS
        or draft.get("authorizes_crew2_training") is not False
        or draft.get("model_training_started") is not False):
        raise ValueError("Input must be the original, unapproved Crew 1 draft")
    if audit.get("status") != "PREPARED_NOT_GATE_APPROVED":
        raise ValueError("Unexpected preparation audit status")
    from src.production_gate import integrity_mode
    mode = integrity_mode(policy)
    if mode == "pinned_reference":
        # V1 behaviour, unchanged: the one pinned workbook only.
        if (draft.get("source_sha256") != policy.get("source_sha256")
            or draft.get("source_sha256") != audit.get("source_sha256")):
            raise ValueError("Frozen source-workbook SHA-256 differs")
    elif mode == "run_receipt":
        # V2: draft and audit must agree here; the independent gate alone checks
        # them against the run receipt recomputed from the stored workbook bytes.
        digest = draft.get("source_sha256")
        if not isinstance(digest, str) or len(digest) != 64 or digest != audit.get("source_sha256"):
            raise ValueError("Draft and preparation audit disagree on the source-workbook SHA-256")
    else:
        raise ValueError("Approved policy defines no valid source-integrity rule")
    actual_sha = _sha256(csv_path)
    if (actual_sha != draft.get("prepared_data", {}).get("sha256")
        or actual_sha != audit.get("clean_data_sha256")):
        raise ValueError("Prepared CSV was changed after the frozen draft/audit")
    if (draft.get("run_id") != audit.get("run_id")
        or draft.get("run_id") != run_dir.parent.name):
        raise ValueError("Run ID is not consistent")
    if (draft.get("cleaning_transformation_version") != policy.get("cleaning_transformation_version")
        or draft.get("cleaning_operations") != EXPECTED_OPERATIONS):
        raise ValueError("Unapproved cleaning operations or version")
    if draft.get("category_alias_mappings") != {}:
        raise ValueError("Alias mappings are not approved")
    if (draft.get("missing_value_strategy") != policy.get("missing_value_strategy")
        or draft.get("unit_assumptions") != policy.get("unit_assumptions")):
        raise ValueError("Unit or missing-value policy mismatch")

    required = policy.get("required_columns")
    rules = policy.get("columns")
    original_columns = draft.get("columns")
    predictors = policy.get("model_input_columns")
    if (not isinstance(required, list) or len(required) != 20 or len(set(required)) != 20
        or not isinstance(rules, dict) or set(rules) != set(required)
        or not isinstance(original_columns, dict) or list(original_columns) != required
        or not isinstance(predictors, list) or len(predictors) != 15
        or len(set(predictors)) != 15 or set(predictors) - set(required)
        or set(predictors) & EXCLUDED_PREDICTORS):
        raise ValueError("Frozen 20-column schema or 15-feature allowlist is invalid")
    if draft.get("proposed_model_input_columns_pending_approval") != predictors:
        raise ValueError("Owner-approved model features differ from original proposal")
    if (draft.get("prepared_data", {}).get("columns") != 20
        or draft.get("prepared_data", {}).get("rows") != audit.get("row_count_after")
        or draft.get("prepared_data", {}).get("missing_cells") != audit.get("missing_cells_retained")
        or draft.get("prepared_data", {}).get("filename") != "clean_data.csv"):
        raise ValueError("Prepared-data metadata does not match frozen audit")

    finalized = deepcopy(draft)
    finalized["contract_version"] = "1.0.0"
    finalized["approved_policy_version"] = policy["policy_version"]
    finalized.pop("proposed_policy_version", None)
    finalized["contract_status"] = "READY_FOR_GATE_VALIDATION"
    finalized["model_input_columns"] = list(predictors)
    finalized.pop("proposed_model_input_columns_pending_approval", None)
    finalized["category_alias_mappings_status"] = "NONE_APPLIED_RAW_LABELS_PRESERVED"
    finalized["gate_status"] = "NOT_RUN_INDEPENDENT_GATE_REQUIRED"
    finalized["known_limitations"] = [
        "Historical Churn is not a documented future-horizon outcome",
        "Measurement units remain unknown where not independently established",
        "Category synonyms remain unmerged because alias policy is not established",
        "Gender, MaritalStatus, and CityTier remain in the CSV but are excluded from model features",
        "Fitting imputation, encoding, and scaling must happen after the train/test split",
    ]
    # No self-authorization: only a new independent gate execution can release Crew 2.
    finalized["authorizes_crew2_training"] = False
    finalized["model_training_started"] = False

    for name in required:
        spec = rules[name]
        observed = finalized["columns"][name]
        if (not isinstance(spec, dict) or spec.get("type") != observed.get("type")
            or spec.get("nullable") != observed.get("proposed_nullable")
            or spec.get("required") is not True):
            raise ValueError(f"Reviewed data type/nullability conflicts with draft: {name}")
        if (name == "Churn" and spec.get("allowed_values") != [0, 1]) or (
            name == "CustomerID" and (spec.get("unique") is not True or spec.get("nullable") is not False)
        ):
            raise ValueError(f"Unsafe identifier/target policy: {name}")
        if observed.get("type") == "string":
            source_observed = observed.get("observed_categories_not_approved_allowlist", [])
            if set(source_observed) != set(spec.get("allowed_values", [])):
                raise ValueError(f"Pinned string categories differ from source evidence: {name}")
        observed.update({k: deepcopy(spec[k]) for k in GOVERNED_FIELDS if k in spec})
        observed["constraints_status"] = "APPROVED_POLICY_VERIFIED_BY_GATE_ONLY"
    return finalized


def finalize(run_dir: Path, policy_path: Path = DEFAULT_POLICY) -> dict:
    """Preserve the draft and write a new gate candidate, then validate locally."""
    run_dir = Path(run_dir)
    policy_path = Path(policy_path)
    final_contract = prepare_finalized_contract(run_dir, policy_path)
    output_dir = run_dir.parent / "handoff_candidate"
    contract_path = output_dir / "dataset_contract.json"
    report_path = output_dir / "validation_report.json"
    if contract_path.exists() or report_path.exists():
        raise FileExistsError("Candidate already exists: preserve it; do not overwrite previous gate evidence")
    output_dir.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(json.dumps(final_contract, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Imported only here: the validation logic stays independent of this builder.
    from src.production_gate import integrity_mode, validate_production_handoff
    from src.source_receipt import receipt_path
    policy = _load_json(policy_path)
    # The receipt location comes from the fixed run layout, never from the contract.
    receipt = receipt_path(run_dir.parent) if integrity_mode(policy) == "run_receipt" else None
    gate = validate_production_handoff(policy_path, contract_path, run_dir / "clean_data.csv",
                                       source_receipt=receipt)
    report_path.write_text(json.dumps(gate, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "status": gate["status"],
        "authorizes_crew2_training": gate["authorizes_crew2_training"],
        "model_training_started": False,
        "rule_ids": [x["rule_id"] for x in gate["failed_rules"]],
        "candidate_contract": str(contract_path),
        "gate_report": str(report_path),
        "original_draft_preserved": str(run_dir / "dataset_contract.json"),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Create a reviewed Crew 1 contract candidate and run independent gate")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    args = parser.parse_args(argv)
    try:
        outcome = finalize(args.run_dir, args.policy)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        outcome = {"status": "FAIL", "authorizes_crew2_training": False,
                   "model_training_started": False, "error": str(exc)}
    print(json.dumps(outcome, indent=2))
    return 0 if outcome["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
