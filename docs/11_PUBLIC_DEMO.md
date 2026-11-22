# Public portfolio demo (read-only replay)

Status: **implemented locally on `feature/public-demo`; not deployed.** No public URL exists yet.

## What it is
`demo/demo_app.py` replays two **genuine, recorded** runs of the full pipeline from sanitized evidence:

| Scenario | Run ID | Outcome |
|---|---|---|
| PASS | `20261111T094219Z_1fbdc4d8` | Gate PASS, Crew 2 completed, model **not deployed**, needs human review |
| FAIL | `20261111T104100Z_ab39b7ad` | Deliberately corrupted workbook (`SatisfactionScore = 9`); gate FAIL `CSV-007`; Crew 2 blocked, no training |

Visitors can switch scenarios, step through the seven recorded component statuses (six agents + gate), inspect the gate finding, see the held-out metrics (labelled retrospective) and browse short verbatim agent-report excerpts. Every view is labelled a **historical execution replay**.

## What it is not / limitations
- Not the running application: nothing executes. No Gemini request, no agent, no training, no upload-triggered run, no subprocess, no secrets (enforced by `tests/test_demo_app.py`).
- Only two recorded runs. The controls navigate recorded evidence; they do not compute anything.
- Agent excerpts are AI-generated text, hand-selected and verified verbatim against the source reports. Full reports are not published.
- Metrics are retrospective on one public dataset, with no defined prediction horizon. They are not evidence of production readiness.
- Dataset attribution: Ankit Verma, *Ecommerce Customer Churn Analysis and Prediction* (https://www.kaggle.com/datasets/ankitverma2010/ecommerce-customer-churn-analysis-and-prediction), declared license CC BY-NC-SA 4.0 (https://creativecommons.org/licenses/by-nc-sa/4.0/). This is a non-commercial portfolio demo; share-alike conditions apply to adapted material. Transformations: aggregate statistics, gate results and model metrics derived from the data; the FAIL scenario used a locally modified copy (one value altered) that is not redistributed. The demo shows aggregates only. No rows, models, logs, workbooks or local paths are published.
- The FAIL run's Crew 1 agent reviews do not mention the corrupted value; the deterministic gate caught it (verified by `tools/build_demo_evidence.py`).

## Published evidence (strict allow-list)
`demo/evidence/pass.json`, `fail.json`, `MANIFEST.json` (SHA-256 of each; the app refuses a file that does not match). `tools/build_demo_evidence.py` names every published field explicitly, fails if an excerpt is not verbatim, and fails on any local path, e-mail, key-like or secret-like string. It reads the git-ignored `runs/` folder and is a local, manual tool. If evidence is regenerated, inspect the JSON by hand before committing.

## Dependencies
`demo/requirements.txt` contains only `streamlit==1.67.0`. It deliberately excludes CrewAI, google-genai, scikit-learn and the rest of the root requirements. Verified by installing only this file into a clean Python 3.12.10 venv (Streamlit 1.67.0, pandas 3.1.1, no CrewAI) and running the 16 demo tests; the full suite (193 tests) was run on Python 3.12 with the root requirements.

## Deployment procedure (Streamlit Community Cloud) — requires owner approval
1. Review and merge the Pull Request for `feature/public-demo` (never commit to `main` directly).
2. At share.streamlit.io choose **Create app** → repository `giladdekel123/ecommerce-churn-intelligence`, branch `main`, main file path `demo/demo_app.py`.
3. In Advanced settings, select **Python 3.12** (the version tested) and **add no secrets**.
4. Confirm the build used `demo/requirements.txt` (the build log must not mention crewai). Cloud looks for a dependency file next to the entrypoint first; **verify this on first deploy.**
5. Open the URL in a private window; check the replay banner, both scenarios, and that no upload or run control exists.
6. Only then replace the placeholder in `README.md` with the verified "Launch Interactive Demo" link.
7. Rollback: delete the app in the Cloud dashboard; revert the README line.

## Risks
- Free apps sleep after inactivity; the first visit may wait while the app wakes.
- Streamlit shows its own toolbar; none of its controls start the pipeline.
- Everything under `demo/`, `docs/` and `contracts/` becomes public with the repository.
- Regenerating evidence from other runs could leak; use the builder and review the output.
