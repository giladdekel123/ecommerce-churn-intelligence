# E-commerce Churn Intelligence

**A validated, multi-agent AI workflow | AI Developers Course final project**

**Status (7 November 2026):** Working local course prototype. A fresh end-to-end run, a controlled validation-failure demonstration, and 86 automated tests have succeeded in the development environment. Generated agent reports require human review. **Not deployed.**
**Submission deadline:** **17 November 2026** (extension from the original 10 November deadline).
**Repository:** https://github.com/giladdekel123/ecommerce-churn-intelligence

## What the project does

This project coordinates two distinct **CrewAI crews**, each containing three AI agents, to analyze historical e-commerce customer churn. A **deterministic, independent validation gate** sits between the crews. Crew 2 is allowed to train models only when Crew 1's saved dataset and data contract pass the approved policy.

The main educational result is a *trustworthy handoff between AI-assisted teams*, not a claim that a churn model is ready for commercial deployment.

A **Streamlit** interface allows an operator to inspect saved runs, compare models, read reports, check readiness, launch a new run intentionally, and demonstrate a validation failure without altering the original data.

## Architecture and responsibilities

1. **Crew 1 - Data preparation and analysis**
   - **Data Quality Specialist:** Reviews the verified cleaning audit, missingness and quality risks.
   - **Exploratory Data Analyst:** Interprets descriptive statistics and the generated EDA evidence.
   - **Business Insights & Contract Specialist:** Explains business patterns and data-contract limitations.
   - Deterministic Python, rather than the LLM, performs source profiling, validation, data preparation, statistical calculations and contract generation.
2. **Independent production gate**
   - Validates the saved `clean_data.csv` **and** `dataset_contract.json` against `contracts/production_policy_v1.json`.
   - Checks approved schema/features, content integrity and other contractual rules; **fails closed**.
   - Prevents training when a policy or handoff does not pass. A generated contract cannot approve itself.
3. **Crew 2 - Predictive modeling and evaluation**
   - **Feature Engineering Specialist:** Reviews feature governance, provenance and leakage concerns.
   - **Machine Learning Engineer:** Reviews the training-only model comparison.
   - **Model Evaluation Specialist:** Reviews held-out metrics, limitations and interpretation.
   - Deterministic Python handles preprocessing, model selection, model training and metrics. Agents review verified evidence and cannot overwrite the canonical numerical results.
4. **CrewAI Flow + Streamlit**
   - `src/full_pipeline.py` coordinates a new Crew 1 run, independent gate, and (on PASS only) Crew 2.
   - `src/integration_flow.py` provides existing-run checks and a separate, controlled negative test.
   - `app.py` is the local Streamlit dashboard; `src/dashboard_data.py` reads saved run artifacts.

**Data boundaries:** Only Crew 1 accesses the raw Excel workbook. Crew 2 is restricted to the independently validated handoff. API keys and bulk customer data remain local.

## Required outputs

Each successful fresh run is isolated under `runs/<run_id>/`. The eight assignment-named artifacts are:

| Crew 1 | Crew 2 |
|---|---|
| `crew1/clean_data.csv` | `crew2/features.csv` |
| `crew1/eda_report.html` | `crew2/model.joblib` |
| `crew1/insights.md` | `crew2/evaluation_report.md` |
| `crew1/dataset_contract.json` | `crew2/model_card.md` |

The run also saves an independently validated handoff (`handoff_candidate/dataset_contract.json`), `handoff_candidate/validation_report.json`, a run manifest and supporting audit/evidence files. Additional Crew 1 reviews (`quality_review.md`, `eda_interpretation.md`) and Crew 2 reviews under `crew2/agent_reviews/` distinguish the six agent contributions from the canonical numerical reports.

**Important:** The initial Crew 1 contract is a draft. Only the independently checked handoff candidate may authorize Crew 2. A PASS is permission to proceed under the approved policy, **not** proof of semantic truth or future predictive accuracy.

## Quick start (Windows / PowerShell)

These commands are run **from the repository root**. The documented development environment used **Python 3.12**, Conda and a local Gemini API key.

### 1. Prepare a Python environment

```powershell
conda create -n ai-final-crewai python=3.12 -y
conda activate ai-final-crewai
python -m pip install "crewai[google-genai]" pandas scikit-learn matplotlib openpyxl python-dotenv streamlit
```

**Reproducibility limitation:** These are the principal direct dependencies, **not a lockfile or exact reproduction of all tested package versions**. Before final submission, pin verified versions and check installation from a clean clone. Provider model names and package availability can change.

### 2. Supply the source workbook and local credentials

Obtain the **same approved version** of `E Commerce Dataset.xlsx` and place it at:

```text
data/raw/E Commerce Dataset.xlsx
```

The application checks the workbook's SHA-256 against the version pinned by the approved production policy. A different workbook may be rejected even if its name matches.

Create a local `.env` file at the repository root, for example:

```dotenv
GEMINI_API_KEY=YOUR_OWN_KEY_HERE
CREW1_GEMINI_MODEL=gemini/gemini-3.5-flash-lite
CREW2_GEMINI_MODEL=gemini/gemini-3.5-flash-lite
```

The model identifiers shown are the configuration used during development; availability depends on the provider and account. **Never commit or share your real API key.** The `.env` file is ignored by Git.

### 3. Run the automated tests

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

**Observed on 7 November 2026:** `Ran 86 tests ... OK` in the project's working Windows environment. A fresh clone/environment must be tested separately.

### 4. Check readiness without calling Gemini

```powershell
python src/full_pipeline.py --check
```

Expected on the approved source and configured environment: `READY_FOR_FRESH_RUN`.

### 5. Run the full pipeline (makes API calls and trains models)

```powershell
python src/full_pipeline.py --run
```

A successful execution creates a **new** `runs/<run_id>/` directory and reports `FULL_RUN_COMPLETE_NEEDS_HUMAN_REVIEW`. It does not deploy the model or overwrite a previous successful run.

### 6. Start the Streamlit application

```powershell
python -m streamlit run app.py
```

Open `http://localhost:8501` if the browser does not open automatically. The dashboard supports **Overview**, **Crew 1 - Data & EDA**, **Crew 2 - Models & reviews**, and **Gate - Failure demonstration**. Viewing a saved run does not rerun the system. Starting a fresh run requires a separate acknowledgment because it calls Gemini and trains models.

## Safety-gate demonstration

The independent gate can be exercised without model training or Gemini calls:

```powershell
# Substitute an existing successful run ID for <run_id>
python src/integration_flow.py --run-dir "runs/<run_id>" --check
python src/integration_flow.py --run-dir "runs/<run_id>" --demo-fail
```

The first command verifies an existing approved handoff and its Crew 2 artifacts. The second intentionally corrupts a **temporary copy** of the saved handoff (not the original dataset) and should report:

```json
{
  "status": "FAILED_VALIDATION",
  "crew2_reached": false,
  "model_training_started": false
}
```

For a visual demonstration, select a saved run in Streamlit, open **Gate - Failure demonstration**, and click **Demonstrate validation failure**. The original run and model files remain unchanged.

**Known display-only issue:** On Windows, CrewAI's event logging may emit `charmap`/Unicode console-encoding warnings during the negative demo. In the observed run, the final JSON still correctly showed `FAILED_VALIDATION` and both Crew 2/training flags as `false`. This warning remains on the final-polish checklist; it is not evidence of a contract-gate bypass.

## Observed modeling results

For the recorded development evaluation of **5,630 customers**, the data was split into **4,504 training** and **1,126 held-out test** customers. Model selection used stratified, **training-only cross-validation**. Fitted imputation/encoding took place within training pipelines; the held-out set was used for final evaluation rather than model selection.

| Model | Accuracy | Precision (churn) | Recall (churn) | F1 (churn) | ROC-AUC |
|---|---:|---:|---:|---:|---:|
| Majority-class baseline | 83.1% | 0.0% | 0.0% | 0.0% | 0.500 |
| Logistic regression | 88.0% | 70.1% | 50.5% | 58.7% | 0.884 |
| **Random forest (selected)** | **93.1%** | **92.4%** | **64.2%** | **75.8%** | **0.981** |

For the selected Random Forest on that holdout: **122** true churners detected, **68** churners missed, **10** false alarms and **926** correctly identified non-churners. Accuracy must **not** be confused with churn recall. These figures document an observed course experiment; consult each run's own saved `model_metrics.json` for run-specific results.

The approved modeling policy uses **15 predictor columns** and excludes `CustomerID`, `Gender`, `MaritalStatus` and `CityTier` from features; `Churn` is the **target label**, not a predictor. The approved source categories are preserved rather than silently remapped.

## Limitations and ethical safeguards

- The label records **historical churn**. The dataset does not document a future prediction horizon, so held-out accuracy **does not establish prospective performance**.
- Behavioral associations are **not causal**; no retention intervention or business cost assumptions have been experimentally validated.
- Temporal leakage and meanings of some source fields/measurement units remain limitations. Do not invent units or deploy a model solely because the contract passed.
- The model is a **research/course prototype**. It is not deployed and must not drive live customer decisions without further temporal/external validation, review and governance.
- The gate verifies defined rules, approved policy and data integrity. It cannot certify every property of the real world.
- LLM-generated reviews are **advisory**. Numerical results and policy decisions come from deterministic code and explicitly approved inputs.

## Repository structure

```text
app.py                     Streamlit dashboard
src/                       Flow orchestration, validators, crews, modeling and reports
tests/                     Automated unit / integration / negative tests
contracts/                 Approved production policy and policy history
docs/                      Project requirements, architecture, decisions and approvals
AGENTS.md                  Coding-assistant safety and repository rules
CLAUDE.md                  Claude Code project instructions
README.md                  This guide
data/raw/                  Local licensed workbook (ignored by Git)
runs/                      Per-run datasets, models, reports and logs (ignored by Git)
.env                       Local API credentials (ignored by Git)
```

The documents in `docs/01_*` through `docs/06_*` were originally written **before implementation** and record the planning baseline. `docs/07_*` and `docs/08_*` document the later deadline/policy decisions. Historical dates and proposed status labels in older specification files should not be mistaken for the current implementation status.

## Source, license and distribution

**Dataset:** Ankit Verma, *Ecommerce Customer Churn Analysis and Prediction* — https://www.kaggle.com/datasets/ankitverma2010/ecommerce-customer-churn-analysis-and-prediction

The original dataset's declared license was recorded during the project as **Creative Commons Attribution-NonCommercial-ShareAlike 4.0 (CC BY-NC-SA 4.0)**: https://creativecommons.org/licenses/by-nc-sa/4.0/ . Observe attribution, noncommercial and share-alike conditions as applicable; this repository does **not** redistribute the raw workbook or bulk derivative customer CSV files.

`.gitignore` excludes local credentials, raw/processed data, generated run directories and model binaries. Only safe code, policies, documentation and small synthetic test fixtures should be committed. Real Git branches and pull requests were used despite solo development, as required by the instructor's clarification.

## Submission and final checks

The instructor clarified that an individual project is permitted, real branches/PRs are required, and the presentation target is **approximately 10 minutes**. The submission deadline was subsequently **extended to 17 November 2026**. The intended submission includes the public GitHub link and presentation via the specified course channel.

Before submission, the remaining checks are: confirm a clean-clone installation with pinned dependencies, inspect Git-tracked files for secrets/customer data, review the fresh-run AI narratives for unsupported claims, correct or clearly document the Windows console warning, and rehearse the valid/invalid Streamlit demo. **The application works locally, but those final verification steps are not claimed as completed here.**
