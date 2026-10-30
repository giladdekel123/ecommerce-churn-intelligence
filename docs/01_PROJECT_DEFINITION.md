# 01 — Project Definition v1.1

**Status:** Instructor administrative clarifications confirmed; technical architecture is a working baseline, not implementation-complete.  
**Updated:** 2026-10-18  
**Decision reference:** DS-001 (dataset), ML-001 (objective), ARC-001 (architecture).

## 1. Project identity and business context

**Working name:** E-Commerce Churn Intelligence — A Validated Multi-Agent AI Workflow.

The instructor's fictional company, Harbor & Vale, has a data-analysis team and a data-science team. Their unvalidated data handoff once caused a model to run successfully on semantically incorrect measurements. The assignment is to make that handoff trustworthy through an enforceable data contract and a coordinating CrewAI Flow.

## 2. Mission statement

Design and implement a reproducible AI-assisted workflow that:

1. Uses Crew 1 to prepare and explore a customer dataset and publish four artifacts, including a dataset contract.
2. Independently validates that handoff against an approved, versioned contract policy.
3. Permits Crew 2 to engineer features, train and compare models **only after** validation succeeds.
4. Stops safely, logs explainable reasons and shows the failure in a Streamlit application when validation fails.
5. Preserves code and permitted artifacts in a public GitHub repository and supports a live demonstration.

**The differentiating result is reliable agent collaboration, not maximizing churn-model accuracy.**

## 3. Dataset and learning problem

- **Source:** Ankit Verma, *Ecommerce Customer Churn Analysis and Prediction*, Kaggle.
- **File:** `E Commerce Dataset.xlsx`; sheet `E Comm` (records), `Data Dict` (definitions).
- **Verified size:** 5,630 records × 20 columns; 4,682 `Churn=0` and 948 `Churn=1`.
- **Target:** `Churn` (1 = recorded churn, 0 = recorded non-churn).
- **ML formulation:** supervised binary classification using historical labeled records.
- **Valid empirical claim:** the model's performance on held-out records sampled from this historical dataset.
- **Invalid unsupported claim:** verified ability to predict churn next month / within 90 days; dates, outcome horizon and feature timing are not sufficiently documented.
- **Evaluation:** class imbalance means accuracy alone is insufficient; report recall, precision, F1, confusion matrix and ROC-AUC where appropriate, with a majority-class baseline.

## 4. Users and responsibilities

- **Primary demonstration user:** instructor/evaluator who initiates a workflow, inspects the output and deliberately corrupts a test artifact.
- **Development owner:** individual student (Jonathan explicitly confirmed solo submission is permitted).
- **System components:** Data Analyst Crew, validation gate, Data Scientist Crew, CrewAI Flow, Streamlit interface.

## 5. In scope

- Two separate CrewAI crews, each with at least three meaningful agent roles.
- Dataset ingestion, cleaning, EDA, charts, business summaries and dataset contract.
- Enforced handoff, deterministic checks, graceful failures and logging.
- Feature engineering, one saved trained model and a comparison of at least two model variations.
- Model evaluation, limitations and ethical considerations.
- A simple Streamlit demo showing both success and rejection cases.
- Reproducible local setup, tests, GitHub and real Pull Requests.

## 6. Out of scope (unless separately approved)

- Live retail integrations, production serving, retraining schedules and user authentication.
- Real-time predictions, future-horizon forecasting claims, causal claims about churn interventions, commercial customer targeting.
- RAG, vector databases, Supabase or cloud deployment without clear justification.
- Completing or incorporating the separate DCC/IMS project.
- Generating an elaborate dashboard before the reliable handoff is working.

## 7. Constraints and quality principles

- Submission via Slack **by 10 November 2026**, with a public GitHub link and an approximately **10-minute** presentation (Jonathan's Slack clarification).
- Required stack: CrewAI, Python, Git/GitHub with **branches and real Pull Requests even for solo work**, Streamlit or Flask, Pandas, Scikit-learn, Matplotlib/Seaborn.
- Required artifact filenames are enumerated in `02_REQUIREMENTS_AND_ACCEPTANCE.md`.
- API keys must remain outside Git; use environment variables and a suitable `.gitignore`. **No bulk data dumps in the Git repository**: do not commit raw workbook, cleaned full dataset or generated feature dataset. Preserve reproducibility through instructions and regeneration. A small model may be committed if appropriate; use a link outside the repo if large.
- Preserve raw workbook unchanged and out of Crew 2's reach.
- For a noncommercial course project, use the dataset in accordance with **CC BY-NC-SA 4.0**; give attribution and publish adapted data only under compliant terms. Dataset licensing and independently written code licensing are separate matters.
- Avoid data leakage: split the data before learning imputations, scalers, feature selections or model parameters; evaluate once on held-out data.
- Agents can reason and summarize but may not invent metrics. Python must compute data quantities and enforce the gate.

## 8. Definition of done

A reviewer can obtain the documented project, follow its setup instructions, run Crew 1, observe contract PASS and Crew 2's four artifacts, examine model-comparison results, then run a controlled invalid-data test where training is blocked with a readable error. Artifacts and logs are linked to individual run IDs so no stale model is mistaken for a fresh result.

## 9. Unresolved issues

- Jonathan resolved solo permission, mandatory branches/PRs, non-commitment of data dumps, conditional model storage, and ~10-minute presentation on 18 October 2026. **No formal grading rubric was provided in his reply.**
- AI model/provider, LLM budget, exact library versions, final canonical category mappings, exact contract constraints and approved publishing policy.
- Whether distribution of a trained model merits additional review under the dataset's licensing terms.

See `06_DECISIONS_AND_OPEN_QUESTIONS.md` for current status and ownership.
