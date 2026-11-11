"""Run-specific SHA-256 source receipts (Architecture V2, policy 2.0.0).

Deterministic application code registers the workbook stored in a run's own
source/ folder. The receipt lives at the run root, outside Crew 1's working
folder; Crew 1, its agents and the generated contract never write it.
Verification never trusts the stored digest alone: it recomputes SHA-256 from the
file bytes every time.

Layout:  runs/<run_id>/source/<workbook>.xlsx   runs/<run_id>/source_receipt.json
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

RECEIPT_NAME = "source_receipt.json"
SOURCE_DIRNAME = "source"
RECEIPT_VERSION = "1.0"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def receipt_path(run_dir: Path) -> Path:
    """The single trusted receipt location for a run (derived from layout, not from a contract)."""
    return Path(run_dir) / RECEIPT_NAME


def create_receipt(run_dir: Path, source_file: Path, *, expected_sha256: str | None = None) -> dict:
    """Register the run's stored source workbook. Refuses overwrite and foreign paths.

    expected_sha256 is the digest computed when the upload was received; if given,
    the stored bytes must match it (proves the copy into the run folder was intact).
    """
    run_dir = Path(run_dir).resolve()
    source = Path(source_file).resolve()
    source_dir = run_dir / SOURCE_DIRNAME
    if not source.is_relative_to(source_dir) or source.parent != source_dir:
        raise ValueError(f"Source workbook must be stored directly in {source_dir}")
    if not source.is_file():
        raise FileNotFoundError(f"Stored source workbook not found: {source}")
    target = receipt_path(run_dir)
    if target.exists():
        raise FileExistsError(f"Refusing to overwrite source receipt: {target}")
    digest = sha256_file(source)
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError("Stored workbook bytes do not match the fingerprint computed at upload")
    receipt = {
        "receipt_version": RECEIPT_VERSION,
        "run_id": run_dir.name,
        "source_file": source.name,
        "source_relative_path": source.relative_to(run_dir).as_posix(),
        "source_sha256": digest,
        "source_size_bytes": source.stat().st_size,
        "registered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "registered_by": "deterministic application code (src/source_receipt.py)",
    }
    target.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def verify_receipt(run_dir: Path, receipt_file: Path | None = None) -> dict:
    """Fail closed unless the stored workbook's CURRENT bytes match its receipt."""
    run_dir = Path(run_dir).resolve()
    path = Path(receipt_file).resolve() if receipt_file is not None else receipt_path(run_dir)
    if path != receipt_path(run_dir):
        raise ValueError("Receipt must be the run's own source_receipt.json")
    if not path.is_file():
        raise FileNotFoundError(f"Run-specific source receipt is missing: {path}")
    receipt = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(receipt, dict) or receipt.get("receipt_version") != RECEIPT_VERSION:
        raise ValueError("Unrecognized source receipt format")
    if receipt.get("run_id") != run_dir.name:
        raise ValueError("Source receipt belongs to a different run")
    expected = receipt.get("source_sha256")
    if not isinstance(expected, str) or not _HEX64.fullmatch(expected):
        raise ValueError("Source receipt does not hold a valid SHA-256")
    relative = receipt.get("source_relative_path")
    source = (run_dir / str(relative)).resolve() if isinstance(relative, str) else None
    if source is None or source.parent != run_dir / SOURCE_DIRNAME:
        raise ValueError("Receipt points outside the run's source folder")
    if not source.is_file():
        raise FileNotFoundError(f"Registered source workbook is missing: {source}")
    actual = sha256_file(source)
    if actual != expected or source.stat().st_size != receipt.get("source_size_bytes"):
        raise ValueError("Stored source workbook changed after its receipt was registered")
    return {"run_id": run_dir.name, "source_sha256": actual, "source_file": source.name,
            "receipt_path": str(path)}
