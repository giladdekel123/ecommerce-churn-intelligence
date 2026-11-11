"""Fresh end-to-end CrewAI Flow orchestrator for the churn course project.

  python src/full_pipeline.py --check   # V1: pinned reference workbook; no Gemini, no files written
  python src/full_pipeline.py --run     # V1: fresh Crew 1 -> independent gate -> Crew 2
  python src/full_pipeline.py --check --upload <workbook.xlsx>   # V2 readiness for one workbook
  python src/full_pipeline.py --run --upload <workbook.xlsx> [--expected-sha256 <hex>]   # V2 run

V2 (Architecture V2, policy 2.0.0): the selected workbook is copied into
runs/<run_id>/source/, its SHA-256 is verified and registered in a run-specific
source receipt, and the independent gate checks that receipt plus the unchanged
content rules. The policy is chosen by this code, never by the workbook or a contract.

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
import shutil
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
SOURCE_SHEET = 'E Comm'
GATE_COMPONENT = 'Independent Validation Gate'  # one of the seven tracked components
STORED_SOURCE_NAME = 'uploaded_workbook.xlsx'  # fixed name: never trust an uploaded filename as a path


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
        problems.extend(_environment_problems())
    return {
        'status': 'READY_FOR_FRESH_RUN' if not problems else 'BLOCKED',
        'errors': problems,
        'policy_version': value.get('policy_version') if isinstance(value, dict) else None,
        'approved_predictors': len(value.get('model_input_columns', [])) if isinstance(value, dict) else None,
        'raw_data_access': 'CREW1_ONLY',
        'model_training_started': False,
        'gemini_called': False,
    }


def _environment_problems() -> list[str]:
    """Shared environment readiness checks (unchanged from V1); no Gemini call."""
    problems = []
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
    return problems


def _source_problems(source: Path) -> list[str]:
    """Deterministic intake checks: a readable .xlsx with the E Comm worksheet.

    Reads only the workbook's sheet list (no customer cells). Content is judged
    later by Crew 1 and the independent gate, never here.
    """
    if not source.is_file():
        return ['Selected workbook is missing: ' + str(source)]
    if source.suffix.lower() != '.xlsx':
        return ['Selected file must be an Excel .xlsx workbook']
    try:
        import openpyxl
        workbook = openpyxl.load_workbook(source, read_only=True)
        try:
            names = list(workbook.sheetnames)
        finally:
            workbook.close()
    except Exception as exc:  # corrupt zip, not an Excel file, etc.
        return ['Selected file cannot be opened as an Excel workbook: ' + str(exc)]
    if SOURCE_SHEET not in names:
        return [f'Workbook has no {SOURCE_SHEET!r} worksheet (found: {names})']
    return []


def preflight_upload(source: Path, *, check_dependencies: bool = True) -> dict[str, Any]:
    """V2 readiness for one selected workbook. Read-only; no Gemini call, no files written."""
    from src.policy_registry import V2_VERSION, approved_policy_path

    source = Path(source).resolve()
    problems = _source_problems(source)
    try:
        policy = _read_json(approved_policy_path(V2_VERSION))
        if len(policy.get('model_input_columns', [])) != 15:
            problems.append('Registered V2 policy does not hold the approved 15-feature allowlist')
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        problems.append('Registered V2 policy is unavailable: ' + str(exc))
    if check_dependencies:
        problems.extend(_environment_problems())
    return {
        'status': 'READY_FOR_FRESH_RUN' if not problems else 'BLOCKED',
        'errors': problems,
        'mode': 'V2_UPLOAD',
        'policy_version': V2_VERSION,
        'source_sha256': _sha256(source) if source.is_file() else None,
        'raw_data_access': 'CREW1_ONLY',
        'model_training_started': False,
        'gemini_called': False,
    }


class FreshRunCoordinator:
    """Per-run stage functions used inside the CrewAI Flow. Fail closed."""

    def __init__(self, *, source: Path = DEFAULT_SOURCE, policy: Path = DEFAULT_POLICY,
                 runs_root: Path = RUNS_ROOT, run_id: str | None = None,
                 upload: bool = False, expected_sha256: str | None = None):
        self.upload = bool(upload)
        if self.upload:
            from src.policy_registry import V2_VERSION, approved_policy_path
            if Path(policy).resolve() != DEFAULT_POLICY.resolve():
                raise ValueError('V2 uploads always use the registered V2 policy; no policy override')
            policy = approved_policy_path(V2_VERSION)  # trusted: selected by code, not by input
        self.input_source = Path(source).resolve()  # the workbook the operator selected
        self.source = self.input_source  # V2: replaced by the registered copy in the run folder
        self.expected_sha256 = expected_sha256
        self.source_sha256: str | None = None
        self.live = None
        self.policy = Path(policy).resolve()
        try:
            self.policy_version = _read_json(self.policy).get('policy_version')
        except (OSError, ValueError, json.JSONDecodeError):
            self.policy_version = None
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
            'pipeline_mode': 'V2_UPLOAD' if self.upload else 'V1_PINNED_SOURCE',
            'policy_version': self.policy_version,
            'source_file': self.input_source.name,
            'source_sha256': self.source_sha256,
        }

    def _mark(self, stage: str, state: str, detail=None) -> None:
        # Live status exists for V2 runs only; it records observed transitions only.
        if self.live is not None:
            self.live.set(stage, state, detail)

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
        if self.upload:
            from src.live_status import LiveStatus
            self.live = LiveStatus(self.run_dir, self.run_id)
            self.intake_stage()
        return self.status

    def intake_stage(self) -> str:
        """V2: copy the selected workbook into this run, verify bytes, write the receipt."""
        self._mark('source_intake', 'RUNNING')
        try:
            from src.source_receipt import SOURCE_DIRNAME, create_receipt
            problems = _source_problems(self.input_source)
            if problems:
                raise ValueError('; '.join(problems))
            before = _sha256(self.input_source)  # fingerprint captured BEFORE the copy
            if self.expected_sha256 is not None and before != self.expected_sha256:
                raise ValueError('Selected workbook differs from the fingerprint captured at upload')
            stored_dir = self.run_dir / SOURCE_DIRNAME
            stored_dir.mkdir(exist_ok=False)
            stored = stored_dir / STORED_SOURCE_NAME
            shutil.copyfile(self.input_source, stored)
            # create_receipt recomputes the stored bytes and refuses a mismatch.
            receipt = create_receipt(self.run_dir, stored, expected_sha256=before)
            self.source = stored
            self.source_sha256 = receipt['source_sha256']
            self.stage = 'SOURCE_REGISTERED'
            self._save()
            self._mark('source_intake', 'COMPLETED', 'SHA-256 ' + self.source_sha256)
        except Exception as exc:
            self.fail('SOURCE_INTAKE', exc)
        return self.status

    def fail(self, stage: str, error: Exception | str) -> str:
        self.status = 'FAILED_' + stage.upper()
        self.stage = stage.upper()
        self.error = str(error)
        self._save()
        if self.live is not None:
            for name in self.live.running():
                self.live.set(name, 'FAILED', self.error)
            for name, entry in self.live.data['components'].items():
                if entry['state'] == 'RUNNING':  # was in progress when the run failed
                    self.live.set_component(name, 'FAILED', self.error)
            # Later work can no longer start; never leave it looking pending.
            self.live.block_unstarted('Not started: ' + self.status)
            self.live.finish(self.status)
        return self.status

    def _component(self, name: str, state: str, detail=None) -> None:
        if self.live is not None:
            self.live.set_component(name, state, detail)

    def _progress_kwargs(self) -> dict:
        # V1 calls stay exactly as before; V2 passes the live status for real task events.
        return {'progress': self.live} if self.live is not None else {}

    def crew1_stage(self) -> str:
        if self.status != 'STARTED':
            return self.status
        try:
            from src.crew1_prepare import run as prepare
            from src.crew1_eda import analyze
            from src.crew1_agents import run_agents

            crew1_dir = self.run_dir / 'crew1'
            self._mark('crew1_preparation', 'RUNNING')
            preparation = prepare(source_path=self.source, output_dir=crew1_dir)
            if preparation['run_id'] != self.run_id or preparation['authorizes_crew2_training'] is not False:
                raise ValueError('Crew 1 preparation provenance/authorization mismatch')
            if self.upload:
                from src.source_profiler import write_profile
                audit = _read_json(crew1_dir / 'cleaning_audit.json')
                profile = write_profile(self.source, crew1_dir / 'source_profile.json')
                if audit.get('source_sha256') != self.source_sha256 or profile['source_sha256'] != self.source_sha256:
                    raise ValueError('Crew 1 did not process the workbook registered in the source receipt')
            analyze(crew1_dir)
            self._mark('crew1_preparation', 'COMPLETED')
            self._mark('crew1_agents', 'RUNNING')
            reviews = run_agents(crew1_dir, **self._progress_kwargs())
            if (reviews.get('status') != 'AGENT_REVIEW_COMPLETE_NEEDS_HUMAN_REVIEW'
                    or reviews.get('missing_required_tools') or reviews.get('missing_outputs')):
                raise ValueError('Crew 1 did not complete all three evidence-backed agent reviews')
            self._mark('crew1_agents', 'COMPLETED')
            self.output_files = ['crew1/clean_data.csv', 'crew1/eda_report.html', 'crew1/insights.md']
            if self.upload:
                # V2: the contract is Crew 1's final artifact, drafted before the gate
                # from this run's own source profile (V1 keeps drafting in gate_stage).
                from src.crew1_contract_draft import run as create_draft
                self._mark('contract_draft', 'RUNNING')
                create_draft(crew1_dir)
                self._mark('contract_draft', 'COMPLETED')
                self.output_files.append('crew1/dataset_contract.json')
            self.stage = 'CREW1_COMPLETED'
            self.status = 'CREW1_COMPLETE_PENDING_GATE'
            self._save()
        except Exception as exc:
            self.fail('CREW1', exc)
        return self.status

    def gate_stage(self) -> str:
        if self.status != 'CREW1_COMPLETE_PENDING_GATE':
            self.validation = {'status': 'FAIL', 'failed_rules': [{'rule_id': 'FLOW-CREW1', 'message': self.status}],
                               'authorizes_crew2_training': False, 'model_training_started': False}
            self._save()
            self._mark('independent_gate', 'BLOCKED', 'Crew 1 did not complete: ' + self.status)
            self._component(GATE_COMPONENT, 'BLOCKED', 'Crew 1 did not complete: ' + self.status)
            return 'FAIL'
        try:
            from src.crew1_contract_draft import run as create_draft
            from src.crew1_contract_finalize import finalize
            from src.production_gate import validate_production_handoff
            from src.flow_gate_checks import decision
            from src.source_receipt import receipt_path

            crew1_dir = self.run_dir / 'crew1'
            self._mark('independent_gate', 'RUNNING')
            self._component(GATE_COMPONENT, 'RUNNING')
            if not self.upload:
                create_draft(crew1_dir)  # V1 behaviour unchanged
            outcome = finalize(crew1_dir, self.policy)
            # The gate is separate from both agent crews and is checked again here.
            self.validation = validate_production_handoff(
                self.policy, self.run_dir / 'handoff_candidate' / 'dataset_contract.json',
                crew1_dir / 'clean_data.csv',
                source_receipt=receipt_path(self.run_dir) if self.upload else None)
            if (outcome.get('status') != 'PASS'
                    or outcome.get('authorizes_crew2_training') is not True
                    or decision(self.validation) != 'gate_pass'):
                self.status = 'FAILED_VALIDATION'
            else:
                self.status = 'GATE_PASS_CREW2_AUTHORIZED'
                self.output_files.extend(([] if self.upload else ['crew1/dataset_contract.json'])
                                         + ['handoff_candidate/dataset_contract.json',
                                            'handoff_candidate/validation_report.json'])
            self.stage = 'INDEPENDENT_GATE_COMPLETED'
            self._save()
            rule_ids = [x.get('rule_id') for x in self.validation.get('failed_rules', [])]
            gate_state = 'COMPLETED' if self.status == 'GATE_PASS_CREW2_AUTHORIZED' else 'FAILED'
            gate_detail = 'PASS' if not rule_ids else 'FAIL: ' + ', '.join(map(str, rule_ids))
            self._mark('independent_gate', gate_state, gate_detail)
            self._component(GATE_COMPONENT, gate_state, gate_detail)
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
        if self.status not in ('FAILED_SOURCE_INTAKE', 'FAILED_CREW1', 'FAILED_GATE', 'FAILED_VALIDATION'):
            self.status = 'FAILED_VALIDATION'
        self.stage = 'STOPPED_BEFORE_CREW2'
        self.crew2_reached = False
        self.model_training_started = False
        self._save()
        if self.live is not None:
            # Genuine: this work (incl. the three Crew 2 agents) never started without a gate PASS.
            self.live.block_unstarted('Not started: no independent gate PASS')
            self.live.finish(self.status)
        return self.status

    def crew2_stage(self) -> str:
        if self.route() != 'gate_pass':
            return self.blocked_stage()
        try:
            from src.crew2_train import train_run
            from src.crew2_reports import write_reports
            from src.crew2_agents import run_agents
            # crew2_reached: the gate PASSed and Crew 2 was entered.
            # model_training_started: only once model fitting actually begins, i.e.
            # after train_run's own handoff re-verification and data checks pass.
            self.crew2_reached = True
            self.stage = 'CREW2_HANDOFF_VERIFICATION'
            self.status = 'CREW2_STARTED_NOT_DEPLOYED'
            self._save()
            self._mark('crew2_modeling', 'RUNNING', 'Re-verifying the gate-approved handoff')

            def training_started():
                self.model_training_started = True
                self.stage = 'CREW2_TRAINING'
                self.status = 'TRAINING_IN_PROGRESS_NOT_DEPLOYED'
                self._save()
                if self.live is not None:
                    self.live.set('crew2_modeling', 'RUNNING', 'Model fitting started')

            training = train_run(self.run_dir, on_training_start=training_started)
            if training.get('status') != 'TRAINED_AND_EVALUATED_NOT_DEPLOYED':
                raise ValueError('Crew 2 model training was not confirmed')
            # A confirmed trained-and-evaluated result is itself proof that fitting ran.
            self.model_training_started = True
            self.stage = 'CREW2_REPORTING'
            self._save()
            report = write_reports(self.run_dir)
            if report.get('status') != 'REPORTS_CREATED_PENDING_HUMAN_REVIEW':
                raise ValueError('Crew 2 deterministic reports incomplete')
            self._mark('crew2_modeling', 'COMPLETED')
            self._mark('crew2_agents', 'RUNNING')
            reviews = run_agents(self.run_dir, **self._progress_kwargs())
            if (reviews.get('status') != 'CREW2_AGENT_REVIEWS_COMPLETE_NEED_HUMAN_REVIEW'
                    or reviews.get('missing_tools') or reviews.get('missing_reports')):
                raise ValueError('Crew 2 three-agent review incomplete')
            self._mark('crew2_agents', 'COMPLETED')
            required = ['crew2/features.csv', 'crew2/model.joblib',
                        'crew2/evaluation_report.md', 'crew2/model_card.md']
            if any(not (self.run_dir / p).is_file() for p in required):
                raise ValueError('Required Crew 2 artifact is missing')
            self.output_files.extend(required)
            self.status = 'FULL_RUN_COMPLETE_NEEDS_HUMAN_REVIEW'
            self.stage = 'DONE'
            self._save()
            if self.live is not None:
                self.live.finish(self.status)
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
    sources = p.add_mutually_exclusive_group()
    sources.add_argument('--source', type=Path, default=DEFAULT_SOURCE,
                         help='V1: pinned reference workbook (default)')
    sources.add_argument('--upload', type=Path, default=None,
                         help='V2: any .xlsx with an E Comm worksheet; registered by run receipt')
    p.add_argument('--expected-sha256', default=None,
                   help='V2: fingerprint captured when the file was received; the run refuses a mismatch')
    p.add_argument('--run-id', default=None,
                   help='V2: run ID chosen by the launcher (YYYYMMDDTHHMMSSZ_<8 hex>) so the UI can follow it')
    args = p.parse_args(argv)
    if args.expected_sha256 and args.upload is None:
        p.error('--expected-sha256 requires --upload')
    if args.run_id is not None:
        import re
        if args.upload is None or not re.fullmatch(r'\d{8}T\d{6}Z_[0-9a-f]{8}', args.run_id):
            p.error('--run-id requires --upload and the format YYYYMMDDTHHMMSSZ_<8 hex>')
    if args.upload is not None:
        readiness = preflight_upload(args.upload)
    else:
        readiness = preflight(args.source)
    if args.check or readiness['status'] != 'READY_FOR_FRESH_RUN':
        print(json.dumps(readiness, indent=2, ensure_ascii=False))
        return 0 if readiness['status'] == 'READY_FOR_FRESH_RUN' else 1

    if args.upload is not None:
        coordinator = FreshRunCoordinator(source=args.upload, upload=True, run_id=args.run_id,
                                          expected_sha256=args.expected_sha256)
    else:
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
