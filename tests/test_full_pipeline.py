"""Fresh-run routing and safety tests; do not call CrewAI/Gemini or retrain."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.full_pipeline import FreshRunCoordinator, preflight


class FullPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.raw = self.base / 'source.xlsx'
        self.raw.write_bytes(b'test source only')
        self.policy = self.base / 'policy.json'
        self.valid_policy = {
            'status': 'APPROVED', 'formally_approved': True,
            'policy_version': '1.0.0', 'model_input_columns': [f'f{i}' for i in range(15)],
            'source_sha256': hashlib.sha256(self.raw.read_bytes()).hexdigest(),
        }
        self.policy.write_text(json.dumps(self.valid_policy), encoding='utf-8')

    def make(self, rid='20261107T120000Z_12345678'):
        return FreshRunCoordinator(source=self.raw, policy=self.policy,
                                   runs_root=self.base / 'runs', run_id=rid)

    def test_valid_approved_preflight_without_api(self):
        result = preflight(self.raw, self.policy, check_dependencies=False)
        self.assertEqual(result['status'], 'READY_FOR_FRESH_RUN')
        self.assertFalse(result['gemini_called'])

    def test_unapproved_policy_blocks_preflight(self):
        self.valid_policy['formally_approved'] = False
        self.policy.write_text(json.dumps(self.valid_policy), encoding='utf-8')
        self.assertEqual(preflight(self.raw, self.policy, check_dependencies=False)['status'], 'BLOCKED')

    def test_changed_workbook_blocks_preflight(self):
        self.raw.write_bytes(b'changed workbook')
        self.assertEqual(preflight(self.raw, self.policy, check_dependencies=False)['status'], 'BLOCKED')

    def test_missing_workbook_blocks_preflight(self):
        self.raw.unlink()
        self.assertEqual(preflight(self.raw, self.policy, check_dependencies=False)['status'], 'BLOCKED')

    def test_begin_creates_manifest_and_wont_overwrite(self):
        coordinator = self.make()
        self.assertEqual(coordinator.begin(), 'STARTED')
        self.assertTrue((coordinator.run_dir / 'run_manifest.json').is_file())
        with self.assertRaises(FileExistsError):
            self.make().begin()

    def test_crew2_is_unreachable_before_gate_pass(self):
        coordinator = self.make()
        coordinator.begin()
        with patch('src.crew2_train.train_run') as train:
            self.assertEqual(coordinator.crew2_stage(), 'FAILED_VALIDATION')
            train.assert_not_called()
        self.assertFalse(coordinator.model_training_started)

    def test_failed_crew1_blocks_gate_and_training(self):
        coordinator = self.make()
        coordinator.begin()
        coordinator.fail('CREW1', 'synthetic failure')
        self.assertEqual(coordinator.gate_stage(), 'FAIL')
        self.assertEqual(coordinator.route(), 'blocked')
        self.assertFalse(coordinator.crew2_reached)

    def test_route_requires_exact_independent_gate_pass(self):
        coordinator = self.make()
        coordinator.begin()
        coordinator.status = 'GATE_PASS_CREW2_AUTHORIZED'
        good = {'status': 'PASS', 'authorizes_crew2_training': True,
                'model_training_started': False, 'failed_rules': []}
        coordinator.validation = good
        self.assertEqual(coordinator.route(), 'gate_pass')
        coordinator.validation = {**good, 'failed_rules': [{'rule_id': 'BAD'}]}
        self.assertEqual(coordinator.route(), 'blocked')

    def test_failed_model_execution_is_recorded_not_deployed(self):
        coordinator = self.make()
        coordinator.begin()
        coordinator.status = 'GATE_PASS_CREW2_AUTHORIZED'
        coordinator.validation = {'status': 'PASS', 'authorizes_crew2_training': True,
                                  'model_training_started': False, 'failed_rules': []}
        with patch('src.crew2_train.train_run', side_effect=RuntimeError('synthetic model error')):
            self.assertEqual(coordinator.crew2_stage(), 'FAILED_CREW2')
        manifest = json.loads((coordinator.run_dir / 'run_manifest.json').read_text())
        self.assertTrue(manifest['model_training_started'])
        self.assertFalse(manifest['model_deployed'])

    def test_gate_failure_blocks_crew2_without_training(self):
        coordinator = self.make()
        coordinator.begin()
        coordinator.status = 'CREW1_COMPLETE_PENDING_GATE'
        with patch('src.crew1_contract_draft.run') as drafted, \
             patch('src.crew1_contract_finalize.finalize') as finalized, \
             patch('src.production_gate.validate_production_handoff') as validator, \
             patch('src.crew2_train.train_run') as train:
            validator.return_value = {'status': 'FAIL', 'authorizes_crew2_training': False,
                                      'model_training_started': False,
                                      'failed_rules': [{'rule_id': 'CHURN-003'}]}
            finalized.return_value = {'status': 'FAIL', 'authorizes_crew2_training': False}
            self.assertEqual(coordinator.gate_stage(), 'FAIL')
            drafted.assert_called_once()
            self.assertEqual(coordinator.route(), 'blocked')
            coordinator.blocked_stage()
            train.assert_not_called()
        self.assertFalse(coordinator.crew2_reached)
        self.assertFalse(coordinator.model_training_started)

    def test_crew2_success_publishes_required_artifacts(self):
        coordinator = self.make()
        coordinator.begin()
        coordinator.status = 'GATE_PASS_CREW2_AUTHORIZED'
        coordinator.validation = {'status': 'PASS', 'authorizes_crew2_training': True,
                                  'model_training_started': False, 'failed_rules': []}
        def fake_train(run_dir):
            folder = run_dir / 'crew2'
            folder.mkdir()
            for name in ('features.csv', 'model.joblib', 'evaluation_report.md', 'model_card.md'):
                (folder / name).write_text('synthetic artifact', encoding='utf-8')
            return {'status': 'TRAINED_AND_EVALUATED_NOT_DEPLOYED'}
        with patch('src.crew2_train.train_run', side_effect=fake_train), \
             patch('src.crew2_reports.write_reports', return_value={
                 'status': 'REPORTS_CREATED_PENDING_HUMAN_REVIEW'}), \
             patch('src.crew2_agents.run_agents', return_value={
                 'status': 'CREW2_AGENT_REVIEWS_COMPLETE_NEED_HUMAN_REVIEW',
                 'missing_tools': [], 'missing_reports': []}):
            self.assertEqual(coordinator.crew2_stage(), 'FULL_RUN_COMPLETE_NEEDS_HUMAN_REVIEW')
        state = json.loads((coordinator.run_dir / 'run_manifest.json').read_text())
        self.assertTrue(state['crew2_reached'])
        self.assertTrue(state['model_training_started'])
        self.assertFalse(state['model_deployed'])
        self.assertEqual(len(state['output_files']), 4)


if __name__ == '__main__':
    unittest.main()
