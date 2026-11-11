# E-commerce Churn Intelligence

**A validated, multi-agent AI workflow | AI Developers Course final project**

**Status (11 November 2026):** Working local course prototype, **Architecture V2**, on branch `feature/architecture-v2` (not yet merged). Genuine runs through the Streamlit interface have produced both outcomes:
- the original workbook passed the independent gate and completed Crew 2;
- a corrupted workbook was rejected by the gate (`CSV-007`, `SatisfactionScore = 9`) with Crew 2 blocked.

177 automated tests pass. Generated agent reports require human review. **Not deployed.**
**Submission deadline:** **17 November 2026** (extended from 10 November).
**Repository:** https://github.com/giladdekel123/ecommerce-churn-intelligence

## What the project does

Two **CrewAI crews**, each with three AI agents, analyse historical e-commerce customer churn. A **deterministic, independent validation gate** sits between them: Crew 2 may train models only when Crew 1's dataset and data contract pass the approved, version-controlled policy.

The educational result is a *trustworthy handoff between AI-assisted teams*. It is not a claim that a churn model is ready for commercial use.

An operator uploads an Excel workbook in **Streamlit**, explicitly starts the analysis, and watches seven live statuses: six agents plus the gate. If the data violates the approved rules, the gate stops the run before any model is trained.

## Architecture (V2)

```text
Upload .xlsx ──► Start Analysis (consent + one click) ──► separate pipeline process
  │
  ├─ Source intake (Python): copy to runs/<id>/source/, verify SHA-256, write source_receipt.json
  ├─ Crew 1: preparation + per-run source profile + EDA (Python)
  │          → Data Quality Specialist → Exploratory Data Analyst → Business Insights & Contract Specialist (agents)
  │          → dataset_contract.json drafted (Python)
  ├─ Independent Validation Gate (deterministic Python, policy 2.0.0)
  │          receipt integrity + contract vs. policy + every CSV row vs. approved content rules
  │          FAIL ──► stop: Crew 2 BLOCKED, no training
  └─ PASS ──► Crew 2: handoff re-verified, models trained (Python)
             → Feature Engineering Specialist → Machine Learning Engineer → Model Evaluation Specialist (agents)
```

1. **Crew 1 — data preparation and analysis.** Deterministic Python does the profiling, conservative preparation (no imputation, row drops, clipping or category merging), statistics and contract drafting. The three agents review the verified evidence and write the quality review, EDA interpretation and business insights.
2. **Independent production gate.** This is deterministic Python, not an agent.
   - It validates the handoff contract and `clean_data.csv` against [`contracts/production_policy_v2.json`](contracts/production_policy_v2.json), and fails closed.
   - The policy's content rules are identical to the approved V1 policy:
     - the 20 required columns and their pinned categories;
     - `CustomerID` unique and non-null;
     - `Churn ∈ {0,1}`;
     - `SatisfactionScore` an integer from 1 to 5;
     - non-negative bounds;
     - the 15 approved predictors.
   - A generated contract cannot approve or relax itself, and an upload cannot choose the policy.
3. **Run-specific SHA-256 receipts.** Each uploaded workbook gets its own receipt. Every check recomputes the hash from the stored bytes, and the Crew 1 audit, per-run profile and contract must all match it. The original Kaggle fingerprint is kept for information only.
4. **Crew 2 — modelling and evaluation (only after an exact PASS).** Deterministic Python handles the split, training-fold-only preprocessing, model selection by training-only cross-validation, and a single held-out evaluation. The three agents review the verified results and cannot change the numbers.
5. **Live monitor.** `runs/<id>/live_status.json` records the seven components (Pending, Running, Completed, Failed, Blocked):
   - agent states come from real CrewAI task events;
   - the gate state comes from the gate result;
   - deterministic steps are listed separately, never as agents;
   - no progress percentages are invented.

**Data boundaries:** only Crew 1 reads the workbook. Crew 2 reads only the gate-approved handoff. API keys and customer data stay local and are Git-ignored.

**V1 remains available.** The pinned-workbook command line (`full_pipeline.py --check/--run`), policy 1.0.0 and historical runs are preserved and still revalidate.

Details: [docs/10_ARCHITECTURE_V2_IMPLEMENTATION_AND_EVIDENCE.md](docs/10_ARCHITECTURE_V2_IMPLEMENTATION_AND_EVIDENCE.md). Decisions: [docs/09_ARCHITECTURE_V2_APPROVAL_2026-11-11.md](docs/09_ARCHITECTURE_V2_APPROVAL_2026-11-11.md).

## Required outputs

Each run is isolated under `runs/<run_id>/`. The eight assignment-named artifacts are:

| Crew 1 | Crew 2 (gate PASS only) |
|---|---|
| `crew1/clean_data.csv` | `crew2/features.csv` |
| `crew1/eda_report.html` | `crew2/model.joblib` |
| `crew1/insights.md` | `crew2/evaluation_report.md` |
| `crew1/dataset_contract.json` | `crew2/model_card.md` |

V2 runs also save:
- `source/uploaded_workbook.xlsx` and `source_receipt.json`;
- `crew1/source_profile.json`;
- `handoff_candidate/dataset_contract.json` and `handoff_candidate/validation_report.json`;
- `run_manifest.json` and `live_status.json`;
- the agent reviews (`crew1/quality_review.md`, `crew1/eda_interpretation.md`, `crew2/agent_reviews/`).

Launch logs are written to `outputs/logs/<run_id>.log`.

**A gate PASS is permission to proceed under the approved policy.** It does not prove the data is factually correct, and it says nothing about future predictive accuracy.

## Setup (Windows / PowerShell, from the repository root)

```powershell
conda create -n ai-final-crewai python=3.12 -y
conda activate ai-final-crewai
python -m pip install -r requirements.txt
```

1. Obtain the approved `E Commerce Dataset.xlsx` (see *Source and license*) and place it at `data/raw/E Commerce Dataset.xlsx`.
2. Create a local `.env` file (Git-ignored; never commit or share a real key):

   ```dotenv
   GEMINI_API_KEY=YOUR_OWN_KEY_HERE
   CREW1_GEMINI_MODEL=gemini/gemini-3.5-flash-lite
   CREW2_GEMINI_MODEL=gemini/gemini-3.5-flash-lite
   ```

   These model IDs are the development configuration; availability depends on your provider account.
3. Run the automated tests. They need no Gemini key and make no network calls:

   ```powershell
   python -m unittest discover -s tests -p "test_*.py"
   ```

   Observed on 11 November 2026: `Ran 177 tests ... OK`.

## Operator guide

### 1. Open the dashboard

```powershell
conda activate ai-final-crewai
python -m streamlit run app.py
```

Open `http://localhost:8501`. Run only one Streamlit server at a time. The page opens clean, showing **Upload Dataset**, the consent checkbox, **Start Analysis** and *Status: Not started*. No old results appear until you ask for them.

### 2. Prepare the workbooks

- **Original:** `data/raw/E Commerce Dataset.xlsx`.
- **Corrupted (for the failure demonstration):** run

  ```powershell
  python src/make_corrupted_workbook.py
  ```

  This creates `outputs/corrupted/E Commerce Dataset - SatisfactionScore 9.xlsx`. It changes exactly one `SatisfactionScore` value to `9`, verifies that no other cell changed, never modifies the original, and writes a receipt with both fingerprints. Both files are Git-ignored and must never be committed.

### 3. Run an analysis

1. Click **Upload Dataset** and choose the workbook. The page shows its name, size and SHA-256, marked *Not started*. Uploading runs nothing.
2. Tick the acknowledgment. **Start Analysis makes Gemini API calls and may train models.**
3. Click **Start Analysis** once. The button disables itself, and other tabs or a page refresh reconnect to the same run without starting another.
4. Watch the **Live execution monitor**: Crew 1 (three agents), the Independent gate, and Crew 2 (three agents), plus the deterministic activities. The page refreshes every 2 seconds while the run is active.
5. When the run finishes, the uploaded workbook shows *Already analyzed in run …* and cannot be relaunched from the same upload. To analyse another workbook, upload it (it needs fresh consent) or click **Start a new analysis**.

### 4. Interpret the gate result

| You see | Meaning |
|---|---|
| Source integrity **VERIFIED** + fingerprint | The bytes processed are exactly the bytes uploaded. Each workbook passes its *own* check, so the original and corrupted files show different fingerprints. |
| Independent gate **PASS** | The data meets every approved rule. Crew 2 runs: three agents, model training, metrics in the *Crew 2 · Models & reviews* tab. |
| Independent gate **FAILED_VALIDATION** | A content rule failed. The findings table names the rule, column, observed value, approved limit and CSV line. For the corrupted workbook: `CSV-007 · SatisfactionScore · 9 · Approved maximum: 5`. Crew 2 is **Blocked** and model training **Not started**. |
| A component **Blocked** | It never started because an earlier step did not pass. |
| Run **INTERRUPTED** | The process ended before recording a final status. This is not a completed run. |

### 5. View past runs

Turn on **View Past Runs** in the sidebar and choose a run. Results appear only for the run you choose. Historical V1 runs show their saved results without a live monitor. Viewing never reruns or overwrites anything. The **Validation gate report** tab shows the gate report read-only.

### Command-line alternatives

```powershell
python src/full_pipeline.py --check --upload "<workbook.xlsx>"   # V2 readiness, no Gemini, writes nothing
python src/full_pipeline.py --run --upload "<workbook.xlsx>"     # V2 run (Gemini calls; may train)
python src/full_pipeline.py --check                              # V1 readiness (pinned workbook)
python src/integration_flow.py --run-dir "runs/<run_id>" --check      # re-verify a saved PASS run (read-only)
python src/integration_flow.py --run-dir "runs/<run_id>" --demo-fail  # V1 negative test on a temporary copy
```

## Observed execution evidence (11 November 2026)

| | Original workbook | Corrupted workbook |
|---|---|---|
| Run | `20261111T094219Z_1fbdc4d8` | `20261111T084920Z_0cd0bd8e` (repeated as `20261111T093641Z_db37ee1c`) |
| Receipt SHA-256 | `db70f1e3…` — VERIFIED | `1d37e8a6…` — VERIFIED |
| Gate (policy 2.0.0) | **PASS** | **FAIL — `CSV-007`**, `SatisfactionScore` observed `9`, approved maximum 5 |
| Crew 1 agents | 3 × Completed | 3 × Completed |
| Crew 2 agents | 3 × Completed | 3 × Blocked |
| Model training | Started and completed; artifacts written | Not started; no Crew 2 folder or model |
| Final status | `FULL_RUN_COMPLETE_NEEDS_HUMAN_REVIEW` | `FAILED_VALIDATION` |

Held-out results of the original run (4,504 training and 1,126 test customers; model chosen by stratified training-only cross-validation):

| Model | Accuracy | Precision (churn) | Recall (churn) | F1 (churn) | ROC-AUC |
|---|---:|---:|---:|---:|---:|
| Majority-class baseline | 83.1% | 0.0% | 0.0% | 0.0% | 0.500 |
| Logistic regression | 88.0% | 70.1% | 50.5% | 58.7% | 0.884 |
| **Random forest (selected)** | **93.1%** | **92.4%** | **64.2%** | **75.8%** | **0.981** |

For the selected random forest: **122** churners detected, **68** missed, **10** false alarms, **926** correct non-churners. Accuracy must not be confused with churn recall. These figures match the earlier V1 run because the data and the fixed seed are identical.

The approved policy uses **15 predictors**. It excludes `CustomerID`, `Gender`, `MaritalStatus` and `CityTier` from the features, and `Churn` is the target, not a predictor. Source categories are kept as they are rather than silently remapped.

## Limitations and ethical safeguards

- **What a PASS means.** It means the data meets the approved, *specified* rules and integrity checks. It does **not** mean the data is factually correct, that units are known, or that the model is production-ready.
- **Weaker provenance than V1 (accepted trade-off).** V2 accepts any workbook whose receipt verifies and whose content passes, not only the reference file.
- **Historical label.** The label records **historical churn** with no documented prediction horizon, so held-out accuracy does not establish prospective performance. Associations are not causal.
- **Advisory agents.** LLM-generated reviews are advisory; numbers and decisions come from deterministic code and approved inputs.
- **Gaps in the launch records.** They do not capture the browser session, the click and consent time, the model ID or the number of LLM requests. The run manifest lists the staged file name (`<sha256>.xlsx`); the original name is in `outputs/logs/<id>.launch.json`.
- **Operational limits.** One analysis runs at a time. A stale lock after a reboot can be cleared by deleting `runs/.active_run.lock`. On Windows, CrewAI's console listener may print `charmap` warnings that do not affect results.
- **Not deployed.** The model must not drive live customer decisions without further temporal and external validation, review and governance.

## Repository structure

```text
app.py                         Streamlit dashboard (upload, Start Analysis, live monitor, past runs)
src/full_pipeline.py           CrewAI Flow: intake → Crew 1 → independent gate → Crew 2 (V1 and V2)
src/run_launcher.py            Upload staging, single-run lock, separate-process launch, run states
src/source_receipt.py          Run-specific SHA-256 receipts (create / verify from bytes)
src/policy_registry.py         Registered approved policies (1.0.0, 2.0.0)
src/production_gate.py         Independent deterministic gate
src/live_status.py             Atomic seven-component / stage status record
src/agent_progress.py          CrewAI task events → per-agent status
src/crew1_*.py, src/crew2_*.py Crew preparation, EDA, contract, training, reports and agents
src/make_corrupted_workbook.py Local one-cell corrupted copy for the failure demonstration
src/integration_flow.py        V1 read-only re-check and --demo-fail negative test
contracts/                     Approved policies v1 (1.0.0) and v2 (2.0.0), policy history
tests/                         177 automated tests (synthetic data only)
docs/                          Requirements, architecture, decisions, approvals, V2 evidence
data/raw/, outputs/, runs/     Local data, uploads, logs and run artifacts (Git-ignored)
```

`docs/01_*` to `docs/06_*` record the original planning baseline. `docs/07_*` and `docs/08_*` record the deadline and V1 policy decisions, `docs/09_*` the V2 approval, and `docs/10_*` the V2 implementation and evidence.

## Source, license and distribution

**Dataset:** Ankit Verma, *Ecommerce Customer Churn Analysis and Prediction* — https://www.kaggle.com/datasets/ankitverma2010/ecommerce-customer-churn-analysis-and-prediction

The dataset's declared license was recorded as **Creative Commons Attribution-NonCommercial-ShareAlike 4.0 (CC BY-NC-SA 4.0)**: https://creativecommons.org/licenses/by-nc-sa/4.0/ . Observe the attribution, noncommercial and share-alike conditions as applicable.

This repository does **not** redistribute the raw workbook, the corrupted copy, uploads or bulk derivative customer files. `.gitignore` excludes credentials, `data/raw/`, `data/processed/`, `outputs/`, `runs/` and model binaries. The corrupted workbook is a local transformation of the licensed data for testing only.

## Submission and final checks

The instructor confirmed that individual submission is permitted, that real branches and Pull Requests are required, and that the presentation should be about **10 minutes**. The deadline is **17 November 2026**.

Remaining steps before submission:
- review and merge the `feature/architecture-v2` Pull Request;
- confirm a clean-clone installation;
- review the agent narratives for unsupported claims;
- rehearse the original and corrupted Streamlit demonstration.
