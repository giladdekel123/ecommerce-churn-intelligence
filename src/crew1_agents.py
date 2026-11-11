"""Crew 1's three reasoning agents, grounded in verified aggregate evidence.

This is a review-only stage. It never edits prepared data, approves a
production data contract, or authorizes Crew 2.

Preflight (no model call):
    python src/crew1_agents.py --run-dir runs/<run-id>/crew1 --check

Run the three agents (requires a local Gemini API key and incurs provider use):
    python src/crew1_agents.py --run-dir runs/<run-id>/crew1 --run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path

EXPECTED_AUDIT_STATUS = "PREPARED_NOT_GATE_APPROVED"
EXPECTED_EDA_STATUS = "EDA_DESCRIPTIVE_ONLY_NOT_GATE_APPROVED"
AGENT_ROLES = (
    "Data Quality Specialist",
    "Exploratory Data Analyst",
    "Business Insights & Contract Specialist",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_evidence(run_dir: Path) -> tuple[dict, dict]:
    """Fail closed if aggregate reports do not match the frozen prepared CSV."""
    run_dir = Path(run_dir).resolve()
    required = [
        run_dir / "clean_data.csv",
        run_dir / "cleaning_audit.json",
        run_dir / "eda_summary.json",
        run_dir / "eda_report.html",
    ]
    missing = [p.name for p in required if not p.is_file()]
    if missing:
        raise ValueError(f"Crew 1 inputs missing: {', '.join(missing)}")
    audit = json.loads(required[1].read_text(encoding="utf-8"))
    eda = json.loads(required[2].read_text(encoding="utf-8"))
    if audit.get("status") != EXPECTED_AUDIT_STATUS:
        raise ValueError("Unapproved preparation audit status")
    if eda.get("status") != EXPECTED_EDA_STATUS:
        raise ValueError("Unapproved EDA status")
    if audit.get("authorizes_crew2_training") is not False or eda.get("authorizes_crew2_training") is not False:
        raise ValueError("An input falsely claims to authorize Crew 2")
    if audit.get("run_id") != eda.get("run_id") or not audit.get("run_id"):
        raise ValueError("Audit and EDA run IDs disagree")
    actual_hash = _sha256(required[0])
    if actual_hash != audit.get("clean_data_sha256") or actual_hash != eda.get("clean_data_sha256"):
        raise ValueError("Clean data hash does not match the frozen audit and EDA")
    if audit.get("row_count_after") != eda.get("customers"):
        raise ValueError("Customer counts disagree")
    if len(audit.get("column_names", [])) != eda.get("column_count"):
        raise ValueError("Column counts disagree")
    if audit.get("missing_cells_retained") != eda.get("missing_cells"):
        raise ValueError("Missing-value counts disagree")
    if audit.get("churn_counts") != eda.get("churn_counts"):
        raise ValueError("Churn counts disagree")
    if audit.get("category_mappings_applied") != {} or audit.get("nulls_imputed") != 0:
        raise ValueError("Cleaning decisions differ from the agreed conservative policy")
    if eda["churn_counts"].get("1", 0) + eda["churn_counts"].get("0", 0) != eda["customers"]:
        raise ValueError("Churn totals do not equal customer count")
    return audit, eda


def _relevant_quality(audit: dict) -> dict:
    return {key: audit[key] for key in (
        "status", "row_count_before", "row_count_after", "missing_cells_retained",
        "missing_by_column", "churn_counts", "identifier_unique", "nulls_imputed",
        "rows_dropped", "category_mappings_applied", "numeric_values_clipped",
        "pending_category_mapping_review", "pending_range_review", "data_units",
        "authorizes_crew2_training",
    ) if key in audit}


def _relevant_eda(eda: dict) -> dict:
    return {key: eda[key] for key in (
        "status", "customers", "churn_counts", "historical_churn_rate",
        "missing_cells", "preferred_login_device_by_churn", "numeric_descriptives",
        "limitations", "authorizes_crew2_training",
    ) if key in eda}


def check(run_dir: Path) -> dict:
    from dotenv import load_dotenv
    load_dotenv()  # Local configuration only; never print the secret.
    audit, eda = _load_evidence(run_dir)
    return {
        "status": "EVIDENCE_VERIFIED_FOR_AGENT_REVIEW_ONLY",
        "run_id": audit["run_id"],
        "customers": eda["customers"],
        "churn_rate": eda["historical_churn_rate"],
        "agents_planned": list(AGENT_ROLES),
        "gemini_api_key_configured": bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")),
        "authorizes_crew2_training": False,
        "model_training_started": False,
    }


def run_agents(run_dir: Path, progress=None) -> dict:
    """Use LLMs for judgments only; statistics are read from Python reports.

    progress: optional LiveStatus; each agent's state is then recorded from the
    real CrewAI task events (src/agent_progress.py). None leaves V1 unchanged.
    """
    from dotenv import load_dotenv
    load_dotenv()  # .env is gitignored; do not print or commit it.
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("No Gemini key configured. Set GEMINI_API_KEY in a local .env; never paste the key into chat.")

    # Reverify both reports and the CSV's exact SHA before any model invocation.
    run_dir = Path(run_dir).resolve()
    audit, eda = _load_evidence(run_dir)
    expected_outputs = [run_dir / name for name in (
        "quality_review.md", "eda_interpretation.md", "insights.md", "agent_tool_calls.jsonl"
    )]
    existing = [p.name for p in expected_outputs if p.exists()]
    if existing:
        raise FileExistsError(f"Refusing to overwrite previous agent output: {', '.join(existing)}")

    from crewai import Agent, Crew, LLM, Process, Task
    from crewai.tools import tool

    # These three tools intentionally expose only aggregate data, never raw rows.
    log_path = run_dir / "agent_tool_calls.jsonl"

    def log_use(name: str) -> None:
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"tool": name, "run_id": audit["run_id"]}) + "\n")

    @tool("Read verified cleaning audit")
    def read_cleaning_audit() -> str:
        """Return verified row, missingness, and cleaning-decision statistics. Use for data quality assessment."""
        fresh_audit, _ = _load_evidence(run_dir)
        log_use("quality")
        return json.dumps(_relevant_quality(fresh_audit))

    @tool("Read verified exploratory statistics")
    def read_eda_summary() -> str:
        """Return verified churn rates, category group sizes, descriptives, and limitations. Use for EDA interpretation."""
        _, fresh_eda = _load_evidence(run_dir)
        log_use("eda")
        return json.dumps(_relevant_eda(fresh_eda))

    @tool("Read verified business evidence and constraints")
    def read_business_evidence() -> str:
        """Return validated business figures and unresolved policy decisions, without customer records."""
        fresh_audit, fresh_eda = _load_evidence(run_dir)
        log_use("business")
        return json.dumps({
            "quality": _relevant_quality(fresh_audit),
            "analysis": _relevant_eda(fresh_eda),
            "contract_policy_status": (
                "The owner-approved production policy and its 15-predictor allowlist exist, but this run's "
                "dataset contract is drafted by deterministic code after this review. Crew 2 authorization "
                "is PENDING the independent deterministic gate; no agent can grant or refuse it."),
            "unresolved_decisions": "category alias mappings, measurement units, future prediction horizon",
            "target_interpretation": "historical churn flag, with no documented future horizon",
            "missing_policy": "preserve nulls; fit imputation only on Crew 2 training folds",
        })

    model_name = os.getenv("CREW1_GEMINI_MODEL", "gemini/gemini-2.5-flash")
    if not model_name.startswith("gemini/"):
        raise ValueError("CREW1_GEMINI_MODEL must be a Gemini model identifier")
    llm = LLM(model=model_name, api_key=api_key, temperature=0.2, timeout=30, max_retries=0)

    quality_agent = Agent(
        role=AGENT_ROLES[0],
        goal="Diagnose quality risks and recommend justified next decisions without modifying any data.",
        backstory="A skeptical data-quality reviewer who separates verified evidence from unapproved cleaning choices.",
        tools=[read_cleaning_audit], llm=llm, allow_delegation=False, verbose=True,
        max_iter=3, max_retry_limit=0, max_execution_time=90,
    )
    eda_agent = Agent(
        role=AGENT_ROLES[1],
        goal="Interpret meaningful descriptive patterns and limitations from Python-calculated figures.",
        backstory="An analyst who challenges confounding, uncertain categories, and unsupported causal claims.",
        tools=[read_eda_summary], llm=llm, allow_delegation=False, verbose=True,
        max_iter=3, max_retry_limit=0, max_execution_time=90,
    )
    business_agent = Agent(
        role=AGENT_ROLES[2],
        goal="Produce cautious business-facing insights and explicitly state unresolved contract decisions.",
        backstory="A business analyst who documents evidence and refuses to self-approve a production data contract.",
        tools=[read_business_evidence], llm=llm, allow_delegation=False, verbose=True,
        max_iter=3, max_retry_limit=0, max_execution_time=90,
    )

    quality_task = Task(
        description=(
            "Call your cleaning-audit tool before answering. Explain the integrity of the "
            "prepared customer rows, missing cells, and unapproved category and range rules. "
            "Separate observations from suggestions; no cleaning or training."
        ),
        expected_output="A concise Markdown review with verified figures, risks, and approval-needed decisions.",
        agent=quality_agent, output_file=str(run_dir / "quality_review.md"),
    )
    eda_task = Task(
        description=(
            "Call the EDA-summary tool before answering. Interpret historical churn, group sample sizes "
            "and the three unnormalized login-device categories. Distinguish association from causation. "
            "Do not invent future time horizon, units, effects, statistics, or charts."
        ),
        expected_output="A concise Markdown interpretation citing numerical values and limitations.",
        agent=eda_agent, output_file=str(run_dir / "eda_interpretation.md"),
    )
    business_task = Task(
        description=(
            "Call the business-evidence tool. Read the previous two reviews as context. Write concise "
            "business-facing findings tied only to verified historical data. Explain that category alias "
            "mappings and measurement units remain unresolved. State that Crew 2 authorization is pending "
            "the independent deterministic gate, which runs after this review; do not claim Crew 2 is "
            "approved or permanently blocked. "
            "Do NOT produce, approve, edit or authorize dataset_contract.json."
        ),
        expected_output=("Markdown insights with evidence, caveats, next decisions, and a Crew 2 status "
                         "stated as pending the independent gate."),
        agent=business_agent, context=[quality_task, eda_task],
        output_file=str(run_dir / "insights.md"),
    )

    crew = Crew(
        agents=[quality_agent, eda_agent, business_agent],
        tasks=[quality_task, eda_task, business_task],
        process=Process.sequential,
        memory=False,
        verbose=True,
    )
    from src.agent_progress import downgrade_without_evidence, track_crew_tasks
    with track_crew_tasks(progress, {quality_task: AGENT_ROLES[0], eda_task: AGENT_ROLES[1],
                                     business_task: AGENT_ROLES[2]}):
        crew.kickoff()
    recorded = Counter()
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            recorded[json.loads(line)["tool"]] += 1
    missing_tools = [name for name in ("quality", "eda", "business") if recorded[name] < 1]
    missing_outputs = [path.name for path in expected_outputs[:3] if not path.is_file()]
    problems = {}
    for role, tool_key, path in zip(AGENT_ROLES, ("quality", "eda", "business"), expected_outputs[:3]):
        found = [f"tool '{tool_key}' not called"] if tool_key in missing_tools else []
        found += [f"{path.name} not written"] if path.name in missing_outputs else []
        problems[role] = "; ".join(found) or None
    downgrade_without_evidence(progress, problems)
    return {
        "status": "AGENT_REVIEW_COMPLETE_NEEDS_HUMAN_REVIEW" if not (missing_tools or missing_outputs)
                  else "AGENT_REVIEW_INCOMPLETE",
        "run_id": audit["run_id"],
        "tools_called": dict(recorded),
        "missing_required_tools": missing_tools,
        "missing_outputs": missing_outputs,
        "output_files": [str(path) for path in expected_outputs[:3] if path.is_file()],
        "dataset_contract_created": False,
        "authorizes_crew2_training": False,
        "model_training_started": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Crew 1 agent review, never production gate approval")
    parser.add_argument("--run-dir", type=Path, required=True)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--check", action="store_true", help="Local evidence preflight; no API call")
    choice.add_argument("--run", action="store_true", help="Use the configured Gemini API")
    args = parser.parse_args()
    report = check(args.run_dir) if args.check else run_agents(args.run_dir)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
