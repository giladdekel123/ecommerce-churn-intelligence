"""Test-only CrewAI Flow smoke test for the existing semantic validation gate.

This is not a production handoff or approval of Crew 2. No agents, LLMs,
training jobs, or input-file writes are performed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from crewai.flow.flow import Flow, listen, router, start
from pydantic import BaseModel, Field

# Support: python src/flow_smoke.py --csv tests/fixtures/semantic/reference_months.csv
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.semantic_validator import validate_semantic_handoff  # noqa: E402


class SmokeState(BaseModel):
    csv_path: str = ""
    validation_report: dict[str, Any] = Field(default_factory=dict)
    route_status: str = "NOT_STARTED"


class ContractGateSmokeFlow(Flow[SmokeState]):
    """Demonstrate deterministic, fail-closed routing without invoking a crew."""

    @start()
    def validate(self) -> str:
        self.state.validation_report = validate_semantic_handoff(self.state.csv_path)
        return str(self.state.validation_report.get("status", "FAIL"))

    @router(validate)
    def choose_route(self, _validation_status: str) -> str:
        report = self.state.validation_report
        # This smoke test NEVER permits Crew 2, even after a synthetic PASS.
        if report.get("status") == "PASS" and report.get("authorizes_crew2_training") is False:
            return "synthetic_pass"
        return "blocked"

    @listen("synthetic_pass")
    def pass_but_no_training(self) -> str:
        self.state.route_status = "TEST_PASS_CREW2_BLOCKED"
        return "Synthetic validation PASS. Crew 2 remains unauthorized."

    @listen("blocked")
    def stop_on_failure(self) -> str:
        self.state.route_status = "FAILED_VALIDATION"
        return "Validation failed or authorization is unsafe. Processing blocked."


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a test-only CrewAI validation Flow.")
    parser.add_argument("--csv", required=True, help="Synthetic handoff CSV path")
    args = parser.parse_args()

    flow = ContractGateSmokeFlow()
    flow.state.csv_path = args.csv
    flow.kickoff()
    report = {
        "route_status": flow.state.route_status,
        "authorizes_crew2_training": False,
        "model_training_started": False,
        "validation_status": flow.state.validation_report.get("status", "FAIL"),
        "failed_rules": flow.state.validation_report.get("failed_rules", []),
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
