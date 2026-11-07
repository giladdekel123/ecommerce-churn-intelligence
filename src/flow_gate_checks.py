"""Read-only integration safeguards for CrewAI Flow. No training, no API calls."""
from __future__ import annotations

import csv
import json
import tempfile
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / 'contracts' / 'production_policy_v1.json'


def validate_run_root(run_dir: Path, runs_root: Path | None = None) -> Path:
    """Reject paths outside the project runs directory, including symlinks."""
    home = (runs_root or ROOT / 'runs').resolve()
    run = Path(run_dir).resolve()
    if run == home or not run.is_relative_to(home):
        raise ValueError('Run must be an individual directory under this project runs/')
    return run


def decision(report: dict) -> str:
    """No truthy shortcuts: only an exact independent clean PASS may advance."""
    if (isinstance(report, dict) and report.get('status') == 'PASS'
            and report.get('authorizes_crew2_training') is True
            and report.get('model_training_started') is False
            and report.get('failed_rules') == []):
        return 'gate_pass'
    return 'blocked'


def corrupt_copy_for_demo(source: Path, target: Path) -> None:
    """Modify a *copy* of the first row's Churn for a negative test; never source."""
    with source.open('r', newline='', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        header = next(reader)
        if 'Churn' not in header:
            raise ValueError('Churn column not present for negative demonstration')
        rows = list(reader)
    if not rows:
        raise ValueError('No data rows in source')
    rows[0][header.index('Churn')] = '7'  # violates approved {0,1}
    with target.open('w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, lineterminator='\n')
        writer.writerow(header)
        writer.writerows(rows)


def evaluate_gate(run_dir: Path, *, demo_fail: bool = False) -> dict:
    """Run independent production validation over existing artifacts, never writes them."""
    from src.production_gate import validate_production_handoff

    run = validate_run_root(run_dir)
    contract = run / 'handoff_candidate' / 'dataset_contract.json'
    csv_file = run / 'crew1' / 'clean_data.csv'
    if not demo_fail:
        report = validate_production_handoff(POLICY, contract, csv_file)
    else:
        with tempfile.TemporaryDirectory(prefix='churn_gate_negative_') as temp_dir:
            altered_csv = Path(temp_dir) / 'corrupted_demo.csv'
            corrupt_copy_for_demo(csv_file, altered_csv)
            report = validate_production_handoff(POLICY, contract, altered_csv)
    return report


def verify_existing_crew2(run_dir: Path) -> dict:
    """Verify gate proof and saved Crew 2 artifacts, without retraining or editing."""
    from src.crew2_train import verify_handoff
    from src.crew2_reports import validate_metrics

    run = validate_run_root(run_dir)
    proof = verify_handoff(run)  # rechecks the original approved CSV + saved PASS proof
    metrics = validate_metrics(run)  # checks preserved Crew 2 artifacts
    if metrics['run_id'] != run.name:
        raise ValueError('Model metrics refer to a different run')
    return {'status': 'EXISTING_CREW2_ARTIFACTS_VERIFIED', 'run_id': run.name,
            'winner': metrics['winner'], 'predictors': len(proof['features'])}


def prove_branch_safety(gate_report: dict, crew2_probe: Callable[[], dict]) -> dict:
    """Pure branch boundary, unit tested: must never touch Crew 2 on FAIL."""
    if decision(gate_report) != 'gate_pass':
        return {'route_status': 'FAILED_VALIDATION', 'crew2_reached': False,
                'model_training_started': False, 'failed_rules': gate_report.get('failed_rules', [])}
    outcome = crew2_probe()
    return {'route_status': 'GATE_PASS_EXISTING_CREW2_VERIFIED', 'crew2_reached': True,
            'model_training_started': False, 'crew2_check': outcome}
