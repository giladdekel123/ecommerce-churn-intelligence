"""Deterministic, aggregate-only EDA for an existing Crew 1 run.

Creates eda_summary.json and eda_report.html beside clean_data.csv. It does
not change source data, approve a production contract or authorize Crew 2.

Run from repository root:
    python src/crew1_eda.py --run-dir runs/<run-id>/crew1
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from io import StringIO
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _chart_svg(fig) -> str:
    buf = StringIO()
    fig.savefig(buf, format="svg", bbox_inches="tight")
    plt.close(fig)
    svg = buf.getvalue()
    return svg[svg.find("<svg"):]


def analyze(run_dir: Path) -> dict:
    """Verify existing prepared data and produce factual, aggregate reports."""
    run_dir = Path(run_dir).resolve()
    csv_path = run_dir / "clean_data.csv"
    audit_path = run_dir / "cleaning_audit.json"
    for path in (csv_path, audit_path):
        if not path.is_file():
            raise FileNotFoundError(f"Missing Crew 1 preparation artifact: {path}")
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if audit.get("status") != "PREPARED_NOT_GATE_APPROVED":
        raise ValueError("Unexpected preparation status; refusing EDA")
    if audit.get("clean_data_sha256") != _sha256(csv_path):
        raise ValueError("Clean data fingerprint mismatch; refusing EDA")
    if audit.get("authorizes_crew2_training") is not False:
        raise ValueError("Preparation audit unexpectedly authorizes Crew 2")

    frame = pd.read_csv(csv_path)
    if frame.empty or len(frame) != audit.get("row_count_after"):
        raise ValueError("Row-count mismatch with the preparation audit")
    if list(frame.columns) != audit.get("column_names"):
        raise ValueError("Column mismatch with the preparation audit")
    if frame.isna().sum().sum() != audit.get("missing_cells_retained"):
        raise ValueError("Missing-cell count mismatch with the preparation audit")
    if frame["CustomerID"].isna().any() or frame["CustomerID"].duplicated().any():
        raise ValueError("Invalid CustomerID in prepared dataset")
    if frame["Churn"].isna().any() or not frame["Churn"].isin((0, 1)).all():
        raise ValueError("Invalid Churn labels in prepared dataset")

    counts = frame["Churn"].value_counts().reindex([0, 1], fill_value=0)
    churn_rate = float(counts[1] / len(frame))
    missing = {str(k): int(v) for k, v in frame.isna().sum().items() if v > 0}
    by_device = []
    if "PreferredLoginDevice" in frame:
        group = frame.groupby("PreferredLoginDevice", dropna=False)["Churn"].agg(["size", "mean"])
        for label, row in group.iterrows():
            by_device.append({"category": str(label), "customers": int(row["size"]),
                              "historical_churn_rate": round(float(row["mean"]), 6)})
        by_device.sort(key=lambda obj: (-obj["customers"], obj["category"]))

    numeric = {}
    for field in ("Tenure", "DaySinceLastOrder", "SatisfactionScore", "CashbackAmount"):
        if field in frame:
            series = pd.to_numeric(frame[field], errors="coerce")
            numeric[field] = {
                "observed_count": int(series.notna().sum()),
                "missing_count": int(series.isna().sum()),
                "median": float(series.median()) if series.notna().any() else None,
                "mean": float(series.mean()) if series.notna().any() else None,
                "unit": "not independently verified",
            }

    summary = {
        "status": "EDA_DESCRIPTIVE_ONLY_NOT_GATE_APPROVED",
        "run_id": audit.get("run_id"),
        "clean_data_sha256": _sha256(csv_path),
        "customers": int(len(frame)),
        "column_count": int(frame.shape[1]),
        "churn_counts": {"0": int(counts[0]), "1": int(counts[1])},
        "historical_churn_rate": round(churn_rate, 8),
        "missing_cells": int(sum(missing.values())),
        "missing_by_column": missing,
        "preferred_login_device_by_churn": by_device,
        "numeric_descriptives": numeric,
        "limitations": [
            "Descriptive association is not causation.",
            "Churn is a historical flag, not a documented future-horizon outcome.",
            "Missing values were not imputed and category aliases were not normalized.",
            "Measurement units have not been independently verified.",
            "This report never authorizes Crew 2 training.",
        ],
        "authorizes_crew2_training": False,
    }

    fig, ax = plt.subplots(figsize=(5.4, 3.2))
    ax.bar(["Recorded non-churn", "Recorded churn"], [int(counts[0]), int(counts[1])])
    ax.set_ylabel("Customer count")
    ax.set_title("Historical recorded churn distribution")
    fig.tight_layout()
    churn_svg = _chart_svg(fig)

    device_svg = ""
    if by_device:
        fig, ax = plt.subplots(figsize=(6, 3.2))
        ax.bar([d["category"] for d in by_device],
               [100 * d["historical_churn_rate"] for d in by_device])
        ax.set_ylabel("Historical churn (%)")
        ax.set_title("Churn by reported login-device category (unmapped)")
        ax.tick_params(axis="x", labelrotation=20)
        fig.tight_layout()
        device_svg = _chart_svg(fig)

    table_rows = "".join(
        f"<tr><td>{html.escape(d['category'])}</td><td>{d['customers']:,}</td>"
        f"<td>{d['historical_churn_rate']:.2%}</td></tr>" for d in by_device
    )
    report = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Crew 1 — EDA — {html.escape(str(summary['run_id']))}</title>
<style>body{{font-family:system-ui,Arial;max-width:960px;margin:3rem auto;padding:0 1.5rem;line-height:1.5}}
svg{{max-width:100%;height:auto}}table{{border-collapse:collapse}}td,th{{padding:.5rem 1rem;border:1px solid #bbb;text-align:left}}
small{{color:#555}}</style></head><body>
<h1>Crew 1 — Descriptive EDA</h1>
<p><strong>Run:</strong> {html.escape(str(summary['run_id']))}. <strong>Records:</strong> {len(frame):,}.
<strong>Historical churn:</strong> {churn_rate:.2%}. <strong>Missing cells:</strong> {sum(missing.values()):,}.</p>
<section>{churn_svg}</section><section>{device_svg}</section>
<h2>Unnormalized login-device groups</h2><table><tr><th>Category</th><th>Customers</th><th>Churn rate</th></tr>
{table_rows}</table>
<h2>Interpretation restrictions</h2><ul>{''.join('<li>'+html.escape(v)+'</li>' for v in summary['limitations'])}</ul>
<p><small>All numeric calculations are computed deterministically from the current clean_data.csv, verified against the preparation audit. No category mapping, imputation or predictive model is applied.</small></p>
</body></html>"""

    summary_path = run_dir / "eda_summary.json"
    report_path = run_dir / "eda_report.html"
    if summary_path.exists() or report_path.exists():
        raise FileExistsError("EDA outputs already exist; do not overwrite an earlier report")
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(report, encoding="utf-8")
    return {"run_id": summary["run_id"], "summary_file": str(summary_path),
            "report_file": str(report_path), "customers": len(frame),
            "historical_churn_rate": summary["historical_churn_rate"],
            "authorizes_crew2_training": False}


def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministic EDA for an existing Crew 1 run")
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(analyze(args.run_dir), indent=2))


if __name__ == "__main__":
    main()
