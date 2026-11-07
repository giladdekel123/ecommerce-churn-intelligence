"""Read-only source profiling for the e-commerce churn course project.

Produces aggregate-only diagnostics. Never modifies the original Excel file.
Run from repository root: python src/source_profiler.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "raw" / "E Commerce Dataset.xlsx"
OUTPUT = ROOT / "data" / "processed" / "source_profile.json"
SHEET = "E Comm"

# These are candidate category fields, NOT automatically approved canonical mappings.
CATEGORY_CANDIDATES = (
    "PreferredLoginDevice", "PreferredPaymentMode", "PreferedOrderCat",
    "Gender", "MaritalStatus", "CityTier", "Complain",
    "SatisfactionScore", "Churn",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def profile(path: Path = SOURCE) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"Source workbook not found: {path}")
    workbook = pd.ExcelFile(path)
    if SHEET not in workbook.sheet_names:
        raise ValueError(f"Missing worksheet {SHEET!r}; found {workbook.sheet_names}")
    data = pd.read_excel(workbook, sheet_name=SHEET)
    columns = [str(name) for name in data.columns]
    report = {
        "source_file": path.name,
        "worksheet": SHEET,
        "source_sha256": sha256(path),
        "rows": int(len(data)),
        "columns_count": int(len(data.columns)),
        "column_names": columns,
        "dtypes_inferred": {str(c): str(data[c].dtype) for c in data.columns},
        "total_missing_cells": int(data.isna().sum().sum()),
        "missing_by_column": {
            str(c): int(n) for c, n in data.isna().sum().items() if n > 0
        },
        "identifier": {},
        "target_counts": {},
        "category_observations": {},
        "numeric_observations": {},
        "notes": [
            "Observations only: no cleaning decisions or canonical mappings are approved here.",
            "Numeric minima/maxima are observed values, not approved policy limits.",
            "Source rows and customer identifiers are deliberately omitted from this report.",
        ],
    }
    if "CustomerID" in data:
        ids = data["CustomerID"]
        report["identifier"] = {
            "null_count": int(ids.isna().sum()),
            "duplicate_count_excluding_nulls": int(ids.dropna().duplicated().sum()),
            "non_null_unique_count": int(ids.nunique(dropna=True)),
        }
    if "Churn" in data:
        counts = data["Churn"].value_counts(dropna=False)
        report["target_counts"] = {
            ("NULL" if pd.isna(value) else str(value)): int(count)
            for value, count in counts.items()
        }
    for name in CATEGORY_CANDIDATES:
        if name not in data:
            continue
        values = data[name]
        if values.nunique(dropna=True) <= 25:
            counts = values.value_counts(dropna=False)
            report["category_observations"][name] = {
                ("NULL" if pd.isna(value) else str(value)): int(count)
                for value, count in counts.items()
            }
    for name in data.select_dtypes(include="number").columns:
        if name == "CustomerID":
            continue
        values = data[name].dropna()
        if not values.empty:
            report["numeric_observations"][str(name)] = {
                "min": float(values.min()),
                "max": float(values.max()),
            }
    return report


def main() -> None:
    report = profile()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Source: {report['source_file']} / {report['worksheet']}")
    print(f"Rows: {report['rows']}; columns: {report['columns_count']}")
    print(f"Missing cells: {report['total_missing_cells']}")
    print(f"Identifier: {report['identifier']}")
    print(f"Churn counts: {report['target_counts']}")
    print(f"Aggregate report saved to: {OUTPUT.relative_to(ROOT)}")
    print("No original data was modified; no transformations were approved.")


if __name__ == "__main__":
    main()
