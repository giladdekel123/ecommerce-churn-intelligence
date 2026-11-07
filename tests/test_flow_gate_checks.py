"""No-network, no-CrewAI unit tests of fail-closed routing boundary."""
from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from src.flow_gate_checks import (
    corrupt_copy_for_demo, decision, prove_branch_safety, validate_run_root
)


class FlowGateBoundaryTests(unittest.TestCase):
    good = {'status': 'PASS', 'authorizes_crew2_training': True,
            'model_training_started': False, 'failed_rules': []}

    def test_exact_clean_pass_advances(self):
        self.assertEqual(decision(self.good), 'gate_pass')

    def test_pass_with_failed_rules_blocks(self):
        self.assertEqual(decision({**self.good, 'failed_rules': [{'rule_id': 'BAD'}]}), 'blocked')

    def test_pass_without_explicit_authorization_blocks(self):
        self.assertEqual(decision({**self.good, 'authorizes_crew2_training': 1}), 'blocked')

    def test_claimed_training_already_started_blocks(self):
        self.assertEqual(decision({**self.good, 'model_training_started': True}), 'blocked')

    def test_failure_never_invokes_crew2_probe(self):
        calls = []
        def probe():
            calls.append('called')
            raise AssertionError('Crew 2 must not be called')
        out = prove_branch_safety({'status': 'FAIL', 'failed_rules': []}, probe)
        self.assertFalse(out['crew2_reached'])
        self.assertEqual(calls, [])

    def test_pass_invokes_readonly_probe_once(self):
        calls = []
        def probe():
            calls.append(True)
            return {'status': 'CHECKED'}
        out = prove_branch_safety(self.good, probe)
        self.assertEqual(out['route_status'], 'GATE_PASS_EXISTING_CREW2_VERIFIED')
        self.assertEqual(len(calls), 1)

    def test_negative_copy_does_not_change_original(self):
        with tempfile.TemporaryDirectory() as dirname:
            original = Path(dirname) / 'source.csv'
            temp = Path(dirname) / 'negative.csv'
            original.write_text('CustomerID,Churn\n1,0\n2,1\n', encoding='utf-8')
            before = original.read_bytes()
            corrupt_copy_for_demo(original, temp)
            self.assertEqual(original.read_bytes(), before)
            with temp.open(encoding='utf-8') as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]['Churn'], '7')

    def test_run_directory_escape_blocks(self):
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname) / 'runs'
            root.mkdir()
            run = root / 'sample'
            run.mkdir()
            self.assertEqual(validate_run_root(run, root), run.resolve())
            with self.assertRaises(ValueError):
                validate_run_root(Path(dirname), root)
            with self.assertRaises(ValueError):
                validate_run_root(root, root)


if __name__ == '__main__':
    unittest.main()
