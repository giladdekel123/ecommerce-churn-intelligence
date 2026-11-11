# 10 — Architecture V2: Implementation and Execution Evidence (11 November 2026)

**Status:** Implemented on branch `feature/architecture-v2` and verified locally. **Not deployed.** Not yet merged to `main`.
**Approval record:** [`09_ARCHITECTURE_V2_APPROVAL_2026-11-11.md`](09_ARCHITECTURE_V2_APPROVAL_2026-11-11.md) (decisions V2-01 to V2-09).
**Scope of this document:** what was actually built, how it behaves, and the evidence from genuine runs. It records observed results only.

## 1. What changed from V1

| Topic | V1 (preserved) | V2 (implemented) |
|---|---|---|
| Input | One pinned workbook in `data/raw/` | Any `.xlsx` with an `E Comm` worksheet, uploaded in Streamlit |
| Source integrity | Workbook SHA-256 must equal the hash pinned in `production_policy_v1.json` | Run-specific SHA-256 **receipt**, recomputed from the stored bytes at every check |
| Policy | `production_policy_v1.json` (1.0.0) | `production_policy_v2.json` (2.0.0). Content rules are identical to 1.0.0 |
| Contract drafting | Inside the gate stage | At the end of Crew 1, before the gate |
| Negative demonstration | Separate "Demonstrate validation failure" button | A real run on a corrupted workbook; the button was removed |
| Progress | Final manifest only | `live_status.json`: seven components driven by real CrewAI task events |

V1 behaviour is kept. `python src/full_pipeline.py --check/--run` and `python src/integration_flow.py --check/--demo-fail` still work. Historical V1 runs revalidate under policy 1.0.0.

## 2. Execution flow (V2)

1. **Upload (Streamlit).** The file is checked (`.xlsx` name, non-empty, at most 25 MB, Office Open XML signature) and stored as `outputs/uploads/<sha256>.xlsx`. Uploading starts nothing.
2. **Start Analysis.** This needs an accepted upload, the consent checkbox ("makes Gemini API calls and may train models") and no active run. An exclusive lock file (`runs/.active_run.lock`) allows one run at a time across all sessions.
3. **Launch.** `src/run_launcher.py` starts `src/full_pipeline.py --run --upload <file> --expected-sha256 <hash> --run-id <id>` as a separate process. It uses the current interpreter, an argument list with no shell, and a log at `outputs/logs/<run_id>.log`. The pipeline always uses the registered V2 policy; the upload cannot choose a policy.
4. **Source intake (deterministic).** The workbook is fingerprinted, copied to `runs/<id>/source/uploaded_workbook.xlsx`, checked again, and registered in `runs/<id>/source_receipt.json`.
5. **Crew 1.** Deterministic preparation writes `clean_data.csv` and a per-run `source_profile.json`, then EDA runs. The three agents review the evidence. Deterministic code then drafts `crew1/dataset_contract.json`.
6. **Independent gate (deterministic Python, not an agent).** The draft is finalised against the approved V2 policy. The gate checks the receipt (recomputed from bytes), the contract against the policy, and every CSV row against the approved content rules. Only an exact PASS releases Crew 2.
7. **Crew 2 (PASS only).** The handoff is re-verified, then models are trained (training-only CV, held-out test once) and canonical reports are written. The three agents then review the verified results.

## 3. Integrity and policy

- **Receipt chain.** The stored workbook's bytes must match the receipt, the Crew 1 audit, the per-run profile and the contract's `source_sha256`. The gate reports `SRC-001` (no receipt), `SRC-002` (bytes changed, receipt edited, wrong run, path outside `source/`) or `CTR-006` (contract hash differs).
- **Registered policies only** ([`src/policy_registry.py`](../src/policy_registry.py)): `1.0.0` maps to V1 and `2.0.0` to V2. A contract may name a version, never a file path or rules.
- **Content rules unchanged in V2.** The policy fixes all of the following:
  - the 20 required columns and their pinned categories;
  - nullability and identifier uniqueness;
  - `Churn ∈ {0,1}`;
  - `SatisfactionScore` as an integer from 1 to 5;
  - non-negative bounds;
  - the 15 approved predictors.
- **Observed ranges never become limits.** In the corrupted run, the profile observed a maximum of 9, while the handoff contract still carries the approved `max: 5`.
- **Reference fingerprint.** `db70f1e3…bd44d` is kept in the V2 policy as information only.

## 4. Seven-component monitor

`runs/<id>/live_status.json` is written atomically. It records:

| Component | Source of its state |
|---|---|
| Data Quality Specialist, Exploratory Data Analyst, Business Insights & Contract Specialist | CrewAI `TaskStartedEvent` / `TaskCompletedEvent` / `TaskFailedEvent` |
| Independent Validation Gate | The gate result (`PASS`, or `FAIL: <rule IDs>`) |
| Feature Engineering Specialist, Machine Learning Engineer, Model Evaluation Specialist | CrewAI task events; `BLOCKED` if the gate did not pass |

- **States:** Pending, Running, Completed, Failed, Blocked. States only move forward, and timestamps come from the events themselves.
- **Evidence check.** A task without its required tool call or output file is downgraded from Completed to Failed (`source: evidence_check`).
- **Deterministic steps are shown separately**, never as agents: source intake, preparation, contract drafting, and Crew 2 training plus reports.
- **No percentages or estimated durations** are produced.
- **Flag meanings:** `crew2_reached` means Crew 2 was entered. `model_training_started` becomes true only when model fitting begins.

## 5. Streamlit behaviour

- **Opening or refreshing** shows a clean screen: Upload Dataset, consent, Start Analysis and "Status: Not started". No historical results are shown.
- **View Past Runs** (sidebar toggle). The run picker starts empty, and results appear only for the run chosen.
- **During a run:** only that run's monitor and results are shown, refreshing every 2 seconds. A refresh, or another browser session, reconnects to the active run and cannot start a second one.
- **After a run finishes:**
  - The monitor shows "finished: <status>".
  - The workbook still in the uploader is shown as "Already analyzed in run <id>", and Start Analysis stays disabled, so it is never relaunched from the same upload.
  - **Start a new analysis** clears the page for a new workbook. It resets the uploader and consent and deletes nothing.
- **A different workbook** is a separate pending analysis. It shows "Not started", needs fresh consent and a new click, and the previous run's results are hidden.
- **Run states** come from the process and the run's own manifest:
  - **RUNNING:** the process is alive.
  - **FINISHED:** a final status was recorded.
  - **INTERRUPTED:** the process ended without a final status. This is never shown as complete.
  - **NOT_STARTED:** the process exited before creating the run, for example after a failed readiness check.
- **Results are run-scoped:** Crew 2 metrics are shown only for that run's own verified completion.

## 6. Genuine execution evidence (real Gemini, launched through Streamlit)

All values below come from the runs' own files under `runs/` and `outputs/logs/`, re-read during the final audit. Crew 2's read-only checks (`verify_handoff`, `validate_metrics`) were re-run without writing anything.

| | Original workbook | Corrupted workbook (two runs) |
|---|---|---|
| Run IDs | `20261111T094219Z_1fbdc4d8` | `20261111T084920Z_0cd0bd8e`, `20261111T093641Z_db37ee1c` |
| Receipt SHA-256 (recomputed from stored bytes) | `db70f1e3…bd44d`, equal to `data/raw/E Commerce Dataset.xlsx` | `1d37e8a6…c783a`, equal to the generated corrupted copy |
| Source integrity / policy | VERIFIED / 2.0.0 | VERIFIED / 2.0.0 (both runs) |
| Chain (receipt = audit = profile = contract; CSV hash = contract) | Consistent | Consistent (both runs) |
| Crew 1 agents | 3 × Completed from task events; tool calls `quality`, `eda`, `business` | Same (both runs) |
| Independent gate | **PASS**, no failed rules | **FAIL: `CSV-007`**, column `SatisfactionScore`, observed `9`, CSV line 2, approved maximum 5 |
| Crew 2 | 3 × Completed from task events; tool calls `features`, `training`, `evaluation` | 3 × **Blocked** ("Not started: no independent gate PASS"); no `crew2/` folder, no model file |
| Flags | `crew2_reached=true`, `model_training_started=true`, `model_deployed=false` | `crew2_reached=false`, `model_training_started=false` |
| Final status | `FULL_RUN_COMPLETE_NEEDS_HUMAN_REVIEW` | `FAILED_VALIDATION` |
| Log | 0 tracebacks; no key-like strings | 0 tracebacks; no key-like strings |

Original run, held-out evaluation (1,126 test and 4,504 training customers; winner chosen by training-only CV F1):

| Model | Accuracy | Precision (churn) | Recall (churn) | F1 (churn) | ROC-AUC |
|---|---:|---:|---:|---:|---:|
| Majority-class baseline | 83.1% | 0.0% | 0.0% | 0.0% | 0.500 |
| Logistic regression | 88.0% | 70.1% | 50.5% | 58.7% | 0.884 |
| **Random forest (selected)** | **93.1%** | **92.4%** | **64.2%** | **75.8%** | **0.981** |

Selected random forest confusion matrix: 926 true negatives, 10 false positives, 68 false negatives, 122 true positives.

**Not inherited from another run.** `model_metrics.json` names this run's ID and this run's `clean_data.csv` hash, and the files were written during this run. The figures equal the earlier V1 run because the data and the fixed random seed are identical.

**Confirmed versus inferred:**
- **Confirmed:** real LLM calls took place. The review texts are model-written (not the offline test LLM's fixed text), tasks took 3–14 seconds each, Gemini is named in the logs, and `.env` configures a Gemini model.
- **Inferred, not recorded:** the number of Gemini requests and their cost.
- **Not established:** who clicked Start Analysis for each run. The launch records do not hold a session or user identity (see §8).

## 7. Automated verification

- **177 tests pass** in the `ai-final-crewai` Conda environment (`python -m unittest discover -s tests -p "test_*.py"`). This includes all pre-V2 tests.
- **Areas covered:**
  - per-workbook profiling and the one-cell corruption utility;
  - receipt and gate integrity (valid, corrupted, tampered CSV, tampered receipt, unregistered policy, weakened contract);
  - the full Flow on both branches;
  - per-agent tracking with a real CrewAI crew driven by an offline scripted LLM (no network);
  - process control (consent, double-click, simultaneous sessions, stale lock, interrupted run);
  - the Streamlit interface via `streamlit.testing` (clean start, past runs, finished-run display, new-analysis reset, failed-gate display, no stale metrics).
- **V1 recheck:** `python src/integration_flow.py --run-dir runs/20261107T175915Z_16425875 --check` still returns `GATE_PASS_EXISTING_CREW2_VERIFIED` under policy 1.0.0.

## 8. Limitations and open items

- **A PASS is not proof of truth.** It means the data meets the approved, specified rules and integrity checks. It does not show that the data is factually correct, that units are known, or that the model predicts future churn. The model is a course prototype and is not production-ready.
- **Weaker provenance than V1 (accepted, V2-02).** Any workbook with a verified receipt and passing content can train a model, not only the reference file.
- **Launch audit gaps.** Launch records do not capture the browser session, the server PID or port, the consent and click time separately from the launch, the model ID or the number of LLM requests. The run manifest stores the staged file name (`<sha256>.xlsx`); the original file name is only in `outputs/logs/<id>.launch.json`.
- **Operational limits:**
  - One analysis can run at a time.
  - A stale lock whose process ID has been reused after a reboot keeps Start disabled until that process ends (fix: delete `runs/.active_run.lock`).
  - Streamlit server output is not saved to a file.
- **Console noise.** CrewAI's console listener can print `charmap` warnings on Windows. They do not affect results.
- **Timestamps** are recorded to the second.
- **Local clutter.** Uploads build up in `outputs/uploads/`. Development evidence folders (`outputs/phase2_evidence/` to `phase4_evidence/`) hold local copies of prepared data. All of these are Git-ignored.
