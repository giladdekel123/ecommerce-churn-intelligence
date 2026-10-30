# AI Final Project — Coding Assistant Instructions

This project is the **AI Developers Course final assignment**, not the separate DCC/IMS project. Read `README.md` and the relevant `docs/` specification before changing code. `docs/06_DECISIONS_AND_OPEN_QUESTIONS.md` separates agreed decisions from unresolved choices.

## Non-negotiable guardrails

1. Implement **two distinct CrewAI crews with at least three meaningful agents each**, orchestrated by a **CrewAI Flow**.
2. Produce all **eight instructor-named artifacts**, using the exact filenames in `docs/02_REQUIREMENTS_AND_ACCEPTANCE.md`.
3. Before Crew 2, validate `clean_data.csv` **and** `dataset_contract.json` against approved, version-controlled policy in **deterministic Python**, then fail closed on violations.
4. **Crew 2 must not read the raw workbook or Crew 1 internal files.** The gate alone authorizes its inputs.
5. Do not silently relax the policy, modify tests to make failures pass or let a generated contract approve itself.
6. Keep a **frozen handoff** for negative tests; demonstrate `Churn=7` blocks modeling and no stale model is misrepresented.
7. Fit imputation/encoding/scaling on training folds only; use held-out evaluation. Never invent metrics, future-horizon guarantees, field units or source definitions.
8. Respect the dataset's declared **CC BY-NC-SA 4.0**, attribute and identify transformations. **Never commit data dumps** (original workbook, cleaned full CSV or feature dumps) or secrets. Keep them locally in ignored paths; small synthetic test fixtures may be tracked. A small model artifact may be tracked if appropriate; for a large model provide an external link.
9. Use run IDs, input hashes, logs and clear failure messages. **Git branches and real Pull Requests are mandatory even for solo work**. Never assume direct commits to `main` are sufficient. Prefer small reversible changes and explicit user review before merging.
10. Before major architectural changes, show the trade-offs and obtain user approval. Implement one small verifiable step at a time.

## First task after specifications are accepted

First check the dedicated folder and `.gitignore`, then initialize Git and create a feature branch. Scaffold a minimal project and prove a deterministic contract gate can PASS valid data and FAIL a controlled `Churn=7` fixture **before** building full crews and UI.
