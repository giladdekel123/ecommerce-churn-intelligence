# 03 — Architecture and Agent Responsibility Matrix

**Status:** Proposed implementation architecture, not yet coded or proven.  
**Update (11 Nov 2026):** This planning baseline was implemented (V1) and then extended by Architecture V2: upload workflow, run-specific SHA-256 receipts, policy 2.0.0, contract drafted at the end of Crew 1, and a seven-component live monitor. The as-built behaviour is described in [`10_ARCHITECTURE_V2_IMPLEMENTATION_AND_EVIDENCE.md`](10_ARCHITECTURE_V2_IMPLEMENTATION_AND_EVIDENCE.md). The text below is kept as the original plan.

**Principle:** CrewAI agents reason within defined boundaries; deterministic Python code reads/writes verified data, calculates values and enforces the gate.

## 1. Runtime components and sequence

1. Streamlit initiates a run, creating an isolated `run_id` and recording the raw input reference/hash.
2. CrewAI Flow executes **Crew 1** on the approved source dataset and data dictionary.
3. Crew 1 generates the four named artifacts in a per-run directory.
4. A **deterministic validation gate** checks `dataset_contract.json` and `clean_data.csv` against each other **and** against a separate version-controlled approved policy.
5. If invalid: record a machine-readable failure and human-readable summary, set status `FAILED_VALIDATION`, do not invoke Crew 2.
6. If valid: Flow authorizes **Crew 2** and passes only the validated data, contract and validation proof.
7. Crew 2 creates its four named artifacts, runs model comparison and records metrics.
8. Streamlit displays the selected run's stage, logs, outputs, limitations and pass/fail behavior.

Use an explicit Flow state (`run_id`, paths, contract version, stage/status, validation outcome, timestamps, errors). Pydantic state is a recommended implementation choice, not an instructor requirement.

## 2. Crew 1 — Data Analyst Crew (minimum three agents)

### A1 — Data Quality Specialist

- **Purpose:** Assess integrity, choose/justify cleaning transformations and document anomalies.
- **Allowed input:** immutable raw Excel `E Comm`, `Data Dict`, approved cleaning rules.
- **Tool work:** Python/Pandas profiling, deterministic cleaning, category normalization only if explicitly authorized, duplicate/null/range checks.
- **Primary output:** `clean_data.csv` plus provenance in logs.
- **Prohibited:** train predictive models; silently alter authoritative policy.

### A2 — Exploratory Data Analyst

- **Purpose:** Identify descriptive patterns and select useful visualizations.
- **Allowed input:** Crew 1's cleaned dataset and computed analysis outputs.
- **Tool work:** Pandas statistics and Matplotlib/Seaborn graphics.
- **Primary output:** `eda_report.html` containing verifiable calculations and visual insights.
- **Prohibited:** invent findings or use label-informed preprocessing across evaluation splits.

### A3 — Business Insights & Contract Specialist

- **Purpose:** Explain business patterns and publish the handoff contract.
- **Allowed input:** audited cleaning record, data dictionary, verified EDA, approved contract policy.
- **Tool work:** report writer and schema publication tool.
- **Primary outputs:** `insights.md`, `dataset_contract.json`.
- **Critical restriction:** cannot silently broaden `allowed_values`, relax constraints or update the authoritative approved policy to make its own outputs pass.

## 3. Validation Gate (not an LLM agent)

- **Allowed input:** `clean_data.csv`, `dataset_contract.json`, approved policy (fixed/version-controlled), immutable run metadata.
- **Check:** contract version and policy compatibility, schema/dtypes, required fields, values/ranges, uniqueness, null policy, required model input columns, optional declared-unit constraints.
- **Output:** per-run `validation_report.json` with `PASS` or `FAIL` and explicit failed rules.
- **Authority:** only gate PASS permits Crew 2; do not treat an AI-written statement of PASS as authoritative.
- **Prohibited:** modifying input data to satisfy its own checks.

## 4. Crew 2 — Data Scientist Crew (minimum three agents)

### A4 — Feature Engineering Specialist

- **Purpose:** Identify approved predictor columns and engineer transparent features.
- **Allowed input:** gate-approved `clean_data.csv`, `dataset_contract.json`, validation proof.
- **Tool work:** deterministic data split, safe feature transformations, train-fitted preprocessing using scikit-learn Pipeline / ColumnTransformer.
- **Primary output:** `features.csv` showing permitted engineered predictors with provenance (and separately documented target/split membership where applicable).
- **Prohibited:** reading raw Excel, selecting features on the held-out test labels or fitting imputation/scaling on test observations.

### A5 — Machine Learning Engineer

- **Purpose:** Train reproducible classification model variations.
- **Allowed input:** A4-approved features, labels, training/test split metadata, modeling configuration.
- **Tool work:** majority-class baseline, logistic regression and random forest (proposed); tuning restricted to training folds.
- **Primary output:** `model.joblib` (including fitted preprocessing pipeline) and tracked model configurations.
- **Prohibited:** choosing a winner by repeatedly optimizing on the held-out test set.

### A6 — Model Evaluation Specialist

- **Purpose:** Compare results faithfully and communicate limitations.
- **Allowed input:** computed predictions, metrics and model/data provenance from approved modeling process.
- **Tool work:** scikit-learn precision, recall, F1, confusion matrix, ROC-AUC when meaningful; documentation.
- **Primary outputs:** `evaluation_report.md` and `model_card.md`.
- **Prohibited:** claiming future 30-/90-day forecasting validity or presenting invented accuracy.

## 5. Enforced access boundaries

| Component | Can access | Must not access |
|---|---|---|
| Crew 1 | Raw Excel, dictionary, approved preparation policy | Crew 2 internals or model training |
| Gate | Four Crew 1 outputs, approved policy | Unapproved contract edits or silent data fixing |
| Crew 2 | Validated handoff and gate PASS proof | Raw Excel or Crew 1 working directories |
| Streamlit | Run orchestrator, logs, status, report views | Direct override of validation decisions |

Tool scopes, filesystems and function arguments must enforce these rules—not merely the agents' prompts.

## 6. Proposed path strategy (not yet created)

- `data/raw/E Commerce Dataset.xlsx` — locally available, unchanged, **gitignored** by default.
- `contracts/approved_policy.json` — author-controlled policy versioned in Git.
- `runs/<run_id>/crew1/{clean_data.csv,eda_report.html,insights.md,dataset_contract.json}`.
- `runs/<run_id>/validation_report.json`.
- `runs/<run_id>/crew2/{features.csv,model.joblib,evaluation_report.md,model_card.md}`.
- `runs/<run_id>/run_manifest.json` — input hash, software/config versions, status and artifact paths.

Jonathan confirmed that **bulk data dumps must not be committed**. Keep `data/raw/` and the bulk files under `runs/` ignored by Git, while generating all eight required artifacts on disk. Source code, approved contract policy, report templates, tests, Markdown documentation and appropriate sample summary reports may be tracked. A small model file may be committed if appropriate; for larger models, provide a documented external link. The Kaggle dataset's declared CC BY-NC-SA 4.0 also applies to any distributed adaptations.

## 7. UX scope

A simple Streamlit app with: **(1) Run & status, (2) EDA and model reports, (3) controlled validation-failure demonstration**. A failed run's UI must never display artifacts from an earlier successful run as if they were new.
