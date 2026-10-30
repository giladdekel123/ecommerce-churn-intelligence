# 06 — Decisions, Assumptions and Open Questions

**Rule:** changes to significant scope, contract constraints or evaluation claims must be recorded here with date, rationale and the person authorizing them.

## A. Decisions already taken

| ID | Decision | Rationale / provenance | Status |
|---|---|---|---|
| P-001 | Follow instructor's supplied project (Approach A), rather than fold DCC into it | Direct compliance and manageable schedule | Provisional choice, user confirmed inclination |
| DS-001 | Use Ankit Verma's E-commerce Customer Churn workbook | Fits retail scenario and provides genuine cleaning/modeling work | Selected |
| ML-001 | Train historical binary `Churn` classifier; do not assert unverified future horizon | Workbook defines label but not churn timing/observation horizon | Selected |
| LIC-001 | Dataset's declared license: CC BY-NC-SA 4.0 | User followed license field on original Kaggle dataset | Confirmed from user's source verification |
| ARC-001 | Two CrewAI crews, three roles each, one deterministic validation gate, one Flow | Instructor requirement + implementation recommendation | Architecture baseline proposed |
| ARC-002 | Version-controlled approved contract policy separate from generated contract | Avoid self-approving broken data | Proposed safeguard, subject to implementation review |
| UI-001 | Prefer Streamlit, not Flask | Faster demonstrable interface for educational scope | Provisional technical choice |
| MOD-001 | Compare logistic regression and random forest against a baseline | Established classifiers and defensible comparison | Provisional technical choice |
| PM-001 | 21 October = internal definition baseline, not obligatory supplied-project proposal | Instructor message reserves that date for independent proposals | Confirmed interpretation |
| J-01 | Supplied final assignment may be submitted individually | Jonathan Slack reply reported 18 Oct 2026 | Confirmed |
| J-02 | Branches and Pull Requests required, **even when working solo** | Jonathan Slack reply reported 18 Oct 2026 | Confirmed |
| J-03 | No data dumps in repo; small model may be committed, otherwise external link | Jonathan Slack reply reported 18 Oct 2026 | Confirmed |
| J-04 | Final presentation approximately 10 minutes | Jonathan Slack reply reported 18 Oct 2026 | Confirmed |

## B. Instructor clarifications — CLOSED (18 October 2026)

Jonathan answered J-01 through J-04 via Slack:

- **J-01:** Individual implementation and submission are permitted.
- **J-02:** Use real feature branches and Pull Requests even when working solo.
- **J-03:** Never commit data dumps; a small model file may be tracked, but a large model should be linked externally.
- **J-04:** The presentation should last approximately 10 minutes. **Rubric:** not provided in the reply, so no grading weights should be invented.

A colleague's separate question about independent project proposals does not change our selection of Approach A.

## C. Technical decisions to finalize before related coding

| ID | Open decision | Proposed owner/time |
|---|---|---|
| T-01 | AI provider, specific models and API budget ceiling | User + assistant, before first agent API call |
| T-02 | Exact contract canonical column names/categories and nullability | User + assistant, during first validator design |
| T-03 | Allowed target/predictor features and ethical treatment of demographics | User + assistant, before model training |
| T-04 | Final `.gitignore` and specific model/report publication choices | User + assistant, before first staging/push; J-03 already forbids bulk data dumps |
| T-05 | Reproducible software versions and environment strategy | Developer, at initial repository setup |
| T-06 | Whether a unit-mismatch test is credible for this workbook | Developer, after source-unit evidence review; otherwise document limitation |
| T-07 | How CrewAI agent work is observable and distinguished from deterministic Python functions | Developer, initial agent implementation |

## D. Never silently assume

- That `Churn=1` means abandonment during a particular future time window.
- That tenure is measured in months or cashback in USD.
- That a data-contract PASS proves semantic truth or production predictive validity.
- That public GitHub availability makes license restrictions disappear.
- That merely defining six agent names proves six meaningful agent roles exist at runtime.
- That rerunning upstream cleaning before a negative test validates the saved faulty handoff.
