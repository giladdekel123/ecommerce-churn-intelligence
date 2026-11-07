"""CrewAI Flow integration checkpoint: route an existing verified run, read-only.

Commands (from project root, with ai-final-crewai active):
  python src/integration_flow.py --run-dir runs/<run-id> --check
  python src/integration_flow.py --run-dir runs/<run-id> --demo-fail

Neither mode trains, calls an LLM, overwrites reports, or uses raw workbook.
A complete fresh-run orchestration is the next integration milestone.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from crewai.flow.flow import Flow, start, router, listen
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.flow_gate_checks import decision, evaluate_gate, verify_existing_crew2


class IntegrationState(BaseModel):
    run_dir: str = ''
    demo_fail: bool = False
    validation: dict[str, Any] = Field(default_factory=dict)
    status: str = 'NOT_STARTED'
    crew2_reached: bool = False
    model_training_started: bool = False
    details: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)


class SafeIntegrationFlow(Flow[IntegrationState]):
    """Routing gate: the only listener allowed to inspect Crew 2 is gate_pass."""

    @start()
    def independent_gate(self) -> str:
        try:
            self.state.validation = evaluate_gate(Path(self.state.run_dir), demo_fail=self.state.demo_fail)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            self.state.validation = {
                'status': 'FAIL', 'authorizes_crew2_training': False,
                'model_training_started': False,
                'failed_rules': [{'rule_id': 'FLOW-INPUT', 'message': str(exc)}],
            }
        return self.state.validation['status']

    @router(independent_gate)
    def gate_route(self, _result: str) -> str:
        return decision(self.state.validation)

    @listen('blocked')
    def stop_without_crew2(self):
        self.state.status = 'FAILED_VALIDATION'
        self.state.crew2_reached = False
        self.state.model_training_started = False

    @listen('gate_pass')
    def inspect_existing_crew2(self):
        # No call that can train, write outputs or touch a raw workbook.
        try:
            self.state.details = verify_existing_crew2(Path(self.state.run_dir))
            self.state.status = 'GATE_PASS_EXISTING_CREW2_VERIFIED'
            self.state.crew2_reached = True
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            self.state.status = 'FAILED_CREW2_ARTIFACT_VERIFICATION'
            self.state.errors = [str(exc)]
            self.state.crew2_reached = False
        self.state.model_training_started = False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Read-only CrewAI Flow PASS/FAIL routing checkpoint')
    parser.add_argument('--run-dir', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true', help='Recheck existing approved run')
    mode.add_argument('--demo-fail', action='store_true', help='Corrupt a temporary CSV copy only')
    args = parser.parse_args(argv)

    flow = SafeIntegrationFlow()
    flow.state.run_dir = str(args.run_dir)
    flow.state.demo_fail = args.demo_fail
    flow.kickoff()
    result = {
        'status': flow.state.status,
        'mode': 'NEGATIVE_DEMO' if args.demo_fail else 'EXISTING_RUN_CHECK',
        'run_id': args.run_dir.name,
        'validation_status': flow.state.validation.get('status', 'FAIL'),
        'failed_rule_ids': [item.get('rule_id') for item in flow.state.validation.get('failed_rules', [])],
        'crew2_reached': flow.state.crew2_reached,
        'model_training_started': flow.state.model_training_started,
        'details': flow.state.details,
        'errors': flow.state.errors,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    expected = 'FAILED_VALIDATION' if args.demo_fail else 'GATE_PASS_EXISTING_CREW2_VERIFIED'
    return 0 if flow.state.status == expected else 1


if __name__ == '__main__':
    raise SystemExit(main())
