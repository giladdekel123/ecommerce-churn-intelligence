# Architecture V2 — approval record (11 November 2026)

**Decision maker:** the **project owner**, who approved the assistant's Architecture V2 assessment and recommended implementation sequence in the project conversation on 11 November 2026. This record documents that decision. It is not an approval by the course instructor or by an independent auditor.

**Status:** Approved design. **Not yet implemented.** No code, policy file, test or run described below exists yet unless it is named as existing. This document records no execution results.

**Supersedes:** nothing. `docs/08_PRODUCTION_POLICY_APPROVAL_2026-11-07.md` and `contracts/production_policy_v1.json` remain valid for V1 and for historical runs.

## 1. Purpose

V1 accepts only one workbook: the copy whose SHA-256 is pinned in the V1 policy. Any other file is blocked before Crew 1 starts. V1 shows a validation failure only through a separate demonstration that corrupts a temporary copy of an existing handoff.

V2 makes the normal pipeline show both outcomes:

- The operator uploads an Excel `.xlsx` workbook containing worksheet `E Comm` and clicks **Start Analysis**.
- The **original** workbook completes Crew 1, passes the independent gate and runs Crew 2.
- A **corrupted** workbook (one `SatisfactionScore` value set to `9`) completes all three Crew 1 agents, fails independent content validation, and blocks all three Crew 2 agents.

## 2. Approved decisions

| ID | Decision | Rationale |
|---|---|---|
| V2-01 | Create a **separate V2 production policy** file. Keep `contracts/production_policy_v1.json` and every historical run unchanged. | The historical run's contract declares `approved_policy_version: 1.0.0`. Editing V1 in place would rewrite the rules that run was approved under. |
| V2-02 | Replace the pinned-workbook requirement with a **run-specific SHA-256 receipt** for each uploaded workbook. Keep the original workbook's fingerprint (`db70f1e34bc5326887ea3fb3d2a0854e82c577d55ef3a76fdfb25328631bd44d`) as **reference information only**. | Integrity becomes "the bytes uploaded are the bytes processed and recorded", which each upload can satisfy with its own fingerprint. Under V1, a corrupted file could never reach Crew 1 or the content rules. |
| V2-03 | **Keep the independent content-validation rules** unchanged, including `SatisfactionScore` as an integer from 1 to 5. | The content rules must decide whether data is acceptable. A changed fingerprint alone must not decide it. |
| V2-04 | **Move contract drafting to the end of Crew 1.** The gate then covers only finalisation against the approved policy and independent validation. | `dataset_contract.json` is a Crew 1 artifact under the instructor's requirements. In V1 it is drafted inside the gate stage. |
| V2-05 | Show **seven real execution statuses**: three Crew 1 agents, the gate, three Crew 2 agents. Show deterministic operations (preparation, EDA, contract drafting, training, reports) **separately**, not as agent work. | Statuses must come from actual execution events, not timers or guesses. Python steps must not be shown as LLM-agent work. |
| V2-06 | **Generate the corrupted workbook locally with a tracked script.** Never commit either the original or the corrupted dataset. | Both are derived from the licensed Kaggle data, and the instructor bans data dumps (J-03). A script makes the corruption reproducible and open to review. |
| V2-07 | **Keep the environment readiness check.** | It confirms dependencies, policy availability and API configuration without calling Gemini. |
| V2-08 | Develop on `feature/architecture-v2`. Open a Pull Request to `main` only after verification. | Instructor rule J-02: real branches and Pull Requests, even when working solo. |
| V2-09 | **Remove the dedicated negative-demonstration interface** from Streamlit. Keep the `src/integration_flow.py --demo-fail` command, its tests and the historical runs. | In V2 the failure path runs through the normal upload flow. The CLI demonstration and its tests remain proof of the V1 routing. |

## 3. Integrity model (V2-02 in detail)

V1 enforces the pinned hash in six places: environment preflight, Crew 1 preparation (default path only), contract drafting (global source profile), contract finalisation, gate rule `CTR-006`, and Crew 2 handoff re-verification. Under the V2 policy, each of these checks compares against **the run's own receipt** instead of the pin:

1. The SHA-256 of the uploaded bytes is computed when the file is received.
2. The pipeline copies the upload into the run folder and recomputes the hash. A mismatch stops the run.
3. A per-run source receipt records the hash. It is never edited afterwards.
4. Crew 1 recomputes the hash when it reads the workbook. The audit, the per-run source profile and the contract must all match the receipt.
5. The gate and Crew 2's re-verification accept the contract's source hash only if it equals the receipt.
6. The UI may report whether an upload equals the reference fingerprint. This is **for information only**: it neither approves nor blocks a run.

V1-policy runs keep their V1 pinned-hash behaviour. The policy is chosen from the contract's declared policy version.

## 4. Acceptance criteria (to be demonstrated, not yet observed)

| Upload | Required result |
|---|---|
| Original workbook | Receipt integrity passes. All three Crew 1 agents complete. The gate returns `PASS` with no failed rules. All three Crew 2 agents run. The run ends as `FULL_RUN_COMPLETE_NEEDS_HUMAN_REVIEW`. |
| Corrupted workbook | Receipt integrity passes with a **different** fingerprint. All three Crew 1 agents complete. The gate returns `FAIL` with status `FAILED_VALIDATION`, citing the `SatisfactionScore` maximum rule (currently `CSV-007`) and **no** source-hash failure. `crew2_reached = false`, `model_training_started = false`, and all three Crew 2 statuses show as blocked (never started). |
| Existing tests | Every pre-V2 automated test still passes without being weakened. |
| Historical run | A read-only `integration_flow --check` of the existing V1 run still passes under the V1 policy. |

The corrupted run must fail on **content**. If it stops earlier, for example because of an exception during contract drafting or finalisation, or a source-hash rule, the criterion has not been met.

## 5. Scope

**In scope:** V2 policy file; per-run source receipt and source profile; receipt-based checks in the gate, finalisation and Crew 2 verification; contract drafting moved to Crew 1; live status file and agent task hooks; Streamlit upload, **Start Analysis** button and seven-status panel; removal of the demonstration button; corrupted-workbook generator script; new tests; documentation updates.

**Out of scope:** new or relaxed content rules; category alias mappings; inferred units; changes to the 15-predictor allowlist; changes to modelling or metrics; deployment; any change to V1 policy files, existing tests or historical runs.

## 6. Non-negotiable safeguards

1. **Execution starts only when the operator clicks Start Analysis.** Uploading a file does nothing on its own.
2. **Fail closed.** A missing, unreadable or mismatched receipt, or a missing `E Comm` worksheet, stops the run before Crew 2.
3. **Only Crew 1 parses the workbook.** The Streamlit app stores and hashes the uploaded bytes but does not read workbook contents. Crew 2 never accesses the raw workbook.
4. **The gate stays deterministic Python, independent of both crews.** No contract, agent output or receipt can approve itself. Only an exact gate `PASS` permits Crew 2.
5. The gate **never repairs data**. Content rules are not relaxed or tuned after seeing a run.
6. **V1 is frozen.** `production_policy_v1.json`, docs 01–08, the V1 tests and historical run folders are not modified to accommodate V2.
7. **No data dumps or secrets are committed.** The original and corrupted workbooks, uploads, run folders and `.env` stay in Git-ignored locations. Only the generator script is tracked.
8. The generator corrupts **exactly one** `SatisfactionScore` cell (set to `9`) in `E Comm`, writes to an ignored path, never overwrites the original workbook, and checks that no other worksheet value changed.
9. **Statuses must be genuine.** Each of the seven statuses comes from an actual execution event or result. Blocked Crew 2 agents are shown as never started, not as failed or completed.
10. **No stale results.** A failed run never displays models, metrics or reports from another run.
11. **No Gemini calls or model training** during development until the owner approves the final two verification runs.
12. One small, tested step at a time. Ask before any push, merge or publication.

## 7. Limitations and accepted risks

- **Weaker provenance than V1 (accepted by the owner).** V1 accepted one known file. V2 accepts any workbook whose integrity chain holds and whose content passes the policy. A different workbook with valid schema and in-range values would therefore pass and train a model. The V2 approval scope must say this plainly. A `PASS` means the content meets the approved rules. It does not mean the data is the reference dataset.
- The V2 policy still pins the snapshot's 20 columns and category labels. An upload with new or missing category labels fails. The failure must surface as a gate result, not an unhandled exception.
- A corruption that changes a column's inferred type (for example a blank or decimal value in `SatisfactionScore`) is not covered by the approved demonstration and may fail at an earlier stage.
- A contract `PASS` does not prove semantic truth or future predictive validity. Units, the churn horizon and category synonyms remain unresolved, as recorded in doc 08.
- The corrupted run still spends Gemini calls on all three Crew 1 agents. This is intended, so the failure is shown after Crew 1 has completed.
- Crew 1's business-agent prompt currently says Crew 2 is blocked, even in passing runs. This is a known narrative mismatch, to be reviewed separately and not changed silently.

## 8. Approved implementation sequence

| Phase | Work | Gemini |
|---|---|---|
| 0 | This approval record | No |
| 1 | Per-run source profile; contract drafting uses it; corrupted-workbook generator | No |
| 2 | V2 policy and receipt-based integrity in gate, finalisation and Crew 2 verification; policy selection by version | No |
| 3 | Pipeline upload input, receipt, contract drafting moved into Crew 1, live status file (tested with stubbed crews) | No |
| 4 | Per-task status hooks for the six agents | No |
| 5 | Streamlit upload, Start Analysis, seven-status panel; demonstration button removed | No |
| 6 | README and specification updates | No |
| 7 | Owner-approved verification: one original run and one corrupted run, plus a read-only historical check | Yes, with approval |

Each phase ends with a concrete test and a result the owner can see before the next phase starts.

## 9. Implementation details still to confirm (do not change the decisions above)

- The exact CrewAI 1.15.37 hook for task start and completion events, to be verified in the `ai-final-crewai` environment.
- Where uploads are staged before a run starts. It must be a Git-ignored path; `data/uploads/` is not ignored today.
- How to prevent two runs launching at once, or a run being orphaned after a browser refresh (for example a lock file or a process-ID guard).
- The V2 policy version string and file name.

## 10. Implementation status (added 11 November 2026, after Phases 1–5)

The sections above are kept as the original approval record. Phases 1–5 and a final stabilisation pass have been implemented on `feature/architecture-v2`. Phase 7 (live verification) is covered by genuine runs launched through the Streamlit Start Analysis button. The owner confirmed intentionally running the original workbook. The launch records cannot establish who started the two corrupted-workbook runs (see `docs/10` §8). Full details and evidence: [`10_ARCHITECTURE_V2_IMPLEMENTATION_AND_EVIDENCE.md`](10_ARCHITECTURE_V2_IMPLEMENTATION_AND_EVIDENCE.md).

How the §9 details were resolved:
- **Task events:** CrewAI's `TaskStartedEvent`, `TaskCompletedEvent` and `TaskFailedEvent`, through the event bus. `Task.callback` was rejected because it runs before the output file is saved.
- **Upload staging:** `outputs/uploads/<sha256>.xlsx`, which is Git-ignored. Logs go to `outputs/logs/`.
- **Concurrency:** an exclusive lock file, `runs/.active_run.lock`, that records the process ID and run ID. A stale lock is released once its process has ended, and the interface reconnects to the active run after a refresh.
- **Policy:** `contracts/production_policy_v2.json`, version `2.0.0`, used only through the registry in `src/policy_registry.py`.

§4 acceptance criteria, as observed:

| Criterion | Result |
|---|---|
| Original workbook | **Met.** Run `20261111T094219Z_1fbdc4d8`: receipt VERIFIED, gate PASS, Crew 2 completed. |
| Corrupted workbook | **Met.** Runs `20261111T084920Z_0cd0bd8e` and `20261111T093641Z_db37ee1c`: receipt VERIFIED with a different fingerprint, Crew 1 completed, gate `FAILED_VALIDATION` on `CSV-007` only, Crew 2 blocked, no training. |
| Existing tests | **Met.** All pre-V2 tests pass. One V1 test was updated by owner instruction in Phase 4: `model_training_started` now means "model fitting began", not "Crew 2 was entered". |
| Historical run | **Met.** `integration_flow --check` still passes under policy 1.0.0. |

Deviation, recorded for transparency: V1 runs still draft the contract inside the gate stage, as an existing test requires. Only V2 runs draft at the end of Crew 1.
