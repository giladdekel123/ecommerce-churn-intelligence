"""Read-only dashboard isolation tests; no Streamlit or Gemini import required."""
import json
import tempfile
import unittest
from pathlib import Path

from src.dashboard_data import list_runs, read_report, run_folder, snapshot, eda_html

ID1 = '20261107T175915Z_16425875'
ID2 = '20261107T180915Z_16425876'


class DashboardDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'runs'
        self.root.mkdir()

    def _make(self, rid, *, status, gate='PASS', reached=True):
        folder = self.root / rid
        (folder / 'crew1').mkdir(parents=True)
        (folder / 'crew2' / 'agent_reviews').mkdir(parents=True)
        (folder / 'handoff_candidate').mkdir()
        (folder / 'run_manifest.json').write_text(json.dumps({
            'run_id': rid, 'status': status, 'validation_status': gate,
            'crew2_reached': reached, 'model_training_started': reached,
            'model_deployed': False}), encoding='utf-8')
        return folder

    def test_list_only_valid_run_ids_with_manifests(self):
        self._make(ID1, status='FULL_RUN_COMPLETE_NEEDS_HUMAN_REVIEW')
        (self.root / 'setup_notes_archive').mkdir()
        (self.root / ID2).mkdir()
        self.assertEqual(list_runs(self.root), [ID1])

    def test_missing_runs_root_is_empty(self):
        self.assertEqual(list_runs(self.root / 'missing'), [])

    def test_bad_ids_refused(self):
        for bad in ('../secrets', 'other', '20261107T175915Z_16425875/../other'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                run_folder(self.root, bad)

    def test_manifest_mismatch_refused(self):
        folder = self._make(ID1, status='FULL_RUN_COMPLETE_NEEDS_HUMAN_REVIEW')
        (folder / 'run_manifest.json').write_text(json.dumps({'run_id': ID2}), encoding='utf-8')
        with self.assertRaises(ValueError):
            snapshot(self.root, ID1)

    def test_complete_model_metrics_scoped_to_same_run(self):
        folder = self._make(ID1, status='FULL_RUN_COMPLETE_NEEDS_HUMAN_REVIEW')
        (folder / 'crew2' / 'model_metrics.json').write_text(json.dumps({'winner': 'random_forest'}), encoding='utf-8')
        result = snapshot(self.root, ID1)
        self.assertTrue(result['complete'])
        self.assertEqual(result['metrics']['winner'], 'random_forest')

    def test_failed_run_cannot_inherit_model_report(self):
        good = self._make(ID1, status='FULL_RUN_COMPLETE_NEEDS_HUMAN_REVIEW')
        bad = self._make(ID2, status='FAILED_VALIDATION', gate='FAIL', reached=False)
        (good / 'crew2' / 'model_metrics.json').write_text('{"winner":"random_forest"}', encoding='utf-8')
        (good / 'crew2' / 'evaluation_report.md').write_text('SAVED FROM GOOD', encoding='utf-8')
        (bad / 'crew2' / 'evaluation_report.md').write_text('STALE SHOULD NOT SHOW', encoding='utf-8')
        result = snapshot(self.root, ID2)
        self.assertIsNone(result['metrics'])
        self.assertIsNone(read_report(result, 'evaluation_report'))
        self.assertFalse(result['complete'])

    def test_unapproved_gate_never_marks_complete(self):
        folder = self._make(ID1, status='FULL_RUN_COMPLETE_NEEDS_HUMAN_REVIEW', gate='FAIL')
        (folder / 'crew2' / 'model_metrics.json').write_text('{"winner":"random_forest"}', encoding='utf-8')
        self.assertIsNone(snapshot(self.root, ID1)['metrics'])

    def test_known_crew1_reports_and_eda_html(self):
        folder = self._make(ID1, status='FAILED_VALIDATION', gate='FAIL', reached=False)
        (folder / 'crew1' / 'insights.md').write_text('Research findings', encoding='utf-8')
        (folder / 'crew1' / 'eda_report.html').write_bytes(b'<html>EDA</html>')
        result = snapshot(self.root, ID1)
        self.assertEqual(read_report(result, 'insights'), 'Research findings')
        self.assertEqual(eda_html(result), b'<html>EDA</html>')

    def test_unknown_report_name_rejected(self):
        folder = self._make(ID1, status='FAILED_VALIDATION', gate='FAIL', reached=False)
        with self.assertRaises(ValueError):
            read_report(snapshot(self.root, ID1), '../../.env')

    def test_missing_report_returns_none(self):
        self._make(ID1, status='FULL_RUN_COMPLETE_NEEDS_HUMAN_REVIEW')
        self.assertIsNone(read_report(snapshot(self.root, ID1), 'quality_review'))


if __name__ == '__main__':
    unittest.main()
