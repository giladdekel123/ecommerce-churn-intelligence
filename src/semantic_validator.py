"""Test-only semantic validator for the synthetic months demonstration.

Compares an incoming CSV with the frozen reference named by the independent
semantic policy. Statistics are calculated from the CSV files. The policy's
expected_corrupted_demonstration block is not used. This module never writes
inputs or replaces the recorded reference hash. A PASS does not authorize
Crew 2 training.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POLICY_PATH = REPO_ROOT / "contracts" / "semantic_test_policy.json"
DEFAULT_CONTRACT_PATH = REPO_ROOT / "tests" / "fixtures" / "semantic" / "dataset_contract.json"

REQUIRED_COLUMNS = ("CustomerID", "Churn", "SyntheticTenureMonths")
RELATIVE_CHANGE_DEFINITION = "abs(observed - reference) / reference"
APPROVED_POLICY_VERSION = "0.1.0-semantic-test"
APPROVED_RELATIVE_CHANGE = 0.50

CREW2_BLOCK_REASON = (
    "Test-only semantic validator. The trusted reference is not formally approved, "
    "so PASS does not authorize Crew 2 training."
)


def _failure(rule_id, message, column=None, rows=None, expected=None, observed=None):
    item = {
        "rule_id": rule_id,
        "column": column,
        "rows": list(rows or []),
        "message": message,
    }
    if expected is not None:
        item["expected"] = expected
    if observed is not None:
        item["observed"] = observed
    return item


def _load_json(path, rule_id, label):
    if not path.is_file():
        return None, _failure(rule_id, f"{label} not found: {path}")
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return None, _failure(rule_id, f"{label} is missing or malformed: {path} ({exc})")
    if not isinstance(payload, dict):
        return None, _failure(rule_id, f"{label} must be a JSON object: {path}")
    return payload, None


def _resolve_repo_path(path_text):
    if not isinstance(path_text, str) or path_text.strip() == "":
        return None
    candidate = Path(path_text)
    if candidate.is_absolute():
        return candidate
    return REPO_ROOT / candidate


def _resolve_csv_argument(csv_path):
    candidate = Path(csv_path)
    if candidate.is_absolute():
        return candidate
    rooted = REPO_ROOT / candidate
    if rooted.is_file() or not candidate.is_file():
        return rooted
    return candidate


def _lookup_dotted(payload, dotted):
    if not isinstance(dotted, str) or dotted.strip() == "":
        return None, False
    current = payload
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None, False
        current = current[part]
    return current, True


def _sha256_hex(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256_hex(value):
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(character in "0123456789abcdefABCDEF" for character in value)


def _load_csv(path, rule_prefix):
    if not path.is_file():
        return None, _failure(f"{rule_prefix}-FILE", f"CSV not found: {path}")
    extra_key = object()
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle, restkey=extra_key)
            if reader.fieldnames is None:
                return None, _failure(f"{rule_prefix}-PARSE", f"CSV has no header row: {path}")
            fieldnames = list(reader.fieldnames)
            if (
                any(name is None or str(name).strip() == "" for name in fieldnames)
                or len(fieldnames) != len(set(fieldnames))
            ):
                return None, _failure(
                    f"{rule_prefix}-PARSE",
                    f"CSV has duplicate or blank column headers: {path}",
                    observed=fieldnames,
                )
            rows = []
            extra_rows = []
            for line_number, row in enumerate(reader, start=2):
                if extra_key in row:
                    extra_rows.append(line_number)
                    continue
                rows.append((line_number, row))
    except (OSError, csv.Error, UnicodeDecodeError) as exc:
        return None, _failure(f"{rule_prefix}-PARSE", f"CSV is missing or malformed: {path} ({exc})")
    if extra_rows:
        return None, _failure(
            f"{rule_prefix}-PARSE",
            "CSV contains rows with more fields than the header.",
            rows=extra_rows,
        )
    return {"fieldnames": fieldnames, "rows": rows, "path": path}, None


def _cell(row, column):
    value = row.get(column)
    if value is None:
        return None
    text = value.strip()
    if text == "":
        return None
    return text


def _parse_int(text):
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _is_finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _number(value):
    if not _is_finite_number(value):
        return None
    return value


def _display_number(value):
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def _same_number(left, right):
    left_number = _number(left)
    right_number = _number(right)
    if left_number is None or right_number is None:
        return False
    return abs(left_number - right_number) <= 1e-9


def _mean(values):
    return statistics.fmean(values)


def _median(values):
    return statistics.median(values)


def _read_policy_settings(policy):
    failures = []
    column = policy.get("column")
    if column != "SyntheticTenureMonths":
        failures.append(
            _failure(
                "POL-001",
                "Semantic policy column must be SyntheticTenureMonths.",
                column="SyntheticTenureMonths",
                expected="SyntheticTenureMonths",
                observed=column,
            )
        )

    declaration = policy.get("required_contract_unit_declaration")
    if not isinstance(declaration, dict):
        failures.append(
            _failure(
                "POL-002",
                "Semantic policy is missing required_contract_unit_declaration.",
                column="SyntheticTenureMonths",
            )
        )
        declaration = {}
    unit_field = declaration.get("field")
    expected_unit = declaration.get("expected")
    if unit_field != "unit_assumptions.SyntheticTenureMonths" or expected_unit != "months":
        failures.append(
            _failure(
                "POL-002",
                "Semantic policy must require unit_assumptions.SyntheticTenureMonths = months.",
                column="SyntheticTenureMonths",
                expected={"field": "unit_assumptions.SyntheticTenureMonths", "expected": "months"},
                observed=declaration,
            )
        )

    numeric_range = policy.get("permitted_numeric_range")
    if not isinstance(numeric_range, dict):
        numeric_range = {}
    minimum = _number(numeric_range.get("minimum"))
    maximum = _number(numeric_range.get("maximum"))
    inclusive = numeric_range.get("inclusive")
    range_ok = (
        minimum == 0
        and maximum == 120
        and inclusive is True
        and numeric_range.get("unit") == "months"
    )
    if not range_ok:
        if not _is_finite_number(numeric_range.get("minimum")) or not _is_finite_number(numeric_range.get("maximum")):
            range_message = (
                "Semantic policy permitted_numeric_range minimum or maximum is missing, "
                "nonnumeric, or non-finite."
            )
        else:
            range_message = "Semantic policy permitted range must be 0-120 months inclusive."
        failures.append(
            _failure(
                "POL-003",
                range_message,
                column="SyntheticTenureMonths",
                expected={"minimum": 0, "maximum": 120, "inclusive": True, "unit": "months"},
                observed={
                    "minimum": _display_number(numeric_range.get("minimum")),
                    "maximum": _display_number(numeric_range.get("maximum")),
                    "inclusive": inclusive,
                    "unit": numeric_range.get("unit"),
                },
            )
        )

    if policy.get("approved_unit_conversions") != [] or policy.get("no_approved_unit_conversion") is not True:
        failures.append(
            _failure(
                "POL-004",
                "Semantic policy must declare no approved unit conversion.",
                column="SyntheticTenureMonths",
            )
        )

    reference = policy.get("trusted_reference")
    if not isinstance(reference, dict):
        reference = {}
    reference_path = _resolve_repo_path(reference.get("path"))
    recorded_hash = reference.get("sha256")
    if reference_path is None or not _is_sha256_hex(recorded_hash):
        failures.append(
            _failure(
                "POL-005",
                "Semantic policy trusted reference path or SHA-256 is missing or malformed.",
                observed={"path": reference.get("path"), "sha256": recorded_hash},
            )
        )
    recorded_mean = _number(reference.get("mean"))
    recorded_median = _number(reference.get("median"))
    if recorded_mean is None or recorded_median is None:
        failures.append(
            _failure(
                "POL-005",
                "Semantic policy trusted reference mean or median is missing, nonnumeric, or non-finite.",
                column="SyntheticTenureMonths",
                observed={
                    "mean": _display_number(reference.get("mean")),
                    "median": _display_number(reference.get("median")),
                },
            )
        )

    threshold = _number(policy.get("maximum_permitted_relative_change"))
    if threshold is None:
        failures.append(
            _failure(
                "POL-006",
                "Semantic policy maximum_permitted_relative_change is missing, nonnumeric, or non-finite.",
                observed=_display_number(policy.get("maximum_permitted_relative_change")),
            )
        )
    elif policy.get("policy_version") == APPROVED_POLICY_VERSION and threshold != APPROVED_RELATIVE_CHANGE:
        failures.append(
            _failure(
                "POL-006",
                "Semantic policy maximum_permitted_relative_change must be 0.50 for policy version 0.1.0-semantic-test.",
                expected=APPROVED_RELATIVE_CHANGE,
                observed=threshold,
            )
        )
        threshold = None
    if policy.get("relative_change_definition") != RELATIVE_CHANGE_DEFINITION:
        failures.append(
            _failure(
                "POL-006",
                "Semantic policy relative-change definition is missing or unsupported.",
                expected=RELATIVE_CHANGE_DEFINITION,
                observed=policy.get("relative_change_definition"),
            )
        )

    blocking = policy.get("blocking_condition")
    if not isinstance(blocking, dict):
        blocking = {}
    if blocking.get("rule_id") != "SEM-001" or blocking.get("both_mean_and_median_must_exceed_threshold") is not True:
        failures.append(
            _failure(
                "POL-007",
                "Semantic policy blocking rule SEM-001 is missing or malformed.",
                expected={
                    "rule_id": "SEM-001",
                    "both_mean_and_median_must_exceed_threshold": True,
                },
                observed=blocking,
            )
        )
    if policy.get("semantic_failure_blocks_crew2") is not True:
        failures.append(
            _failure(
                "POL-007",
                "Semantic policy must state that a semantic failure blocks Crew 2.",
                expected=True,
                observed=policy.get("semantic_failure_blocks_crew2"),
            )
        )

    settings = {
        "unit_field": unit_field,
        "expected_unit": expected_unit,
        "minimum": minimum if range_ok else None,
        "maximum": maximum if range_ok else None,
        "range_ok": range_ok,
        "reference_path": reference_path,
        "recorded_hash": recorded_hash,
        "recorded_mean": recorded_mean,
        "recorded_median": recorded_median,
        "threshold": threshold,
        "policy_version": policy.get("policy_version"),
    }
    return settings, failures


def _check_contract(contract, settings):
    failures = []
    if contract.get("approved_policy_version") != settings["policy_version"]:
        failures.append(
            _failure(
                "CTR-001",
                "Handoff contract approved_policy_version does not match the semantic policy.",
                expected=settings["policy_version"],
                observed=contract.get("approved_policy_version"),
            )
        )
    if contract.get("authorizes_crew2_training") is not False:
        failures.append(
            _failure(
                "CTR-002",
                "Handoff contract must not authorize Crew 2 training.",
                expected=False,
                observed=contract.get("authorizes_crew2_training"),
            )
        )
    if contract.get("approved_unit_conversions") != [] or contract.get("no_approved_unit_conversion") is not True:
        failures.append(
            _failure(
                "CTR-003",
                "Handoff contract declares an approved unit conversion.",
                column="SyntheticTenureMonths",
                observed={
                    "approved_unit_conversions": contract.get("approved_unit_conversions"),
                    "no_approved_unit_conversion": contract.get("no_approved_unit_conversion"),
                },
            )
        )

    declared_unit, found = _lookup_dotted(contract, settings["unit_field"])
    if not found or declared_unit != settings["expected_unit"]:
        failures.append(
            _failure(
                "UNIT-001",
                "Handoff contract is missing unit_assumptions.SyntheticTenureMonths or it is not months.",
                column="SyntheticTenureMonths",
                expected="months",
                observed=None if not found else declared_unit,
            )
        )

    columns = contract.get("columns") if isinstance(contract.get("columns"), dict) else {}
    tenure = columns.get("SyntheticTenureMonths") if isinstance(columns.get("SyntheticTenureMonths"), dict) else {}
    declared_range = tenure.get("permitted_numeric_range")
    if tenure.get("unit") != "months" or declared_range != {
        "minimum": 0,
        "maximum": 120,
        "inclusive": True,
    }:
        failures.append(
            _failure(
                "CTR-004",
                "Handoff contract SyntheticTenureMonths unit or permitted range does not match the policy.",
                column="SyntheticTenureMonths",
                expected={"unit": "months", "minimum": 0, "maximum": 120, "inclusive": True},
                observed={"unit": tenure.get("unit"), "permitted_numeric_range": declared_range},
            )
        )

    churn = columns.get("Churn") if isinstance(columns.get("Churn"), dict) else {}
    if churn.get("allowed_values") != [0, 1]:
        failures.append(
            _failure(
                "CTR-005",
                "Handoff contract must allow only Churn values 0 and 1.",
                column="Churn",
                expected=[0, 1],
                observed=churn.get("allowed_values"),
            )
        )

    contract_version = contract.get("contract_version")
    if not isinstance(contract_version, str) or contract_version.strip() == "" or contract_version != settings["policy_version"]:
        failures.append(
            _failure(
                "CTR-006",
                "Handoff contract_version is missing or does not match the semantic policy version.",
                expected=settings["policy_version"],
                observed=contract_version,
            )
        )
    if contract.get("identifier_column") != "CustomerID":
        failures.append(
            _failure(
                "CTR-007",
                "Handoff contract identifier_column must be CustomerID.",
                column="CustomerID",
                expected="CustomerID",
                observed=contract.get("identifier_column"),
            )
        )
    if contract.get("target_column") != "Churn":
        failures.append(
            _failure(
                "CTR-008",
                "Handoff contract target_column must be Churn.",
                column="Churn",
                expected="Churn",
                observed=contract.get("target_column"),
            )
        )

    customer = columns.get("CustomerID") if isinstance(columns.get("CustomerID"), dict) else {}
    if (
        customer.get("required") is not True
        or customer.get("nullable") is not False
        or customer.get("unique") is not True
        or customer.get("role") != "identifier"
    ):
        failures.append(
            _failure(
                "CTR-009",
                "Handoff contract is missing required CustomerID constraints.",
                column="CustomerID",
                expected={"required": True, "nullable": False, "unique": True, "role": "identifier"},
                observed=customer or None,
            )
        )
    if (
        tenure.get("required") is not True
        or tenure.get("nullable") is not False
        or tenure.get("role") != "numeric"
    ):
        failures.append(
            _failure(
                "CTR-010",
                "Handoff contract is missing required SyntheticTenureMonths constraints.",
                column="SyntheticTenureMonths",
                expected={"required": True, "nullable": False, "role": "numeric"},
                observed=tenure or None,
            )
        )
    return failures


def _value_failures(table, label, minimum, maximum):
    failures = []
    if not _is_finite_number(minimum) or not _is_finite_number(maximum):
        failures.append(
            _failure(
                "POL-003",
                f"{label} CSV was not compared because the policy range is missing, nonnumeric, or non-finite.",
                column="SyntheticTenureMonths",
                observed={
                    "minimum": _display_number(minimum),
                    "maximum": _display_number(maximum),
                },
            )
        )
        return failures, None

    fieldnames = table["fieldnames"]
    missing = [column for column in REQUIRED_COLUMNS if column not in fieldnames]
    if missing or not table["rows"]:
        failures.append(
            _failure(
                f"{label}-COL",
                f"{label} CSV is missing required columns or has no data rows.",
                observed=missing or fieldnames,
            )
        )
        return failures, None

    ids_by_value = {}
    tenure_values = []
    for line_number, row in table["rows"]:
        customer_id = _cell(row, "CustomerID")
        if customer_id is None:
            failures.append(
                _failure(
                    f"{label}-ID",
                    f"{label} CSV has a missing CustomerID.",
                    column="CustomerID",
                    rows=[line_number],
                )
            )
        else:
            ids_by_value.setdefault(customer_id, []).append(line_number)

        churn_text = _cell(row, "Churn")
        churn = _parse_int(churn_text) if churn_text is not None else None
        if churn not in (0, 1):
            failures.append(
                _failure(
                    f"{label}-CHURN",
                    f"{label} CSV has a missing or illegal Churn value.",
                    column="Churn",
                    rows=[line_number],
                    expected=[0, 1],
                    observed=churn_text,
                )
            )

        tenure_text = _cell(row, "SyntheticTenureMonths")
        tenure = _parse_int(tenure_text) if tenure_text is not None else None
        if tenure is None:
            failures.append(
                _failure(
                    f"{label}-VALUE",
                    f"{label} CSV has a missing or non-integer SyntheticTenureMonths value.",
                    column="SyntheticTenureMonths",
                    rows=[line_number],
                    observed=tenure_text,
                )
            )
            continue
        if tenure < minimum or tenure > maximum:
            failures.append(
                _failure(
                    f"{label}-RANGE",
                    f"{label} CSV SyntheticTenureMonths value is outside 0-120 months.",
                    column="SyntheticTenureMonths",
                    rows=[line_number],
                    expected={"minimum": minimum, "maximum": maximum, "inclusive": True},
                    observed=tenure,
                )
            )
            continue
        tenure_values.append(tenure)

    for customer_id, line_numbers in ids_by_value.items():
        if len(line_numbers) > 1:
            failures.append(
                _failure(
                    f"{label}-ID",
                    f"{label} CSV has a duplicate CustomerID.",
                    column="CustomerID",
                    rows=line_numbers,
                    observed=customer_id,
                )
            )
    if failures:
        return failures, None
    return failures, tenure_values


def _relative_change(observed, reference):
    if reference == 0:
        return None
    return abs(observed - reference) / reference


def _result(status, failed_rules, evidence, inputs):
    return {
        "status": status,
        "authorizes_crew2_training": False,
        "model_training_started": False,
        "reason_crew2_blocked": CREW2_BLOCK_REASON,
        "failed_rules": failed_rules,
        "evidence": evidence,
        "inputs": inputs,
    }


def validate_semantic_handoff(csv_path, policy_path=None, contract_path=None):
    """Validate one synthetic handoff CSV. Does not write files or update the policy hash."""
    policy_path = Path(policy_path) if policy_path is not None else DEFAULT_POLICY_PATH
    contract_path = Path(contract_path) if contract_path is not None else DEFAULT_CONTRACT_PATH
    handoff_path = _resolve_csv_argument(csv_path)

    failed_rules = []
    evidence = {
        "declared_unit": None,
        "reference_sha256_expected": None,
        "reference_sha256_observed": None,
        "reference_hash_matches": False,
        "reference_mean": None,
        "reference_median": None,
        "handoff_mean": None,
        "handoff_median": None,
        "relative_change_mean": None,
        "relative_change_median": None,
        "threshold": None,
        "relative_change_definition": RELATIVE_CHANGE_DEFINITION,
    }
    inputs = {
        "policy_path": str(policy_path),
        "contract_path": str(contract_path),
        "csv_path": str(handoff_path),
        "reference_path": None,
    }

    policy, policy_error = _load_json(policy_path, "FILE-001", "Semantic policy")
    if policy_error:
        failed_rules.append(policy_error)
        return _result("FAIL", failed_rules, evidence, inputs)

    contract, contract_error = _load_json(contract_path, "FILE-002", "Semantic handoff contract")
    if contract_error:
        failed_rules.append(contract_error)

    settings, policy_failures = _read_policy_settings(policy)
    failed_rules.extend(policy_failures)
    evidence["threshold"] = settings["threshold"]
    evidence["reference_sha256_expected"] = settings["recorded_hash"]
    if settings["reference_path"] is not None:
        inputs["reference_path"] = str(settings["reference_path"])

    if contract is not None and not policy_failures:
        declared_unit, found = _lookup_dotted(contract, settings["unit_field"])
        evidence["declared_unit"] = declared_unit if found else None
        failed_rules.extend(_check_contract(contract, settings))

    reference_values = None
    hash_matches = False
    reference_path = settings["reference_path"]
    if reference_path is not None and _is_sha256_hex(settings["recorded_hash"]):
        if not reference_path.is_file():
            failed_rules.append(_failure("HASH-001", f"Trusted reference CSV not found: {reference_path}"))
        else:
            try:
                observed_hash = _sha256_hex(reference_path)
            except OSError as exc:
                failed_rules.append(
                    _failure("HASH-001", f"Trusted reference CSV could not be read: {reference_path} ({exc})")
                )
                observed_hash = None
            evidence["reference_sha256_observed"] = observed_hash
            if observed_hash is not None:
                hash_matches = observed_hash.lower() == settings["recorded_hash"].lower()
                evidence["reference_hash_matches"] = hash_matches
                if not hash_matches:
                    failed_rules.append(
                        _failure(
                            "HASH-001",
                            "Trusted reference CSV bytes do not match the SHA-256 recorded in the policy. The recorded hash was not changed.",
                            expected=settings["recorded_hash"],
                            observed=observed_hash,
                        )
                    )

    if hash_matches and settings["range_ok"]:
        reference_table, reference_error = _load_csv(reference_path, "REF")
        if reference_error:
            failed_rules.append(reference_error)
        else:
            value_failures, reference_values = _value_failures(
                reference_table,
                "REF",
                settings["minimum"],
                settings["maximum"],
            )
            failed_rules.extend(value_failures)

    handoff_table, handoff_error = _load_csv(handoff_path, "HANDOFF")
    handoff_values = None
    if handoff_error:
        failed_rules.append(handoff_error)
    elif settings["range_ok"]:
        value_failures, handoff_values = _value_failures(
            handoff_table,
            "HANDOFF",
            settings["minimum"],
            settings["maximum"],
        )
        failed_rules.extend(value_failures)

    if reference_values:
        evidence["reference_mean"] = _mean(reference_values)
        evidence["reference_median"] = _median(reference_values)
        if not _same_number(evidence["reference_mean"], settings["recorded_mean"]) or not _same_number(
            evidence["reference_median"], settings["recorded_median"]
        ):
            failed_rules.append(
                _failure(
                    "REF-001",
                    "Calculated reference mean or median does not match the semantic policy.",
                    column="SyntheticTenureMonths",
                    expected={"mean": settings["recorded_mean"], "median": settings["recorded_median"]},
                    observed={"mean": evidence["reference_mean"], "median": evidence["reference_median"]},
                )
            )

    if handoff_values:
        evidence["handoff_mean"] = _mean(handoff_values)
        evidence["handoff_median"] = _median(handoff_values)

    if (
        hash_matches
        and reference_values
        and handoff_values
        and _is_finite_number(settings["threshold"])
        and not any(item["rule_id"] == "REF-001" for item in failed_rules)
    ):
        mean_change = _relative_change(evidence["handoff_mean"], evidence["reference_mean"])
        median_change = _relative_change(evidence["handoff_median"], evidence["reference_median"])
        evidence["relative_change_mean"] = mean_change
        evidence["relative_change_median"] = median_change
        if mean_change is None or median_change is None:
            failed_rules.append(
                _failure(
                    "SEM-001",
                    "Relative change could not be calculated because a reference statistic is zero.",
                    column="SyntheticTenureMonths",
                    observed={
                        "reference_mean": evidence["reference_mean"],
                        "reference_median": evidence["reference_median"],
                    },
                )
            )
        elif mean_change > settings["threshold"] and median_change > settings["threshold"]:
            failed_rules.append(
                _failure(
                    "SEM-001",
                    "Both mean and median differ from the trusted reference by more than the permitted relative change.",
                    column="SyntheticTenureMonths",
                    expected={"maximum_permitted_relative_change": settings["threshold"]},
                    observed={
                        "reference_mean": evidence["reference_mean"],
                        "reference_median": evidence["reference_median"],
                        "handoff_mean": evidence["handoff_mean"],
                        "handoff_median": evidence["handoff_median"],
                        "relative_change_mean": mean_change,
                        "relative_change_median": median_change,
                        "declared_unit": evidence["declared_unit"],
                    },
                )
            )

    status = "PASS" if not failed_rules else "FAIL"
    return _result(status, failed_rules, evidence, inputs)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate a synthetic semantic handoff CSV.")
    parser.add_argument("--csv", required=True, help="Incoming CSV, such as reference_months.csv or corrupted_months.csv.")
    parser.add_argument("--policy", default=str(DEFAULT_POLICY_PATH))
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT_PATH))
    args = parser.parse_args(argv)
    report = validate_semantic_handoff(args.csv, args.policy, args.contract)
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    main()
