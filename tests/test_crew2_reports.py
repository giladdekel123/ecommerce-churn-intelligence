"""No real customer data and no Gemini calls in these tests."""
from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from src.crew2_reports import report_texts, validate_metrics, write_reports
from src.crew2_agents import ROLES, check as check_agents


def fixture(root: Path) -> dict:
    """Generate simple, internally consistent synthetic evidence, not production input."""
    metrics = {
        "status": "TRAINED_AND_EVALUATED_NOT_DEPLOYED", "run_id": root.name,
        "winner": "random_forest",
        "selection_rule": "Highest mean positive-class F1 across 3 stratified training-only CV folds",
        "test_use": "Held-out test set evaluated once, after model selection; not used to select winner",
        "total_rows": 20, "training_rows": 16, "test_rows": 4,
        "training_churn_count": 8, "test_churn_count": 2,
        "source_clean_data_sha256": "a" * 64,
        "approved_predictor_columns": [f"Feature{i}" for i in range(15)],
        "excluded_inputs": ["Churn", "Gender", "CustomerID", "MaritalStatus", "CityTier"],
        "limitations": ["Retrospective only"],
        "cv_training_only": {
            "logistic_regression": {"f1_mean": 0.65, "f1_std": 0.04, "fold_scores": [0.60, 0.66, 0.69]},
            "random_forest": {"f1_mean": 0.75, "f1_std": 0.03, "fold_scores": [0.72, 0.76, 0.77]},
        },
        "holdout_once": {
            "majority_baseline": {
                "accuracy": 0.5, "precision": 0, "recall": 0, "f1": 0,
                "confusion_matrix": [[2, 0], [2, 0]], "roc_auc": 0.5},
            "logistic_regression": {
                "accuracy": 0.5, "precision": 0.5, "recall": 0.5, "f1": 0.5,
                "confusion_matrix": [[1, 1], [1, 1]], "roc_auc": 0.5},
            "random_forest": {
                "accuracy": 0.75, "precision": 1.0, "recall": 0.5, "f1": 0.666667,
                "confusion_matrix": [[2, 0], [1, 1]], "roc_auc": 0.75},
        },
    }
    c2 = root / "crew2"
    c2.mkdir()
    handoff = root / "handoff_candidate"
    handoff.mkdir()
    (c2 / "model_metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    (c2 / "split_manifest.json").write_text(json.dumps({
        "run_id": root.name, "train_row_indices": list(range(16)),
        "test_row_indices": list(range(16, 20))}), encoding="utf-8")
    with (c2 / "features.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(metrics["approved_predictor_columns"])
    (c2 / "model.joblib").write_bytes(b"fake-model-for-tests-only")
    (handoff / "validation_report.json").write_text(json.dumps({
        "run_id": root.name, "status": "PASS", "failed_rules": [],
        "authorizes_crew2_training": True}), encoding="utf-8")
    (handoff / "dataset_contract.json").write_text(json.dumps({
        "run_id": root.name, "model_input_columns": metrics["approved_predictor_columns"],
        "prepared_data": {"sha256": "a" * 64}}), encoding="utf-8")
    return metrics


class Crew2ReportTests(unittest.TestCase):
    def test_valid_evidence_and_report_generation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "synthetic-run"
            root.mkdir()
            fixture(root)
            self.assertEqual(validate_metrics(root)["winner"], "random_forest")
            out = write_reports(root)
            self.assertEqual(out["status"], "REPORTS_CREATED_PENDING_HUMAN_REVIEW")
            text = (root / "crew2" / "evaluation_report.md").read_text(encoding="utf-8")
            self.assertIn("detected **1** and missed **1**", text)
            self.assertIn("training-fold CV", text)
            card = (root / "crew2" / "model_card.md").read_text(encoding="utf-8")
            self.assertIn("does **not** establish fairness", card)

    def test_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "synthetic-run"
            root.mkdir()
            fixture(root)
            write_reports(root)
            with self.assertRaises(FileExistsError):
                write_reports(root)

    def test_missing_gate_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "synthetic-run"
            root.mkdir()
            fixture(root)
            (root / "handoff_candidate" / "validation_report.json").unlink()
            with self.assertRaisesRegex(ValueError, "Missing"):
                validate_metrics(root)

    def test_tampered_split_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "synthetic-run"
            root.mkdir()
            fixture(root)
            p = root / "crew2" / "split_manifest.json"
            data = json.loads(p.read_text(encoding="utf-8"))
            data["test_row_indices"][0] = 0
            p.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "overlap|inconsistent"):
                validate_metrics(root)

    def test_tampered_holdout_metric_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "synthetic-run"
            root.mkdir()
            fixture(root)
            p = root / "crew2" / "model_metrics.json"
            data = json.loads(p.read_text(encoding="utf-8"))
            data["holdout_once"]["random_forest"]["recall"] = 0.99
            p.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Inconsistent recall"):
                validate_metrics(root)

    def test_model_inputs_cannot_include_identifier(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "synthetic-run"
            root.mkdir()
            fixture(root)
            p = root / "crew2" / "model_metrics.json"
            data = json.loads(p.read_text(encoding="utf-8"))
            data["approved_predictor_columns"][0] = "CustomerID"
            p.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Predictors"):
                validate_metrics(root)

    def test_agent_preflight_is_read_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "synthetic-run"
            root.mkdir()
            fixture(root)
            result = check_agents(root)
            self.assertEqual(len(result["agents_planned"]), 3)
            self.assertFalse((root / "crew2" / "agent_reviews").exists())
            self.assertEqual(result["winner"], "random_forest")

    def test_render_from_persisted_real_metrics_without_real_customer_rows(self):
        # Tests claim formatting only; no claim that the real run gate exists here.
        candidate = Path(__file__).resolve().parents[2] / "model_metrics.json"
        if candidate.exists():
            m = json.loads(candidate.read_text(encoding="utf-8"))
            evaluation, card = report_texts(m)
            self.assertIn("detected **122** and missed **68**", evaluation)
            self.assertIn("false alarms: **10**", card)


if __name__ == "__main__":
    unittest.main()
