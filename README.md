# AI Developers Course — Final Project

**Specification pack:** v1.1 (requirements baseline updated from instructor reply; architecture remains proposed until tested)  
**Updated:** 18 October 2026  
**Project:** E-Commerce Churn Intelligence — A Validated Multi-Agent AI Workflow  
**Course submission deadline:** 10 November 2026

## Purpose

This is a **project-definition and code-context pack**, not a completed application or a claim that the code runs. It translates the instructor's supplied assignment and our decisions into specifications for subsequent implementation in Cursor and/or Claude Code.

The core problem: prevent a data-analysis crew from silently handing incompatible data to a predictive-modeling crew. A CrewAI Flow must enforce a machine-readable dataset contract at that boundary.

**Instructor clarification received 18 October 2026:** The supplied assignment may be completed solo, but Git branches and real Pull Requests are still required. Do not commit bulk data dumps; a model may be committed if small, otherwise link to external storage. The presentation target is approximately **10 minutes**. A formal grading rubric was not supplied in the reply.

## Document index

1. [`docs/01_PROJECT_DEFINITION.md`](docs/01_PROJECT_DEFINITION.md) — purpose, scope, success criteria and constraints.
2. [`docs/02_REQUIREMENTS_AND_ACCEPTANCE.md`](docs/02_REQUIREMENTS_AND_ACCEPTANCE.md) — mandatory deliverables and traceable acceptance tests.
3. [`docs/03_ARCHITECTURE_AND_AGENTS.md`](docs/03_ARCHITECTURE_AND_AGENTS.md) — six agent roles, access boundaries, deterministic tools and Flow stages.
4. [`docs/04_DATA_CONTRACT_SPEC.md`](docs/04_DATA_CONTRACT_SPEC.md) — handoff rules, validation design, data audit findings and negative tests.
5. [`docs/05_IMPLEMENTATION_READINESS.md`](docs/05_IMPLEMENTATION_READINESS.md) — readiness gate, proposed milestones, implementation order and risks.
6. [`docs/06_DECISIONS_AND_OPEN_QUESTIONS.md`](docs/06_DECISIONS_AND_OPEN_QUESTIONS.md) — settled decisions vs. unresolved questions.

[`AGENTS.md`](AGENTS.md) contains concise project guardrails for coding assistants. [`CLAUDE.md`](CLAUDE.md) points Claude Code to those same instructions. Neither replaces the actual project documents.

## How to use

- **During planning:** read files 01–06 and revise only with an explicit decision recorded in file 06.
- **In Cursor:** place this folder at the root of your coding project, and explicitly ask Cursor to read `AGENTS.md` and the relevant `docs/` file before coding. We have not altered global Cursor rules.
- **In Claude Code:** put `CLAUDE.md` and `AGENTS.md` at the repository root; have Claude Code follow the linked specifications.
- **In Obsidian:** these are ordinary Markdown files and may also be referenced in a vault, but Obsidian is not required to run the program.
- **Before committing to GitHub:** respect the dataset's CC BY-NC-SA 4.0 conditions; keep bulk source, cleaned and feature datasets out of Git; keep API secrets out of Git. The supplied `.gitignore` provides safe defaults. A small serialized model can be committed if its redistribution is appropriate; use external storage for large models.

## Source documents

- Instructor technical brief: `final_projecא.html` — *Industry-Simulated AI Product Workflow*.
- Instructor scheduling and submission note: `הנחיות לפרויקטי גמר בקורס מפתחי AI.docx`.
- Data workbook: `E Commerce Dataset.xlsx`, worksheets `Data Dict` and `E Comm`.
- Kaggle dataset page: https://www.kaggle.com/datasets/ankitverma2010/ecommerce-customer-churn-analysis-and-prediction
- Declared dataset license (confirmed by user from Kaggle license field): https://creativecommons.org/licenses/by-nc-sa/4.0/

The three original instructor/data files are **not** duplicated inside this specification pack.
- Instructor Slack clarifications, reported by user on **18 October 2026**: solo permitted; branches/PRs mandatory even solo; do not place data dumps in repo; model if small or link externally; presentation ~10 minutes.

## First Cursor session (one step at a time)

1. Extract this package into a dedicated project folder and open that folder in Cursor.
2. Verify `README.md`, `AGENTS.md`, `CLAUDE.md`, `docs/` and `.gitignore` are present before coding.
3. Only after verifying the folder, initialize Git, create a feature branch, and develop a **small deterministic validation gate** with passing and failing fixtures.
4. Add the minimal CrewAI Flow, followed by the two crews and Streamlit. Do not start six agents at once.

The full Python application has **not** been created in this pack. The raw Kaggle workbook must be acquired separately and stored in a gitignored local data directory.
