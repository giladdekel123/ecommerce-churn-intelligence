"""Single-value check of SatisfactionScore against the approved V2 policy file.

The limits are read at run time from contracts/production_policy_v2.json (the
version-controlled policy), never typed in here, so there is no second copy of the
rule to drift. The comparison mirrors the production gate's row check
(src/production_gate.py: Decimal parsing, CSV-006 for min, CSV-007 for max);
tests/test_demo_app.py runs the same values through the real gate to prove
the two agree. This is a one-rule demonstration, not the gate.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

POLICY_FILE = Path(__file__).resolve().parents[1] / "contracts" / "production_policy_v2.json"
COLUMN = "SatisfactionScore"
# Constraint keys this lab knows how to evaluate. If the policy ever adds another
# constraint to this column, the lab refuses to give a verdict rather than guess.
HANDLED_KEYS = {"type", "required", "nullable", "role", "min", "max"}
RULE_FOR_BOUND = {"min": "CSV-006", "max": "CSV-007"}  # same mapping as the production gate


def load_policy(path: Path | None = None) -> dict:
    """Read the approved policy. Raises OSError/ValueError/KeyError/TypeError on anything unusable.

    The caller must treat any exception as "lab disabled": there is deliberately no fallback rule.
    """
    policy = json.loads((path or POLICY_FILE).read_text(encoding="utf-8"))
    spec = policy["columns"][COLUMN]
    version = policy["policy_version"]
    if not isinstance(spec, dict) or not isinstance(version, str):
        raise ValueError("Policy column definition is malformed")
    for bound in ("min", "max"):
        if isinstance(spec.get(bound), bool) or not isinstance(spec.get(bound), int):
            raise ValueError(f"Policy {bound} for {COLUMN} is missing or not an integer")
    if spec["min"] > spec["max"]:
        raise ValueError("Policy min exceeds max")
    return {"policy_version": version, "spec": spec}


def unsupported_constraints(spec: dict) -> list[str]:
    return sorted(set(spec) - HANDLED_KEYS)


def default_value(spec: dict) -> int:
    """A valid starting value derived from the policy's own bounds."""
    return (int(spec["min"]) + int(spec["max"])) // 2


def evaluate(value: int, spec: dict) -> dict:
    """Return {'status': 'PASS'|'FAIL'|'UNSUPPORTED', 'rule_id', 'bound', 'limit'}."""
    if unsupported_constraints(spec):
        return {"status": "UNSUPPORTED", "rule_id": None, "bound": None, "limit": None}
    parsed = Decimal(str(value))
    for bound in ("min", "max"):
        if spec.get(bound) is None:
            continue
        limit = Decimal(str(spec[bound]))
        if (parsed < limit) if bound == "min" else (parsed > limit):
            return {"status": "FAIL", "rule_id": RULE_FOR_BOUND[bound], "bound": bound, "limit": int(limit)}
    return {"status": "PASS", "rule_id": None, "bound": None, "limit": None}
