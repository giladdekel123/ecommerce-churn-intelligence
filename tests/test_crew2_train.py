"""Synthetic-only tests for the Crew 2 modeling foundation; no real customer data."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src.crew2_train import (FORBIDDEN_INPUTS, build_feature_data, create_pipeline,
                             fit_models, train_run, verify_handoff)


class Crew2TrainingTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame({
            "CustomerID": list(range(100, 180)),
            "Churn": [i % 2 for i in range(80)],
            "Tenure": [None if i % 11 == 0 else float(i % 25) for i in range(80)],
            "Complain": [i % 2 for i in range(80)],
            "PreferredLoginDevice": ["Phone" if i % 3 else "Computer" for i in range(80)],
            "Gender": ["Female" if i % 2 else "Male" for i in range(80)],
        })
        self.names = ["Tenure", "Complain", "PreferredLoginDevice"]
        self.types = {"Tenure": "number", "Complain": "integer",
                      "PreferredLoginDevice": "string"}

    def test_allowlist_excludes_sensitive_and_identifying_fields(self):
        X, y = build_feature_data(self.frame, self.names, self.types)
        self.assertEqual(X.columns.tolist(), self.names)
        self.assertFalse(set(X.columns) & FORBIDDEN_INPUTS)
        self.assertEqual(y.sum(), 40)

    def test_forbidden_predictor_is_blocked(self):
        names = self.names + ["Gender"]
        types = dict(self.types, Gender="string")
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            build_feature_data(self.frame, names, types)

    def test_invalid_churn_is_blocked(self):
        bad = self.frame.copy()
        bad.loc[3, "Churn"] = 7
        with self.assertRaisesRegex(ValueError, "Target"):
            build_feature_data(bad, self.names, self.types)

    def test_pipeline_has_unfitted_imputation_and_onehot(self):
        X, _ = build_feature_data(self.frame, self.names, self.types)
        pipeline = create_pipeline(self.types, "logistic_regression")
        self.assertFalse(hasattr(pipeline.named_steps["preprocessing"], "transformers_"))
        self.assertEqual(pipeline.predict.__name__, "predict")
        self.assertTrue(X["Tenure"].isna().any())

    def test_cross_validation_and_holdout_split_are_disjoint(self):
        X, y = build_feature_data(self.frame, self.names, self.types)
        trained = fit_models(X, y, self.types, forest_trees=8)
        self.assertEqual(len(set(trained["train_indices"]) & set(trained["test_indices"])), 0)
        self.assertEqual(len(trained["test_indices"]), 16)
        self.assertEqual(set(trained["cv"]), {"logistic_regression", "random_forest"})
        self.assertIn(trained["winner"], trained["cv"])
        self.assertEqual(set(trained["holdout"]), {
            "majority_baseline", "logistic_regression", "random_forest"})

    def test_gate_failure_cannot_write_crew2_results(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch("src.crew2_train.verify_handoff", side_effect=ValueError("gate rejected")):
                with self.assertRaisesRegex(ValueError, "gate rejected"):
                    train_run(root, forest_trees=8)
            self.assertFalse((root / "crew2").exists())

    def test_never_overwrite_existing_model_artifacts(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "crew2").mkdir()
            (root / "crew2" / "model.joblib").write_bytes(b"old")
            evidence = {"run_dir": root, "csv_path": root / "clean_data.csv",
                        "features": self.names, "types": self.types,
                        "source_sha256": "abc"}
            with patch("src.crew2_train.verify_handoff", return_value=evidence):
                with self.assertRaises(FileExistsError):
                    train_run(root, forest_trees=8)
            self.assertEqual((root / "crew2" / "model.joblib").read_bytes(), b"old")


if __name__ == "__main__":
    unittest.main()
