# 05 — Implementation Readiness, Phases and Risks

**Update (11 Nov 2026):** This is the original pre-implementation plan, kept for history. The project has since been implemented (V1) and extended (Architecture V2, Phases 0–5 in `docs/09`). Current status, test results and genuine run evidence are in [`10_ARCHITECTURE_V2_IMPLEMENTATION_AND_EVIDENCE.md`](10_ARCHITECTURE_V2_IMPLEMENTATION_AND_EVIDENCE.md) and the README.

**Updated:** 2026-10-18  
**Milestone:** Internal project baseline by 21 October; instructor submission by 10 November.

## 1. Readiness verdict

**Ready to begin repository scaffolding and a small technical vertical slice, subject to user review of v1.1.** This is **not** a runnable application yet. Jonathan has approved solo completion and clarified the repository and presentation expectations.

### What is ready

- [x] Instructor sources and eight required artifact names identified.
- [x] Original workbook and dictionary inspected.
- [x] Dataset selected and historical churn target fixed.
- [x] Core two-crew/six-agent responsibilities proposed.
- [x] Contract gate, policy-governance idea, negative tests and basic UI defined.
- [x] Dataset's declared CC BY-NC-SA 4.0 license recorded from user's Kaggle license-field verification.
- [x] Jonathan's answers recorded: individual permitted; branches and PRs mandatory even solo; no data dumps in Git; small model or external link; ~10-minute presentation.

### What is not complete

- [ ] Final user review of this updated specification pack.
- [ ] Formal grading rubric (not supplied in Jonathan's reply; not a blocker).
- [ ] GitHub repository / Python environment / CrewAI installation and compatibility testing.
- [ ] LLM provider/model and API spending limits selected.
- [ ] Approved JSON policy, detailed features and category canonicalization rules signed off.
- [ ] CrewAI crews and deterministic tools coded.
- [ ] Full successful and failed Flow executions demonstrated.
- [ ] Application and presentation completed.

## 2. Proposed implementation order

### Phase 0 — Baseline / feasibility (17–21 Oct)

- Review these specs; answer essential architectural objections.
- Create local project directory; add `.gitignore` **before staging files**; initialize Git, create a feature branch and later create a public GitHub repository **without uploading data dumps**.
- Pin initial Python dependencies and create a `.env.example`, `.gitignore`, `README`.
- Prove one minimal CrewAI Flow can call a controlled Python function in the selected environment.
- Produce a first hand-coded approved policy and a tiny gate-only negative test.

**Exit condition:** clean setup, minimal Flow, a failing test that genuinely blocks continuation.

### Phase 1 — Crew 1 and deterministic contract (22–27 Oct)

- Implement read-only Excel ingestion + profiling and approved category cleaning.
- Implement three Crew 1 roles with real tasks and controlled tools.
- Generate `clean_data.csv`, `eda_report.html`, `insights.md`, `dataset_contract.json`.
- Validate contract against fixed policy; retain logs and isolated run IDs.

**Exit condition:** reproducible Crew 1 artifacts, a PASS and a purposeful FAIL with Crew 2 not invoked.

### Phase 2 — Crew 2 and integration (28 Oct–3 Nov)

- Implement safe train/test split and preprocessing pipelines.
- Implement three Crew 2 roles and train baseline + two variations.
- Generate `features.csv`, `model.joblib`, `evaluation_report.md`, `model_card.md`.
- Integrate entire Flow and verify fail-safe routing.

**Exit condition:** full end-to-end valid execution, metrics traceable to Python, negative tests pass.

### Phase 3 — Application and quality (4–7 Nov)

- Build Streamlit run control, report viewer and controlled corruption demo.
- Add unit tests, integration tests, requirements, reproducible setup and PR history.
- Inspect model-card limitations, licensed data outputs and public repo contents.

**Exit condition:** clean clone and demo walkthrough succeed; no credentials/raw data leak.

### Phase 4 — Final rehearsal/submission (8–10 Nov)

- Use 8 Nov work session for critical bug fixes, rehearsed success/failure demo.
- Prepare and rehearse a **~10-minute** presentation and final GitHub README/screenshots as appropriate.
- By 10 Nov: submit public GitHub link and presentation via Slack.

## 3. Managerial gates / deliverables

| Date | Management gate | Evidence |
|---|---|---|
| 21 Oct | Project Definition v1.0 baseline | Signed-off requirements, risk decisions and backlog |
| 27 Oct | Crew 1 + gate | Four Crew 1 artifacts and valid/invalid tests |
| 3 Nov | Integrated crews | Four Crew 2 artifacts and model comparison |
| 7 Nov | Demo-ready | Streamlit, negative demos, repository docs and reproducibility |
| 10 Nov | Submit | GitHub URL and presentation in Slack |

These are **internal planning targets**, except 10 November, which is Jonathan's stated deadline. The 21 October instructor date applies to independent project proposals only.

## 4. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Agent complexity consumes schedule | High | Build deterministic vertical slice/gate first; keep agent roles meaningful |
| Contract self-approves invalid data | High | Approved, versioned policy independent of Crew 1 output |
| Test corruption accidentally cleaned away | High | Gate-only / frozen handoff test mode |
| Hidden target leakage | High | Split first for fitted transforms; forbid future-dated claims |
| Large UI scope | Medium | Streamlit three-section minimum |
| Missing credentials/provider choice | Medium | Decide early and smoke-test one model |
| Accidental bulk-data commit | High | Add `.gitignore` before first `git add`; inspect `git status` and tracked files before pushing |
| Weak PR history despite solo approval | Medium | Work on feature branches and create real Pull Requests before merging |

## 5. First executable experiment (next hands-on session)

1. Create repository skeleton and virtual environment.
2. Add a tiny example `clean_data.csv` and matching contract **in a test fixture**, not production output.
3. Write independent Python validator reading approved policy, contract and CSV.
4. Prove one passing and one failing case (e.g., `Churn=7`).
5. Wrap the validator in a minimal CrewAI Flow. Prove invalid results never launch Crew 2.
6. Only then expand Crew 1, Crew 2 and Streamlit.

We should execute these **one step at a time**, inspecting results before advancing.
