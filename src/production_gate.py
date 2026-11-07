"""Read-only, fail-closed candidate production gate for Crew 1 -> Crew 2.

Unlike the old test-only validator, this gate is designed for the complete
version-controlled policy. It refuses unapproved policies and draft contracts.
It NEVER starts training and NEVER changes the policy, contract, or data.

This module can be installed and tested before any production policy is signed.
Command from project root:
    python src/production_gate.py --policy contracts/production_policy_proposed.json \
        --contract runs/<run-id>/crew1/dataset_contract.json \
        --csv runs/<run-id>/crew1/clean_data.csv
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

_ALLOWED_TYPES = {"integer", "number", "string"}
_GOVERNED_FIELDS = ("type", "required", "nullable", "unique", "role", "allowed_values", "min", "max")
_FORBIDDEN_PREDICTORS = {"CustomerID", "Churn", "Gender", "MaritalStatus", "CityTier"}
_MAX_SAMPLE_ROWS = 12


def _fail(rule_id: str, message: str, *, column: str | None = None,
          expected=None, observed=None, rows=None) -> dict:
    result = {"rule_id": rule_id, "message": message}
    if column is not None:
        result["column"] = column
    if rows:
        result["rows"] = list(rows)[:_MAX_SAMPLE_ROWS]
    if expected is not None:
        result["expected"] = expected
    if observed is not None:
        result["observed"] = observed
    return result


def _load_json(path: Path, label: str) -> dict:
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _normalize(value: str, dtype: str):
    if dtype == "string":
        return value
    d = Decimal(value)
    if not d.is_finite():
        raise ValueError("nonfinite number")
    if dtype == "integer":
        if d != d.to_integral_value():
            raise ValueError("not an integer")
        return int(d)
    return d


def validate_production_handoff(policy_path, contract_path, csv_path) -> dict:
    """Validate all rows and independent contract rules; return report, never mutate."""
    policy_path, contract_path, csv_path = map(Path, (policy_path, contract_path, csv_path))
    result = {
        "status": "FAIL",
        "authorizes_crew2_training": False,
        "model_training_started": False,
        "failed_rules": [],
        "inputs": {"policy_path": str(policy_path), "contract_path": str(contract_path), "csv_path": str(csv_path)},
    }
    issues = result["failed_rules"]
    try:
        policy = _load_json(policy_path, "Policy")
        contract = _load_json(contract_path, "Contract")
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        issues.append(_fail("FILE-001", "Policy or contract cannot be read as JSON", observed=str(exc)))
        return result

    result["policy_version"] = policy.get("policy_version")
    result["contract_version"] = contract.get("contract_version")
    result["run_id"] = contract.get("run_id")

    # A proposed policy or draft contract cannot self-authorize by asserting PASS.
    if (policy.get("status") != "APPROVED" or policy.get("formally_approved") is not True
            or not isinstance(policy.get("approved_by"), str) or not policy["approved_by"].strip()
            or not isinstance(policy.get("approval_date"), str) or not policy["approval_date"].strip()):
        issues.append(_fail("POL-001", "Independent policy has not been formally approved"))
    if contract.get("contract_status") != "READY_FOR_GATE_VALIDATION":
        issues.append(_fail("CTR-001", "Contract is not finalized for gate validation",
                            expected="READY_FOR_GATE_VALIDATION", observed=contract.get("contract_status")))
    if contract.get("approved_policy_version") != policy.get("policy_version"):
        issues.append(_fail("CTR-002", "Policy version mismatch", expected=policy.get("policy_version"),
                            observed=contract.get("approved_policy_version")))
    if (contract.get("identifier_column") != "CustomerID" or contract.get("target_column") != "Churn"
            or policy.get("identifier_column") != "CustomerID" or policy.get("target_column") != "Churn"):
        issues.append(_fail("CTR-003", "Identifier or target declaration mismatch"))

    required = policy.get("required_columns")
    pcols = policy.get("columns")
    ccols = contract.get("columns")
    predictors = policy.get("model_input_columns")
    if (not isinstance(required, list) or not required or len(required) != len(set(required))
            or not isinstance(pcols, dict) or set(required) != set(pcols)
            or not isinstance(ccols, dict) or set(ccols) != set(required)):
        issues.append(_fail("POL-002", "Policy/contract column specifications are incomplete or disagree"))
        return result
    if (not isinstance(predictors, list) or not predictors
            or len(predictors) != len(set(predictors))
            or not set(predictors).issubset(set(required))
            or set(predictors) & _FORBIDDEN_PREDICTORS
            or contract.get("model_input_columns") != predictors):
        issues.append(_fail("CTR-004", "Approved predictor allowlist is missing, unsafe, or inconsistent",
                            expected=predictors, observed=contract.get("model_input_columns")))
    if (policy.get("cleaning_transformation_version") != contract.get("cleaning_transformation_version")
            or not policy.get("cleaning_transformation_version")):
        issues.append(_fail("CTR-005", "Cleaning transformation version mismatch"))
    if policy.get("source_sha256") != contract.get("source_sha256") or not policy.get("source_sha256"):
        issues.append(_fail("CTR-006", "Source hash not pinned or mismatched"))

    expected_operations = {"rows_dropped": 0, "nulls_imputed": 0,
                           "category_mappings_applied": {}, "numeric_values_clipped": 0,
                           "columns_renamed": {}}
    if contract.get("cleaning_operations") != expected_operations:
        issues.append(_fail("CTR-007", "Unexpected changes to conservative cleaning operations"))
    if contract.get("category_alias_mappings") != {}:
        issues.append(_fail("CTR-008", "Unapproved category remapping in handoff"))
    if contract.get("authorizes_crew2_training") is True or contract.get("model_training_started") is True:
        issues.append(_fail("CTR-010", "Crew 1 contract cannot authorize training or claim it started"))
    if (not isinstance(policy.get("unit_assumptions"), dict)
            or contract.get("unit_assumptions") != policy.get("unit_assumptions")):
        issues.append(_fail("CTR-011", "Unit assumptions must match independently approved policy"))
    if (not isinstance(policy.get("missing_value_strategy"), str)
            or contract.get("missing_value_strategy") != policy.get("missing_value_strategy")):
        issues.append(_fail("CTR-012", "Missing-value handling differs from approved policy"))

    for name in required:
        ps, cs = pcols[name], ccols[name]
        if (not isinstance(ps, dict) or not isinstance(cs, dict)
                or ps.get("type") not in _ALLOWED_TYPES or not isinstance(ps.get("nullable"), bool)
                or ps.get("required") is not True):
            issues.append(_fail("POL-003", "Incomplete approved column rule", column=name))
            continue
        governed = {k: ps[k] for k in _GOVERNED_FIELDS if k in ps}
        observed = {k: cs.get(k) for k in governed}
        if observed != governed:
            issues.append(_fail("CTR-009", "Contract conflicts with approved column rule", column=name,
                                expected=governed, observed=observed))
        if ps["type"] == "string" and not isinstance(ps.get("allowed_values"), list):
            issues.append(_fail("POL-004", "Approved categorical values are missing", column=name))
        if name == "CustomerID" and (ps.get("nullable") is not False or ps.get("unique") is not True
                                     or ps.get("type") != "integer" or ps.get("role") != "identifier"):
            issues.append(_fail("POL-005", "CustomerID policy is unsafe", column=name))
        if name == "Churn" and (ps.get("nullable") is not False or ps.get("type") != "integer"
                                or ps.get("allowed_values") != [0, 1] or ps.get("role") != "target"):
            issues.append(_fail("POL-006", "Churn policy must allow only 0 and 1", column=name))

    try:
        observed_hash = _hash_file(csv_path)
        with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            headers = reader.fieldnames
            if headers is None:
                raise ValueError("Missing CSV header")
            if len(headers) != len(set(headers)) or headers != required:
                issues.append(_fail("CSV-001", "CSV columns must exactly match approved columns in order",
                                    expected=required, observed=headers))
            row_count = 0
            missing_count = 0
            seen_ids = set()
            # Some breaches are frequent, so return a bounded sample per rule/column.
            buckets: dict[tuple, dict] = {}

            def note(rule_id, msg, name, line, value=None):
                key = (rule_id, name)
                if key not in buckets:
                    buckets[key] = {"count": 0, "rows": [], "values": [], "message": msg}
                item = buckets[key]
                item["count"] += 1
                if len(item["rows"]) < _MAX_SAMPLE_ROWS:
                    item["rows"].append(line)
                    if value is not None:
                        item["values"].append(str(value)[:100])

            for line, row in enumerate(reader, start=2):
                row_count += 1
                if None in row:
                    note("CSV-002", "Extra CSV values beyond header", "", line)
                for name in required:
                    spec = pcols.get(name)
                    if not isinstance(spec, dict) or spec.get("type") not in _ALLOWED_TYPES:
                        continue
                    value = row.get(name)
                    if value is None or value.strip() == "":
                        missing_count += 1
                        if not spec.get("nullable", False):
                            note("CSV-003", "Missing value in non-nullable column", name, line)
                        continue
                    try:
                        parsed = _normalize(value, spec["type"])
                    except (InvalidOperation, ValueError, OverflowError):
                        note("CSV-004", "Invalid value for approved data type", name, line, value)
                        continue
                    if name == "CustomerID":
                        if parsed in seen_ids:
                            note("CID-003", "Duplicate CustomerID", name, line, parsed)
                        seen_ids.add(parsed)
                    if "allowed_values" in spec:
                        try:
                            allowed = [_normalize(str(x), spec["type"]) for x in spec["allowed_values"]]
                        except (InvalidOperation, ValueError, TypeError):
                            note("POL-007", "Invalid categorical policy values", name, line)
                            continue
                        if parsed not in allowed:
                            note("CHURN-003" if name == "Churn" else "CSV-005",
                                 "Value outside approved allowed set", name, line, value)
                    if spec["type"] != "string":
                        for bound, rule in (("min", "CSV-006"), ("max", "CSV-007")):
                            if bound in spec and spec[bound] is not None:
                                try:
                                    limit = Decimal(str(spec[bound]))
                                    exceeded = parsed < limit if bound == "min" else parsed > limit
                                except (InvalidOperation, TypeError):
                                    note("POL-008", "Invalid numeric policy bound", name, line)
                                else:
                                    if exceeded:
                                        note(rule, f"Numeric {bound} constraint violated", name, line, value)

            for (rule_id, name), item in buckets.items():
                issues.append(_fail(rule_id, f"{item['message']} ({item['count']} row(s))",
                                    column=name or None, rows=item["rows"],
                                    observed=item["values"] or None))
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        issues.append(_fail("FILE-002", "CSV cannot be read or parsed", observed=str(exc)))
        return result

    prepared = contract.get("prepared_data") if isinstance(contract.get("prepared_data"), dict) else {}
    if observed_hash != prepared.get("sha256"):
        issues.append(_fail("HASH-001", "Prepared CSV SHA-256 does not match contract",
                            expected=prepared.get("sha256"), observed=observed_hash))
    if row_count != prepared.get("rows"):
        issues.append(_fail("CSV-008", "CSV row count mismatch", expected=prepared.get("rows"), observed=row_count))
    if len(required) != prepared.get("columns"):
        issues.append(_fail("CSV-009", "Contract column count mismatch"))
    if missing_count != prepared.get("missing_cells"):
        issues.append(_fail("CSV-010", "CSV missing-cell count mismatch",
                            expected=prepared.get("missing_cells"), observed=missing_count))

    # The independently reviewed version-controlled policy, not the generated
    # contract's own authorization field, is the authority after a full PASS.
    if not issues:
        result["status"] = "PASS"
        result["authorizes_crew2_training"] = True
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fail-closed, read-only production handoff validator")
    parser.add_argument("--policy", required=True)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--csv", required=True)
    arguments = parser.parse_args(argv)
    print(json.dumps(validate_production_handoff(arguments.policy, arguments.contract, arguments.csv), indent=2))


if __name__ == "__main__":
    main()
