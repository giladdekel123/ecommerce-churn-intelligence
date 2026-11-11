"""Trusted, code-defined registry of approved production policies.

Only versions listed here can ever be used. An uploaded workbook or a generated
contract may name a version, but it can only select a policy the project owner
approved and committed; it can never supply a path or new rules.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPROVED_POLICIES = {
    "1.0.0": ROOT / "contracts" / "production_policy_v1.json",  # pinned reference workbook
    "2.0.0": ROOT / "contracts" / "production_policy_v2.json",  # run-specific source receipt
}
V1_VERSION = "1.0.0"
V2_VERSION = "2.0.0"


def approved_policy_path(version) -> Path:
    """Return the committed file for a registered version; fail closed otherwise."""
    if not isinstance(version, str) or version not in APPROVED_POLICIES:
        raise ValueError(f"Policy version is not a registered approved policy: {version!r}")
    path = APPROVED_POLICIES[version]
    policy = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(policy, dict) or policy.get("policy_version") != version
            or policy.get("status") != "APPROVED" or policy.get("formally_approved") is not True):
        raise ValueError(f"Registered policy file does not match approved version {version}")
    return path


def require_registered(path) -> Path:
    """Accept an explicit policy path only if it IS one of the registered files."""
    resolved = Path(path).resolve()
    for version, registered in APPROVED_POLICIES.items():
        if resolved == registered.resolve():
            return approved_policy_path(version)
    raise ValueError("Only a registered approved production policy is permitted")
