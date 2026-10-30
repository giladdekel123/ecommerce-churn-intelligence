# 02 — Instructor Requirements & Acceptance Tests

**Origin key:** **HTML** = supplied instructor technical assignment; **Jonathan note** = Hebrew scheduling/submission message; **Jonathan Slack (18 Oct)** = his later direct clarification, reported by the user; **DESIGN** = additional project safeguard, not an instructor-imposed requirement.

## A. Mandatory requirements traceability

| ID | Required capability / output | Origin | Verification |
|---|---|---|---|
| R01 | Implement two **separate** CrewAI crews | HTML | Flow calls both distinct crews |
| R02 | At least **three agents per crew** | HTML | Crew configurations show ≥3 meaningful roles each |
| R03 | Crew 1: ingest, validate, clean, preprocess and document selected dataset | HTML | Crew 1 run records cleaning decisions |
| R04 | Crew 1: descriptive statistics, EDA, visualization, business summary | HTML | Review generated report and summary |
| R05 | Crew 1 output `clean_data.csv` | HTML | File exists and matches declared schema |
| R06 | Crew 1 output `eda_report.html` | HTML | Opens and shows charts/statistics |
| R07 | Crew 1 output `insights.md` | HTML | Reads as business-facing findings tied to data |
| R08 | Crew 1 output `dataset_contract.json` | HTML | File contains schema, allowed values, assumptions and constraints |
| R09 | Crew 2 reads validated cleaned dataset **and contract**, never the raw source | HTML | Restricted tool/file access and integration test |
| R10 | Crew 2 performs feature engineering | HTML | Produces features; pipeline records transforms |
| R11 | Train at least one predictive model and compare **at least two variations** | HTML | Comparison report with same evaluation protocol |
| R12 | Crew 2 output `features.csv` | HTML | File exists and feature provenance is documented |
| R13 | Crew 2 output `model.pkl` **or** `model.joblib` | HTML | Model loads in controlled environment |
| R14 | Crew 2 output `evaluation_report.md` | HTML | Metrics and comparison supplied |
| R15 | Crew 2 output `model_card.md` | HTML | Purpose, training data, metrics, limitations, ethical considerations |
| R16 | A CrewAI Flow automates handoff and validates contract + required features | HTML | End-to-end and negative tests |
| R17 | Graceful failure, deterministic processing, clear logging and saved artifacts | HTML | Invalid handoff blocks training with useful report |
| R18 | Shared public GitHub repository, Git/Pull Requests | HTML | Repo and real PR history verified |
| R19 | Streamlit or Flask application presenting results | HTML | Live/local demonstration |
| R20 | Final submission via Slack by **10 November 2026**: GitHub link + presentation | Jonathan note | Submission checklist |
| R21 | Supplied team project may be completed **individually** | Jonathan Slack (18 Oct) | Solo submission permitted |
| R22 | Use **Git branches and Pull Requests even for solo work** | Jonathan Slack (18 Oct) | Feature branch and completed PR history |
| R23 | **No data dumps in GitHub repository** | Jonathan Slack (18 Oct) | Inspect Git-tracked files: source/processed/feature dumps not tracked |
| R24 | A **small** trained model may be committed; a larger one should have an external link | Jonathan Slack (18 Oct) | Model storage decision documented in README |
| R25 | Presentation length approximately **10 minutes** | Jonathan Slack (18 Oct) | Timed presentation rehearsal |

**Technology notes from HTML:** required Python, Pandas, Scikit-learn, Matplotlib/Seaborn; HTML/CSS is allowed for frontend; Streamlit Cloud, Railway and Supabase are optional/recommended. Kaggle is recommended, **not mandated** as the dataset source.

## B. Proposed acceptance tests (not an official grading rubric)

| Test ID | Setup | Expected outcome | Requirement |
|---|---|---|---|
| AT01 | Execute the Flow with approved data | Both crews run, gate PASS, all eight named artifacts exist | R01–R17 |
| AT02 | After Crew 1, remove a mandatory column from a frozen `clean_data.csv` | Gate FAIL; Crew 2 does not train | R16–R17 |
| AT03 | Change `Churn` to 7 in a frozen test artifact | Gate FAIL with row/column explanation | R08, R16–R17 |
| AT04 | Duplicate `CustomerID` in a frozen test artifact | Gate FAIL with duplicate-ID explanation | R08, R16–R17 |
| AT05 | Widen the generated contract or alter its approved version without authorization | Gate FAIL against independent approved policy | DESIGN, strengthens R16 |
| AT06 | Force validation failure after a previous successful run | No new model saved or displayed as current; prior run labeled separately | DESIGN, strengthens R17 |
| AT07 | Train baseline + logistic regression + random forest under one held-out evaluation procedure | Report valid metrics, comparison and model card | R11, R14–R15 |
| AT08 | Clone repo in clean environment and follow setup | Program runs reproducibly with documented data-access steps and dependencies | R17–R20 |
| AT09 | Verify Crew 2's data tool access | Raw source path unavailable; only validated handoff readable | R09 |
| AT10 | Perform a declared-unit mismatch test **only if a trustworthy unit is established** | Gate rejects it; otherwise report that the source cannot support such a claim | DESIGN, instructor's motivating failure case |

**Important test-harness rule:** test corruption must be inserted **after** Crew 1 produces a snapshot and **before** the gate. If the entire pipeline automatically re-cleans and overwrites the test fixture, the negative test proves nothing. Provide a controlled test mode or a gate-only validation path.

## C. Resolved constraints and remaining interpretation

- Jonathan confirmed that **solo submission is allowed**, but **branches and Pull Requests are still required**.
- The HTML calls for generated artifacts saved within the repository workspace; Jonathan subsequently clarified that **data dumps must not be committed**. Generate all eight files under per-run local folders, but ignore bulk CSV/Excel outputs in Git. Commit code, contract policy, reports/documentation and small synthetic test fixtures as appropriate. Preserve reproducibility through instructions and regeneration.
- A model artifact may be tracked **if small**, or linked externally if large. Its publication must also respect applicable rights.
- Jonathan specified approximately 10 minutes for the final presentation; **he did not provide a formal grading rubric** in this reply.
- The **21 October** instructor deadline applies **only** to a one-page proposal for an independently chosen project. For the supplied assignment, it remains our internal project-baseline milestone.
