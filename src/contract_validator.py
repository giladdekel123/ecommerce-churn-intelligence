"""Test-only deterministic validator for the initial data-contract gate.

Compares a generated dataset_contract.json and clean_data.csv against the
independent approved policy. Never writes to those inputs. A PASS does not
authorize Crew 2 training while the approved policy is an incomplete test policy.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POLICY_PATH = REPO_ROOT / "contracts" / "approved_policy.json"
DEFAULT_CONTRACT_PATH = REPO_ROOT / "tests" / "fixtures" / "dataset_contract.json"
DEFAULT_CSV_PATH = REPO_ROOT / "tests" / "fixtures" / "valid_clean_data.csv"

GOVERNED_COLUMN_FIELDS = ("required", "nullable", "unique", "allowed_values", "role")

# Rules the approved policy must declare. The validator does not invent them.
MANDATORY_POLICY_RULES = (
    ("CID-001", "CustomerID", "column_present"),
    ("CID-002", "CustomerID", "non_null"),
    ("CID-003", "CustomerID", "unique"),
    ("CHURN-001", "Churn", "column_present"),
    ("CHURN-002", "Churn", "non_null"),
    ("CHURN-003", "Churn", "allowed_values"),
)

CREW2_BLOCK_REASON = (
    "Test-only validator and incomplete approved policy "
    "(approved_policy_version 0.1.0-test). PASS does not authorize Crew 2 training."
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


def _load_csv(path):
    if not path.is_file():
        return None, _failure("FILE-003", f"Clean data CSV not found: {path}")
    extra_key = object()
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle, restkey=extra_key)
            if reader.fieldnames is None:
                return None, _failure("PARSE-002", f"Clean data CSV has no header row: {path}")
            fieldnames = list(reader.fieldnames)
            blank_or_duplicate = (
                any(name is None or str(name).strip() == "" for name in fieldnames)
                or len(fieldnames) != len(set(fieldnames))
            )
            if blank_or_duplicate:
                return None, _failure(
                    "PARSE-003",
                    f"Clean data CSV has duplicate or blank column headers: {path}",
                    observed=fieldnames,
                )
            rows = []
            extra_field_rows = []
            for line_number, row in enumerate(reader, start=2):
                if extra_key in row:
                    extra_field_rows.append(line_number)
                    continue
                rows.append((line_number, row))
    except (OSError, csv.Error, UnicodeDecodeError) as exc:
        return None, _failure("PARSE-002", f"Clean data CSV is missing or malformed: {path} ({exc})")
    if extra_field_rows:
        return None, _failure(
            "PARSE-004",
            "Clean data CSV contains rows with more fields than the header.",
            rows=extra_field_rows,
        )
    return {"fieldnames": fieldnames, "rows": rows}, None


def _parse_allowed_values(raw):
    """Return a list of ints, or None when the policy value is missing or unusable.

    Booleans are rejected because bool is a subclass of int.
    """
    if not isinstance(raw, list) or len(raw) == 0:
        return None
    parsed = []
    for item in raw:
        if isinstance(item, bool) or not isinstance(item, int):
            return None
        parsed.append(item)
    return parsed


def _policy_integrity(policy):
    """Fail closed when mandatory policy rules or Churn allowed values are unusable.

    Returns (failures, allowed_values). allowed_values is None when the policy
    does not provide a trustworthy set; callers must not substitute defaults.
    """
    failures = []
    rules = policy.get("blocking_rules")
    found = {}
    if not isinstance(rules, list):
        failures.append(
            _failure(
                "POL-001",
                "Approved policy is missing mandatory blocking_rules.",
                observed=rules,
            )
        )
    else:
        for rule in rules:
            if isinstance(rule, dict) and isinstance(rule.get("rule_id"), str):
                found.setdefault(rule["rule_id"], rule)
        for rule_id, column, check in MANDATORY_POLICY_RULES:
            rule = found.get(rule_id)
            if not isinstance(rule, dict) or rule.get("column") != column or rule.get("check") != check:
                failures.append(
                    _failure(
                        "POL-001",
                        f"Approved policy is missing mandatory rule {rule_id}.",
                        column=column,
                        expected={"rule_id": rule_id, "column": column, "check": check},
                        observed=rule,
                    )
                )

    columns = policy.get("columns") if isinstance(policy.get("columns"), dict) else {}
    customer_spec = columns.get("CustomerID") if isinstance(columns.get("CustomerID"), dict) else None
    if customer_spec is None or any(
        customer_spec.get(key) != expected
        for key, expected in (("required", True), ("nullable", False), ("unique", True))
    ):
        failures.append(
            _failure(
                "POL-001",
                "Approved policy is missing mandatory CustomerID constraints.",
                column="CustomerID",
                expected={"required": True, "nullable": False, "unique": True},
                observed=customer_spec,
            )
        )

    churn_spec = columns.get("Churn") if isinstance(columns.get("Churn"), dict) else None
    parsed_column = None
    if churn_spec is None or churn_spec.get("required") is not True or churn_spec.get("nullable") is not False:
        failures.append(
            _failure(
                "POL-001",
                "Approved policy is missing mandatory Churn constraints.",
                column="Churn",
                expected={"required": True, "nullable": False},
                observed=churn_spec,
            )
        )
    if churn_spec is None or "allowed_values" not in churn_spec:
        failures.append(
            _failure(
                "POL-002",
                "Approved policy is missing Churn.allowed_values.",
                column="Churn",
                observed=None if churn_spec is None else churn_spec.get("allowed_values"),
            )
        )
    else:
        parsed_column = _parse_allowed_values(churn_spec.get("allowed_values"))
        if parsed_column is None:
            failures.append(
                _failure(
                    "POL-002",
                    "Approved policy Churn.allowed_values is malformed.",
                    column="Churn",
                    observed=churn_spec.get("allowed_values"),
                )
            )

    parsed_expected = None
    churn_rule = found.get("CHURN-003")
    if isinstance(churn_rule, dict):
        if "expected" not in churn_rule:
            failures.append(
                _failure(
                    "POL-002",
                    "Approved policy rule CHURN-003 is missing expected allowed values.",
                    column="Churn",
                    observed=churn_rule,
                )
            )
        else:
            parsed_expected = _parse_allowed_values(churn_rule.get("expected"))
            if parsed_expected is None:
                failures.append(
                    _failure(
                        "POL-002",
                        "Approved policy rule CHURN-003 expected allowed values are malformed.",
                        column="Churn",
                        observed=churn_rule.get("expected"),
                    )
                )

    allowed_values = None
    if parsed_column is not None and parsed_expected is not None:
        if parsed_column != parsed_expected:
            failures.append(
                _failure(
                    "POL-002",
                    "Approved policy Churn.allowed_values does not match rule CHURN-003.",
                    column="Churn",
                    expected=parsed_expected,
                    observed=parsed_column,
                )
            )
        else:
            allowed_values = parsed_column
    return failures, allowed_values


def _cell(row, column):
    value = row.get(column)
    if value is None:
        return None
    text = value.strip()
    if text == "":
        return None
    return text


def _compare_contract_to_policy(policy, contract):
    failures = []

    policy_version = policy.get("approved_policy_version")
    contract_policy_version = contract.get("approved_policy_version")
    if contract_policy_version != policy_version:
        failures.append(
            _failure(
                "CTR-001",
                "Generated contract approved_policy_version does not match the approved policy.",
                expected=policy_version,
                observed=contract_policy_version,
            )
        )

    for field, rule_id in (
        ("identifier_column", "CTR-002"),
        ("target_column", "CTR-003"),
        ("model_input_columns", "CTR-004"),
    ):
        expected = policy.get(field)
        observed = contract.get(field)
        if observed != expected:
            failures.append(
                _failure(
                    rule_id,
                    f"Generated contract {field} does not match the approved policy.",
                    column=field,
                    expected=expected,
                    observed=observed,
                )
            )

    policy_columns = policy.get("columns") if isinstance(policy.get("columns"), dict) else {}
    contract_columns = contract.get("columns") if isinstance(contract.get("columns"), dict) else {}

    for column, rule_id in (("CustomerID", "CTR-005"), ("Churn", "CTR-006")):
        expected_spec = policy_columns.get(column)
        observed_spec = contract_columns.get(column)
        if not isinstance(expected_spec, dict):
            failures.append(
                _failure(
                    rule_id,
                    f"Approved policy is missing a column specification for {column}.",
                    column=column,
                )
            )
            continue
        if not isinstance(observed_spec, dict):
            failures.append(
                _failure(
                    rule_id,
                    f"Generated contract is missing a column specification for {column}.",
                    column=column,
                    expected={key: expected_spec.get(key) for key in GOVERNED_COLUMN_FIELDS if key in expected_spec},
                    observed=observed_spec,
                )
            )
            continue
        expected = {key: expected_spec.get(key) for key in GOVERNED_COLUMN_FIELDS if key in expected_spec}
        observed = {key: observed_spec.get(key) for key in expected}
        if observed != expected:
            failures.append(
                _failure(
                    rule_id,
                    f"Generated contract specification for {column} does not match the approved policy.",
                    column=column,
                    expected=expected,
                    observed=observed,
                )
            )

    return failures


def _validate_csv_against_policy(table, allowed_values):
    failures = []
    fieldnames = table["fieldnames"]
    rows = table["rows"]

    if "CustomerID" not in fieldnames:
        failures.append(
            _failure(
                "CID-001",
                "Required column CustomerID is missing from clean_data.csv.",
                column="CustomerID",
            )
        )
    if "Churn" not in fieldnames:
        failures.append(
            _failure(
                "CHURN-001",
                "Required column Churn is missing from clean_data.csv.",
                column="Churn",
            )
        )

    if "CustomerID" in fieldnames:
        missing_id_rows = []
        ids_by_value = {}
        for line_number, row in rows:
            value = _cell(row, "CustomerID")
            if value is None:
                missing_id_rows.append(line_number)
            else:
                ids_by_value.setdefault(value, []).append(line_number)
        if missing_id_rows:
            failures.append(
                _failure(
                    "CID-002",
                    "CustomerID contains missing values.",
                    column="CustomerID",
                    rows=missing_id_rows,
                )
            )
        duplicate_rows = []
        duplicate_ids = []
        for customer_id, line_numbers in ids_by_value.items():
            if len(line_numbers) > 1:
                duplicate_rows.extend(line_numbers)
                duplicate_ids.append(customer_id)
        if duplicate_rows:
            failures.append(
                _failure(
                    "CID-003",
                    "CustomerID values are not unique.",
                    column="CustomerID",
                    rows=duplicate_rows,
                    observed=duplicate_ids,
                )
            )

    if "Churn" in fieldnames:
        missing_churn_rows = []
        illegal = []
        for line_number, row in rows:
            value = _cell(row, "Churn")
            if value is None:
                missing_churn_rows.append(line_number)
                continue
            if allowed_values is None:
                continue
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                illegal.append((line_number, value))
                continue
            if parsed not in allowed_values:
                illegal.append((line_number, parsed))
        if missing_churn_rows:
            failures.append(
                _failure(
                    "CHURN-002",
                    "Churn contains missing values.",
                    column="Churn",
                    rows=missing_churn_rows,
                )
            )
        if illegal:
            failures.append(
                _failure(
                    "CHURN-003",
                    "Churn contains values outside the approved allowed set.",
                    column="Churn",
                    rows=[item[0] for item in illegal],
                    expected=list(allowed_values),
                    observed=[item[1] for item in illegal],
                )
            )

    return failures


def validate_handoff(policy_path=None, contract_path=None, csv_path=None):
    """Validate contract and CSV against the approved policy. Does not write files."""
    policy_path = Path(policy_path) if policy_path is not None else DEFAULT_POLICY_PATH
    contract_path = Path(contract_path) if contract_path is not None else DEFAULT_CONTRACT_PATH
    csv_path = Path(csv_path) if csv_path is not None else DEFAULT_CSV_PATH

    failed_rules = []
    policy = None
    contract = None
    allowed_values = None

    policy, policy_error = _load_json(policy_path, "FILE-001", "Approved policy")
    if policy_error:
        failed_rules.append(policy_error)
    else:
        policy_failures, allowed_values = _policy_integrity(policy)
        failed_rules.extend(policy_failures)

    contract, contract_error = _load_json(contract_path, "FILE-002", "Generated dataset contract")
    if contract_error:
        failed_rules.append(contract_error)

    if policy is not None and contract is not None:
        failed_rules.extend(_compare_contract_to_policy(policy, contract))

    table, csv_error = _load_csv(csv_path)
    if csv_error:
        failed_rules.append(csv_error)
    elif policy is not None:
        failed_rules.extend(_validate_csv_against_policy(table, allowed_values))

    status = "PASS" if not failed_rules else "FAIL"
    return {
        "status": status,
        "authorizes_crew2_training": False,
        "reason_crew2_blocked": CREW2_BLOCK_REASON,
        "approved_policy_version": None if policy is None else policy.get("approved_policy_version"),
        "contract_version": None if contract is None else contract.get("contract_version"),
        "failed_rules": failed_rules,
        "inputs": {
            "policy_path": str(policy_path),
            "contract_path": str(contract_path),
            "csv_path": str(csv_path),
        },
    }


if __name__ == "__main__":
    print(json.dumps(validate_handoff(), indent=2))
