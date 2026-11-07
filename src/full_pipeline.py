"""Fresh end-to-end CrewAI Flow orchestrator for the churn course project.

  python src/full_pipeline.py --check   # no Gemini calls, no files written
  python src/full_pipeline.py --run     # fresh Crew 1 -> independent gate -> Crew 2

Preserves existing src/integration_flow.py --check/--demo-fail as separate
proof of PASS and intentional validation FAIL routing. Never overwrites runs.
No deployment or real-time customer decisions are performed.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_SOURCE = ROOT / 'data' / 'raw' / 'E Commerce Dataset.xlsx'
DEFAULT_POLICY = ROOT / 'contracts' / 'production_policy_v1.json'
RUNS_ROOT = ROOT / 'runs'


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _new_id() -> str:
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + uuid.uuid4().hex[:8]


def _read_json(path: Path) -> dict:
    obj = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(obj, dict):
        raise ValueError('Expected a JSON object: ' + str(path))
    return obj


def preflight(source: Path = DEFAULT_SOURCE, policy: Path = DEFAULT_POLICY,
              *, check_dependencies: bool = True) -> dict[str, Any]:
    """Read-only checks; specifically does not call Gemini or run a model."""
    source, policy = Path(source).resolve(), Path(policy).resolve()
    problems = []
    if not source.is_file():
        problems.append('Original Excel workbook is missing: ' + str(source))
    if not policy.is_file():
        problems.append('Approved production policy is missing: ' + str(policy))
    value = None
    if policy.is_file():
        try:
            value = _read_json(policy)
            if not (value.get('status') == 'APPROVED'
                    and value.get('formally_approved') is True
                    and value.get('policy_version') == '1.0.0'
                    and len(value.get('model_input_columns', [])) == 15):
                problems.append('Production policy is not an approved v1 15-feature policy')
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            problems.append('Cannot read approved policy: ' + str(exc))
    if source.is_file() and value is not None:
        pinned = value.get('source_sha256')
        if not isinstance(pinned, str) or len(pinned) != 64 or _sha256(source) != pinned:
            problems.append('Source workbook hash does not match the approved policy')
    if check_dependencies:
        for name in ('crewai', 'pandas', 'sklearn', 'matplotlib', 'openpyxl', 'dotenv'):
            if importlib.util.find_spec(name) is None:
                problems.append('Python package missing from active environment: ' + name)
        try:
            from dotenv import load_dotenv
            load_dotenv(ROOT / '.env', override=False)
        except ImportError:
            pass
        if not (os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY')):
            problems.append('Gemini API key is not configured in the active environment')
        if not os.getenv('CREW1_GEMINI_MODEL', '').startswith('gemini/'):
            problems.append('CREW1_GEMINI_MODEL is not configured as gemini/<model>')
        selected2 = os.getenv('CREW2_GEMINI_MODEL') or os.getenv('CREW1_GEMINI_MODEL', '')
        if not selected2.startswith('gemini/'):
            problems.append('Crew 2 Gemini model is not configured as gemini/<model>')
    return {
        'status': 'READY_FOR_FRESH_RUN' if not problems else 'BLOCKED',
        'errors': problems,
        'policy_version': value.get('policy_version') if isinstance(value, dict) else None,
        'approved_predictors': len(value.get('model_input_columns', [])) if isinstance(value, dict) else None,
        'raw_data_access': 'CREW1_ONLY',
        'model_training_started': False,
        'gemini_called': False,
    }


class FreshRunCoordinator:
    """Per-run stage functions used inside the CrewAI Flow. Fail closed."""

    def __init__(self, *, source: Path = DEFAULT_SOURCE, policy: Path = DEFAULT_POLICY,
                 runs_root: Path = RUNS_ROOT, run_id: str | None = None):
        self.source = Path(source).resolve()
        self.policy = Path(policy).resolve()
        self.runs_root = Path(runs_root).resolve()
        self.run_id = run_id or _new_id()
        if not self.run_id.replace('_', '').isalnum() or '/' in self.run_id or '\\' in self.run_id:
            raise ValueError('Invalid run identifier')
        self.run_dir = self.runs_root / self.run_id
        self.status = 'NOT_STARTED'
        self.stage = 'NOT_STARTED'
        self.error: str | None = None
        self.validation: dict[str, Any] = {}
        self.model_training_started = False
        self.crew2_reached = False
        self.output_files: list[str] = []
        self.started_at: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            'run_id': self.run_id, 'status': self.status, 'stage': self.stage,
            'started_at': self.started_at, 'updated_at': _utc(),
            'validation_status': self.validation.get('status', 'NOT_RUN'),
            'failed_rule_ids': [x.get('rule_id') for x in self.validation.get('failed_rules', [])],
            'crew2_reached': self.crew2_reached,
            'model_training_started': self.model_training_started,
            'model_deployed': False,
            'output_files': self.output_files,
            'error': self.error,
        }

    def _save(self) -> None:
        # Manifest is local and gitignored. Atomic replace keeps a readable snapshot.
        self.run_dir.mkdir(parents=True, exist_ok=True)
        target = self.run_dir / 'run_manifest.json'
        temp = self.run_dir / '.run_manifest.tmp'
        temp.write_text(json.dumps(self.snapshot(), indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        temp.replace(target)

    def begin(self) -> str:
        # Never reuse or overwrite an existing run, even if it was incomplete.
        if self.run_dir.exists():
            raise FileExistsError('A run with this ID already exists: ' + str(self.run_dir))
        self.started_at = _utc()
        self.status = 'STARTED'
        self.stage = 'INITIALIZED'
        self._save()
        return self.status

    def fail(self, stage: str, error: Exception | str) -> str:
        self.status = 'FAILED_' + stage.upper()
        self.stage = stage.upper()
        self.error = str(error)
        self._save()
        return self.status

    def crew1_stage(self) -> str:
        if self.status != 'STARTED':
            return self.status
        try:
            from src.crew1_prepare import run as prepare
            from src.crew1_eda import analyze
            from src.crew1_agents import run_agents

            crew1_dir = self.run_dir / 'crew1'
            preparation = prepare(source_path=self.source, output_dir=crew1_dir)
            if preparation['run_id'] != self.run_id or preparation['authorizes_crew2_training'] is not False:
                raise ValueError('Crew 1 preparation provenance/authorization mismatch')
            analyze(crew1_dir)
            reviews = run_agents(crew1_dir)
            if (reviews.get('status') != 'AGENT_REVIEW_COMPLETE_NEEDS_HUMAN_REVIEW'
                    or reviews.get('missing_required_tools') or reviews.get('missing_outputs')):
                raise ValueError('Crew 1 did not complete all three evidence-backed agent reviews')
            self.stage = 'CREW1_COMPLETED'
            self.status = 'CREW1_COMPLETE_PENDING_GATE'
            self.output_files = ['crew1/clean_data.csv', 'crew1/eda_report.html', 'crew1/insights.md']
            self._save()
        except Exception as exc:
            self.fail('CREW1', exc)
        return self.status

    def gate_stage(self) -> str:
        if self.status != 'CREW1_COMPLETE_PENDING_GATE':
            self.validation = {'status': 'FAIL', 'failed_rules': [{'rule_id': 'FLOW-CREW1', 'message': self.status}],
                               'authorizes_crew2_training': False, 'model_training_started': False}
            self._save()
            return 'FAIL'
        try:
            from src.crew1_contract_draft import run as create_draft
            from src.crew1_contract_finalize import finalize
            from src.production_gate import validate_production_handoff
            from src.flow_gate_checks import decision

            crew1_dir = self.run_dir / 'crew1'
            create_draft(crew1_dir)
            outcome = finalize(crew1_dir, self.policy)
            # The gate is separate from both agent crews and is checked again here.
            self.validation = validate_production_handoff(
                self.policy, self.run_dir / 'handoff_candidate' / 'dataset_contract.json',
                crew1_dir / 'clean_data.csv')
            if (outcome.get('status') != 'PASS'
                    or outcome.get('authorizes_crew2_training') is not True
                    or decision(self.validation) != 'gate_pass'):
                self.status = 'FAILED_VALIDATION'
            else:
                self.status = 'GATE_PASS_CREW2_AUTHORIZED'
                self.output_files.extend(['crew1/dataset_contract.json',
                                          'handoff_candidate/dataset_contract.json',
                                          'handoff_candidate/validation_report.json'])
            self.stage = 'INDEPENDENT_GATE_COMPLETED'
            self._save()
        except Exception as exc:
            self.validation = {'status': 'FAIL', 'authorizes_crew2_training': False,
                               'model_training_started': False,
                               'failed_rules': [{'rule_id': 'FLOW-GATE', 'message': str(exc)}]}
            self.fail('GATE', exc)
        return self.validation.get('status', 'FAIL')

    def route(self) -> str:
        from src.flow_gate_checks import decision
        return ('gate_pass' if self.status == 'GATE_PASS_CREW2_AUTHORIZED'
                and decision(self.validation) == 'gate_pass' else 'blocked')

    def blocked_stage(self) -> str:
        if self.status not in ('FAILED_CREW1', 'FAILED_GATE', 'FAILED_VALIDATION'):
            self.status = 'FAILED_VALIDATION'
        self.stage = 'STOPPED_BEFORE_CREW2'
        self.crew2_reached = False
        self.model_training_started = False
        self._save()
        return self.status

    def crew2_stage(self) -> str:
        if self.route() != 'gate_pass':
            return self.blocked_stage()
        try:
            from src.crew2_train import train_run
            from src.crew2_reports import write_reports
            from src.crew2_agents import run_agents
            self.crew2_reached = True
            self.stage = 'CREW2_TRAINING'
            self.model_training_started = True
            self.status = 'TRAINING_IN_PROGRESS_NOT_DEPLOYED'
            self._save()
            training = train_run(self.run_dir)  # re-verifies contract and gate proof
            if training.get('status') != 'TRAINED_AND_EVALUATED_NOT_DEPLOYED':
                raise ValueError('Crew 2 model training was not confirmed')
            self.stage = 'CREW2_REPORTING'
            self._save()
            report = write_reports(self.run_dir)
            if report.get('status') != 'REPORTS_CREATED_PENDING_HUMAN_REVIEW':
                raise ValueError('Crew 2 deterministic reports incomplete')
            reviews = run_agents(self.run_dir)
            if (reviews.get('status') != 'CREW2_AGENT_REVIEWS_COMPLETE_NEED_HUMAN_REVIEW'
                    or reviews.get('missing_tools') or reviews.get('missing_reports')):
                raise ValueError('Crew 2 three-agent review incomplete')
            required = ['crew2/features.csv', 'crew2/model.joblib',
                        'crew2/evaluation_report.md', 'crew2/model_card.md']
            if any(not (self.run_dir / p).is_file() for p in required):
                raise ValueError('Required Crew 2 artifact is missing')
            self.output_files.extend(required)
            self.status = 'FULL_RUN_COMPLETE_NEEDS_HUMAN_REVIEW'
            self.stage = 'DONE'
            self._save()
        except Exception as exc:
            self.fail('CREW2', exc)
        return self.status


def make_flow(coordinator: FreshRunCoordinator):
    """Create the real CrewAI Flow, importing CrewAI only for a fresh live run."""
    from crewai.flow.flow import Flow, start, listen, router
    from pydantic import BaseModel

    class FullPipelineState(BaseModel):
        run_id: str = ''
        run_dir: str = ''
        status: str = 'NOT_STARTED'
        stage: str = 'NOT_STARTED'
        validation_status: str = 'NOT_RUN'
        crew2_reached: bool = False
        model_training_started: bool = False
        errors: list[str] = []

    class FullPipelineFlow(Flow[FullPipelineState]):
        def _sync(self):
            current = coordinator.snapshot()
            self.state.run_id = current['run_id']
            self.state.run_dir = str(coordinator.run_dir)
            self.state.status = current['status']
            self.state.stage = current['stage']
            self.state.validation_status = current['validation_status']
            self.state.crew2_reached = current['crew2_reached']
            self.state.model_training_started = current['model_training_started']
            self.state.errors = [current['error']] if current['error'] else []

        @start()
        def initialize(self):
            coordinator.begin()
            self._sync()
            return self.state.status

        @listen(initialize)
        def execute_crew1(self, _status):
            coordinator.crew1_stage()
            self._sync()
            return self.state.status

        @listen(execute_crew1)
        def independent_gate(self, _status):
            coordinator.gate_stage()
            self._sync()
            return coordinator.validation.get('status', 'FAIL')

        @router(independent_gate)
        def branch_on_validation(self, _status):
            return coordinator.route()

        @listen('blocked')
        def do_not_train(self):
            coordinator.blocked_stage()
            self._sync()

        @listen('gate_pass')
        def execute_crew2(self):
            coordinator.crew2_stage()
            self._sync()

    return FullPipelineFlow()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description='Full safe Crew 1 -> gate -> Crew 2 orchestration')
    choice = p.add_mutually_exclusive_group(required=True)
    choice.add_argument('--check', action='store_true', help='Read-only preflight, zero Gemini calls')
    choice.add_argument('--run', action='store_true', help='Start a NEW end-to-end run, incurs Gemini calls')
    p.add_argument('--source', type=Path, default=DEFAULT_SOURCE)
    args = p.parse_args(argv)
    readiness = preflight(args.source)
    if args.check or readiness['status'] != 'READY_FOR_FRESH_RUN':
        print(json.dumps(readiness, indent=2, ensure_ascii=False))
        return 0 if readiness['status'] == 'READY_FOR_FRESH_RUN' else 1

    coordinator = FreshRunCoordinator(source=args.source)
    try:
        make_flow(coordinator).kickoff()
    except Exception as exc:
        # Even a CrewAI runtime exception must fail closed, preserving state.
        coordinator.fail('FLOW', exc)
    result = coordinator.snapshot()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if coordinator.status == 'FULL_RUN_COMPLETE_NEEDS_HUMAN_REVIEW' else 1


if __name__ == '__main__':
    raise SystemExit(main())
