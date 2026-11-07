"""Crew 2's three distinct CrewAI review agents, with deterministic metric tools.

No training or policy edits. Markdown reviews are kept separate from the canonical,
number-checked `evaluation_report.md` and `model_card.md`.

  python src/crew2_agents.py --run-dir runs/<id> --check   # no model call
  python src/crew2_agents.py --run-dir runs/<id> --run     # live API calls
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.crew2_reports import validate_metrics

ROLES = (
    "Feature Engineering Specialist",
    "Machine Learning Engineer",
    "Model Evaluation Specialist",
)
REVIEW_FILES = (
    "feature_engineering_review.md",
    "model_selection_review.md",
    "evaluation_review.md",
)


def check(run_dir: Path) -> dict:
    """Verify actual checked model evidence; no LLM calls or output modifications."""
    from dotenv import load_dotenv
    load_dotenv()
    m = validate_metrics(run_dir)
    model = os.getenv("CREW2_GEMINI_MODEL") or os.getenv("CREW1_GEMINI_MODEL", "")
    return {"status": "MODEL_EVIDENCE_VERIFIED_FOR_AGENT_REVIEW_ONLY",
            "run_id": m["run_id"], "winner": m["winner"],
            "agents_planned": list(ROLES),
            "gemini_key_configured": bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")),
            "model_name": model, "model_deployed": False}


def run_agents(run_dir: Path) -> dict:
    """CrewAI reviews measured summaries; it cannot alter metrics, training, or gate."""
    from dotenv import load_dotenv
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("No Gemini API key in local .env")
    model_name = os.getenv("CREW2_GEMINI_MODEL") or os.getenv("CREW1_GEMINI_MODEL", "")
    if not model_name.startswith("gemini/"):
        raise ValueError("Configure CREW2_GEMINI_MODEL or CREW1_GEMINI_MODEL with a gemini/ prefix")
    run_dir = Path(run_dir).resolve()
    metrics = validate_metrics(run_dir)
    report_dir = run_dir / "crew2"
    for needed in ("evaluation_report.md", "model_card.md"):
        if not (report_dir / needed).is_file():
            raise ValueError(f"Create the deterministic {needed} before agent reviews")
    review_dir = report_dir / "agent_reviews"
    if review_dir.exists():
        raise FileExistsError(f"Previous agent review exists, refusing overwrite: {review_dir}")

    from crewai import Agent, Crew, LLM, Process, Task
    from crewai.tools import tool

    review_dir.mkdir(parents=True, exist_ok=False)
    log_path = review_dir / "agent_tool_calls.jsonl"

    def log_use(name: str) -> None:
        with log_path.open("a", encoding="utf-8") as fd:
            fd.write(json.dumps({"tool": name, "run_id": metrics["run_id"]}) + "\n")

    @tool("Read checked feature governance and provenance")
    def read_features() -> str:
        """Provide the approved predictor allowlist, excluded inputs, split sizes, and provenance."""
        current = validate_metrics(run_dir)
        log_use("features")
        return json.dumps({key: current[key] for key in (
            "run_id", "total_rows", "training_rows", "test_rows", "approved_predictor_columns",
            "excluded_inputs", "source_clean_data_sha256", "limitations")})

    @tool("Read checked training-fold model comparison")
    def read_training_cv() -> str:
        """Provide model selection protocol and verified training-fold CV F1 scores."""
        current = validate_metrics(run_dir)
        log_use("training")
        return json.dumps({key: current[key] for key in (
            "winner", "selection_rule", "test_use", "cv_training_only",
            "training_rows", "training_churn_count")})

    @tool("Read checked held-out evaluation")
    def read_evaluation() -> str:
        """Provide measured confusion matrices and holdout metrics, without customer rows."""
        current = validate_metrics(run_dir)
        log_use("evaluation")
        return json.dumps({key: current[key] for key in (
            "winner", "test_rows", "test_churn_count", "holdout_once",
            "status", "limitations")})

    llm = LLM(model=model_name, api_key=api_key, temperature=0.2, timeout=30, max_retries=0)
    agent1 = Agent(
        role=ROLES[0],
        goal="Audit approved input features, preprocessing provenance and possible leakage risks.",
        backstory="A skeptical feature-engineering specialist who distinguishes recorded controls from unverified temporal assumptions.",
        tools=[read_features], llm=llm, allow_delegation=False, verbose=True,
        max_iter=3, max_retry_limit=0, max_execution_time=90,
    )
    agent2 = Agent(
        role=ROLES[1],
        goal="Audit model variations, training-only selection, CV stability and reproducibility.",
        backstory="A careful ML engineer who never selects a winner from held-out test performance.",
        tools=[read_training_cv], llm=llm, allow_delegation=False, verbose=True,
        max_iter=3, max_retry_limit=0, max_execution_time=90,
    )
    agent3 = Agent(
        role=ROLES[2],
        goal="Evaluate churn recall and business trade-offs, weaknesses and deployment limitations.",
        backstory="A critical evaluator who interprets confusion matrices and avoids forward-looking or fairness guarantees.",
        tools=[read_evaluation], llm=llm, allow_delegation=False, verbose=True,
        max_iter=3, max_retry_limit=0, max_execution_time=90,
    )
    t1 = Task(
        description=("Call your feature tool before writing. Explain exactly which predictors were approved, "
                     "which were excluded, and why training-fitted preprocessing matters. Distinguish "
                     "documented policy from remaining risks. No new feature selection, training, or unverified unit claims."),
        expected_output="Concise Markdown review of features, governance, and temporal leakage caveats.",
        agent=agent1, output_file=str(review_dir / REVIEW_FILES[0]),
    )
    t2 = Task(
        description=("Call your training CV tool before writing. Review baseline and two trained variations, "
                     "and the winner selected by train-only CV positive-class F1. Never choose a winner by test metrics, "
                     "invent scores or rerun training."),
        expected_output="Concise Markdown model engineering review with recorded F1 scores and limitations.",
        agent=agent2, context=[t1], output_file=str(review_dir / REVIEW_FILES[1]),
    )
    t3 = Task(
        description=("Call your held-out evaluation tool before writing. Interpret accuracy versus minority-class "
                     "recall, confusion matrix counts, false negative and false positive trade-offs, fairness caveats, "
                     "and unknown future outcome horizon. Critically review previous two tasks. "
                     "Do NOT overwrite the deterministic evaluation_report.md or model_card.md."),
        expected_output="Concise critical Markdown held-out evaluation with measured figures and no deployment claim.",
        agent=agent3, context=[t1, t2], output_file=str(review_dir / REVIEW_FILES[2]),
    )
    crew = Crew(agents=[agent1, agent2, agent3], tasks=[t1, t2, t3],
                process=Process.sequential, memory=False, verbose=True)
    crew.kickoff()
    count = Counter()
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            count[json.loads(line)["tool"]] += 1
    missing_tools = [name for name in ("features", "training", "evaluation") if count[name] < 1]
    missing_reports = [name for name in REVIEW_FILES if not (review_dir / name).is_file()]
    return {"status": "CREW2_AGENT_REVIEWS_COMPLETE_NEED_HUMAN_REVIEW"
            if not (missing_tools or missing_reports) else "CREW2_AGENT_REVIEWS_INCOMPLETE",
            "run_id": metrics["run_id"], "tool_calls": dict(count),
            "missing_tools": missing_tools, "missing_reports": missing_reports,
            "reports": [str(review_dir / name) for name in REVIEW_FILES if (review_dir / name).is_file()],
            "model_deployed": False}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Crew 2 three-agent review (never retrains or deploys)")
    p.add_argument("--run-dir", required=True, type=Path)
    action = p.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--run", action="store_true")
    a = p.parse_args(argv)
    try:
        result = check(a.run_dir) if a.check else run_agents(a.run_dir)
        print(json.dumps(result, indent=2))
        return 0 if result["status"] != "CREW2_AGENT_REVIEWS_INCOMPLETE" else 1
    except (RuntimeError, ValueError, FileNotFoundError, OSError, TypeError, KeyError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc), "model_deployed": False}, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
