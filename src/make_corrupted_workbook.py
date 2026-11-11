"""Create a deliberately corrupted LOCAL copy of the E Comm workbook (Architecture V2 demo).

Changes exactly one existing SatisfactionScore value (the first one in 1..5) to 9,
which the approved policy forbids (integer 1..5). Everything else is preserved and
verified. The source workbook is never modified. The output must be in a
Git-ignored location; it is a derivative of licensed data and must never be committed.

Run from repository root:
    python src/make_corrupted_workbook.py
    python src/make_corrupted_workbook.py --source <original.xlsx> --output <ignored path.xlsx>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path

import openpyxl
import pandas as pd
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "data" / "raw" / "E Commerce Dataset.xlsx"
DEFAULT_OUTPUT = ROOT / "outputs" / "corrupted" / "E Commerce Dataset - SatisfactionScore 9.xlsx"
REFERENCE_POLICY = ROOT / "contracts" / "production_policy_v1.json"
SHEET = "E Comm"
COLUMN = "SatisfactionScore"
CORRUPT_VALUE = 9
# Inside the repository, generated workbooks may only go to these .gitignore'd folders.
IGNORED_DIRS = (ROOT / "outputs", ROOT / "runs", ROOT / "data" / "raw", ROOT / "data" / "processed")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_output_location(output: Path) -> None:
    if output.is_relative_to(ROOT) and not any(output.is_relative_to(d) for d in IGNORED_DIRS):
        raise ValueError("Output inside the repository must be under a Git-ignored folder: "
                         + ", ".join(str(d.relative_to(ROOT)) for d in IGNORED_DIRS))


def _reference_sha256() -> str | None:
    try:
        return json.loads(REFERENCE_POLICY.read_text(encoding="utf-8")).get("source_sha256")
    except (OSError, ValueError):
        return None


def _find_target_cell(source: Path) -> tuple[int, int, str]:
    """Return (Excel row, value, coordinate) of the first integer SatisfactionScore in 1..5."""
    workbook = openpyxl.load_workbook(source, read_only=True)
    try:
        if SHEET not in workbook.sheetnames:
            raise ValueError(f"Missing worksheet {SHEET!r}; found {workbook.sheetnames}")
        rows = workbook[SHEET].iter_rows(values_only=True)
        headers = list(next(rows, ()))
        if headers.count(COLUMN) != 1:
            raise ValueError(f"Worksheet must contain exactly one {COLUMN!r} header")
        index = headers.index(COLUMN)
        for excel_row, values in enumerate(rows, start=2):
            value = values[index] if index < len(values) else None
            if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 5:
                return excel_row, value, f"{get_column_letter(index + 1)}{excel_row}"
    finally:
        workbook.close()
    raise ValueError(f"No existing integer {COLUMN} value between 1 and 5 to corrupt")


def _sheet_part(archive: zipfile.ZipFile) -> str:
    """Locate the worksheet XML part for SHEET via workbook.xml and its relationships."""
    def elements(xml: str, tag: str) -> list[dict]:
        # Attribute order differs between writers (Excel vs openpyxl); parse order-free.
        return [dict(re.findall(r'([\w:]+)="([^"]*)"', attrs))
                for attrs in re.findall(rf"<{tag}\s([^>]*?)/?>", xml)]

    workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
    sheets = [e.get("r:id") for e in elements(workbook_xml, "sheet") if e.get("name") == SHEET]
    if len(sheets) != 1 or not sheets[0]:
        raise ValueError(f"Cannot locate worksheet {SHEET!r} in workbook.xml")
    rels = archive.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    targets = [e.get("Target") for e in elements(rels, "Relationship") if e.get("Id") == sheets[0]]
    if len(targets) != 1 or not targets[0]:
        raise ValueError(f"Cannot resolve the worksheet part for {SHEET!r}")
    target = targets[0].lstrip("/")
    return target if target.startswith("xl/") else "xl/" + target


def _write_patched_copy(source: Path, target: Path, coordinate: str, original_value: int) -> None:
    """Copy every package part byte-for-byte except ONE numeric cell value.

    A full openpyxl load/save would re-serialise every cell and was observed to alter
    unrelated floating-point values, so only the single <v> element is rewritten.
    """
    with zipfile.ZipFile(source) as src:
        part = _sheet_part(src)
        xml = src.read(part).decode("utf-8")
        pattern = re.compile(rf'<c r="{coordinate}"((?: s="\d+")?(?: t="n")?)><v>(-?\d+)</v></c>')
        matches = pattern.findall(xml)
        if len(matches) != 1 or int(matches[0][1]) != original_value:
            raise ValueError(f"Target cell {coordinate} is not a single plain numeric value {original_value}")
        patched = pattern.sub(lambda m: f'<c r="{coordinate}"{m.group(1)}><v>{CORRUPT_VALUE}</v></c>', xml)
        with zipfile.ZipFile(target, "w") as dst:
            for info in src.infolist():
                data = patched.encode("utf-8") if info.filename == part else src.read(info)
                dst.writestr(info, data)


def verify_single_change(source: Path, candidate: Path, excel_row: int,
                         original_value: int) -> None:
    """Fail unless the two workbooks differ in exactly the one intended cell."""
    before = pd.read_excel(source, sheet_name=None, engine="openpyxl")
    after = pd.read_excel(candidate, sheet_name=None, engine="openpyxl")
    if list(before) != list(after):
        raise ValueError("Worksheet names or order changed")
    for name in before:
        x, y = before[name], after[name]
        if list(x.columns) != list(y.columns) or x.shape != y.shape:
            raise ValueError(f"Columns or record count changed in worksheet {name!r}")
        if list(x.dtypes) != list(y.dtypes):
            raise ValueError(f"Column data types changed in worksheet {name!r}")
        same = (x == y) | (x.isna() & y.isna())
        changed = [(row, col) for col in x.columns for row in x.index[~same[col]]]
        expected = [(excel_row - 2, COLUMN)] if name == SHEET else []
        if changed != expected:
            raise ValueError(f"Unexpected cell differences in worksheet {name!r}: {len(changed)} cell(s)")
        if name == SHEET and (x.at[excel_row - 2, COLUMN] != original_value
                              or y.at[excel_row - 2, COLUMN] != CORRUPT_VALUE):
            raise ValueError("The changed cell does not hold the expected before/after values")


def corrupt_workbook(source: Path = DEFAULT_SOURCE, output: Path = DEFAULT_OUTPUT) -> dict:
    """Write the corrupted copy and a receipt; never touch the source or overwrite outputs."""
    source, output = Path(source).resolve(), Path(output).resolve()
    receipt_path = output.with_name(output.stem + ".receipt.json")
    if not source.is_file():
        raise FileNotFoundError(f"Source workbook not found: {source}")
    if output == source:
        raise ValueError("Output must not be the source workbook")
    if output.suffix.lower() != ".xlsx":
        raise ValueError("Output must be an .xlsx file")
    _check_output_location(output)
    for path in (output, receipt_path):
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite: {path}")

    source_hash = sha256(source)
    excel_row, original_value, coordinate = _find_target_cell(source)

    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(output.name + ".tmp.xlsx")
    try:
        _write_patched_copy(source, temp, coordinate, original_value)
        verify_single_change(source, temp, excel_row, original_value)
        if sha256(source) != source_hash:
            raise RuntimeError("Source workbook changed during processing")
        output_hash = sha256(temp)
        if output_hash == source_hash:
            raise ValueError("Corrupted workbook fingerprint equals the source fingerprint")
        temp.replace(output)
    finally:
        if temp.exists():
            temp.unlink()

    reference = _reference_sha256()
    receipt = {
        "status": "CORRUPTED_COPY_CREATED_LOCAL_ONLY",
        "purpose": "Architecture V2 negative test; must fail independent content validation",
        "source_file": source.name,
        "source_sha256": source_hash,
        "source_unchanged": sha256(source) == source_hash,
        "source_matches_reference_fingerprint": (source_hash == reference) if reference else None,
        "output_file": output.name,
        "output_sha256": sha256(output),
        "fingerprints_differ": True,
        "worksheet": SHEET,
        "column": COLUMN,
        "excel_row": excel_row,
        "original_value": original_value,
        "new_value": CORRUPT_VALUE,
        "cells_changed": 1,
        "do_not_commit": "Derivative of licensed customer data; keep in a Git-ignored folder",
    }
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Create a one-cell corrupted copy of the E Comm workbook")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        result = corrupt_workbook(args.source, args.output)
    except (OSError, ValueError, RuntimeError) as exc:
        result = {"status": "FAILED", "error": str(exc)}
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "CORRUPTED_COPY_CREATED_LOCAL_ONLY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
