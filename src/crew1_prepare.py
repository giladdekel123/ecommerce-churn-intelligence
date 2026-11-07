"""Conservative, deterministic Crew 1 preparation (no learned cleaning).

Reads the original 'E Comm' worksheet, validates identifier and historical label,
and emits a per-run CSV plus an aggregate audit. Does not map categories,
impute missing values, remove outliers, or authorize Crew 2.

Run from repository root:
    python src/crew1_prepare.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKBOOK = ROOT / "data" / "raw" / "E Commerce Dataset.xlsx"
PROFILE = ROOT / "data" / "processed" / "source_profile.json"
SHEET = "E Comm"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_dataframe(source: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Validate hard-approved rules; return unchanged data and aggregate audit."""
    if source.empty:
        raise ValueError("Source worksheet contains no customer rows")
    if source.columns.has_duplicates or any(not isinstance(c, str) or not c.strip() for c in source.columns):
        raise ValueError("Duplicate or blank/non-text column header")
    for name in ("CustomerID", "Churn"):
        if name not in source.columns:
            raise ValueError(f"Required column missing: {name}")
    ids = source["CustomerID"]
    if ids.isna().any():
        raise ValueError(f"CustomerID has {int(ids.isna().sum())} null values")
    if ids.duplicated().any():
        raise ValueError(f"CustomerID has {int(ids.duplicated().sum())} duplicates")
    churn = source["Churn"]
    if churn.isna().any():
        raise ValueError(f"Churn has {int(churn.isna().sum())} null values")
    if pd.api.types.is_bool_dtype(churn) or not pd.api.types.is_numeric_dtype(churn):
        raise ValueError("Churn must contain numeric historical labels 0 and 1")
    if not churn.isin([0, 1]).all():
        raise ValueError("Churn has labels outside the approved set {0,1}")

    # Copy without filling nulls, renaming columns or mapping categories.
    prepared = source.copy(deep=True)
    audit = {
        "preparation_version": "0.1.0-conservative",
        "status": "PREPARED_NOT_GATE_APPROVED",
        "authorizes_crew2_training": False,
        "model_training_started": False,
        "row_count_before": int(len(source)),
        "row_count_after": int(len(prepared)),
        "column_names": list(prepared.columns),
        "missing_cells_retained": int(prepared.isna().sum().sum()),
        "missing_by_column": {
            str(col): int(count)
            for col, count in prepared.isna().sum().items()
            if count > 0
        },
        "churn_counts": {
            str(label): int(count)
            for label, count in prepared["Churn"].value_counts(dropna=False).items()
        },
        "identifier_unique": True,
        "nulls_imputed": 0,
        "rows_dropped": 0,
        "category_mappings_applied": {},
        "numeric_values_clipped": 0,
        "columns_renamed": {},
        "pending_category_mapping_review": {
            "PreferredPaymentMode": ["CC / Credit Card", "COD / Cash on Delivery"],
            "PreferredLoginDevice": ["Phone / Mobile Phone"],
            "PreferedOrderCat": ["Mobile / Mobile Phone"],
        },
        "pending_range_review": ["WarehouseToHome"],
        "data_units": "Not inferred; consult source dictionary and approved policy",
        "next_step": (
            "Approve mappings and full production contract separately. "
            "Fit missing-value imputation only on Crew 2 training folds."
        ),
    }
    return prepared, audit


def run(source_path: Path = DEFAULT_WORKBOOK, output_dir: Path | None = None) -> dict:
    if not source_path.is_file():
        raise FileNotFoundError(f"Original workbook not found: {source_path}")
    source_hash = file_sha256(source_path)
    if PROFILE.is_file() and source_path.resolve() == DEFAULT_WORKBOOK.resolve():
        profile = json.loads(PROFILE.read_text(encoding="utf-8"))
        expected = profile.get("source_sha256")
        if expected and source_hash != expected:
            raise ValueError("Workbook fingerprint differs from source_profile.json; review source first")

    frame = pd.read_excel(source_path, sheet_name=SHEET, engine="openpyxl")
    prepared, audit = prepare_dataframe(frame)
    if output_dir is None:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
        output_dir = ROOT / "runs" / run_id / "crew1"
    else:
        run_id = output_dir.parent.name
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"Refusing to overwrite existing output directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "clean_data.csv"
    prepared.to_csv(csv_path, index=False, encoding="utf-8", lineterminator="\n")
    audit.update({
        "run_id": run_id,
        "source_file": source_path.name,
        "source_sheet": SHEET,
        "source_sha256": source_hash,
        "clean_data_sha256": file_sha256(csv_path),
        "output_file": str(csv_path.resolve()),
        "operation": "Excel-to-CSV serialization only; values, rows, columns and nulls retained",
    })
    (output_dir / "cleaning_audit.json").write_text(
        json.dumps(audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return {"run_id": run_id, "clean_data": str(csv_path),
            "audit_file": str(output_dir / "cleaning_audit.json"),
            "rows": len(prepared), "missing_retained": audit["missing_cells_retained"],
            "authorizes_crew2_training": False}


def main() -> None:
    parser = argparse.ArgumentParser(description="Conservative Crew 1 data preparation")
    parser.add_argument("--source", type=Path, default=DEFAULT_WORKBOOK)
    args = parser.parse_args()
    print(json.dumps(run(args.source), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
