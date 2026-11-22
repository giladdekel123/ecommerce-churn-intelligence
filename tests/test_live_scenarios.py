"""V3 live core: closed scenario registry with pinned workbook hashes."""
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_support import fake_xlsx  # noqa: E402

from src import run_launcher  # noqa: E402
from live.scenarios import (DEFAULT_SCENARIOS, Scenario, ScenarioRegistry, ScenarioUnavailable,  # noqa: E402
                            UnknownScenario)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.data = fake_xlsx("a")
        (self.base / "a.xlsx").write_bytes(self.data)
        self.scenario = Scenario("a", "A", "a.xlsx", hashlib.sha256(self.data).hexdigest(), "PASS", "")
        self.registry = ScenarioRegistry([self.scenario], self.base)
        patcher = patch.object(run_launcher, "UPLOADS_DIR", self.base / "uploads")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_default_scenarios_are_the_two_recorded_workbooks(self):
        by_id = {s.id: s for s in DEFAULT_SCENARIOS}
        self.assertEqual(set(by_id), {"pass", "fail"})
        self.assertEqual(by_id["pass"].sha256[:8], "db70f1e3")     # source_receipt of the genuine PASS run
        self.assertEqual(by_id["fail"].sha256[:8], "1d37e8a6")     # source_receipt of the genuine FAIL runs
        self.assertEqual((by_id["pass"].expected_outcome, by_id["fail"].expected_outcome), ("PASS", "FAIL"))
        ScenarioRegistry(DEFAULT_SCENARIOS, self.base)             # all entries are well-formed

    def test_matching_file_is_staged_through_the_existing_launcher(self):
        upload = self.registry.stage("a")
        self.assertEqual(upload["sha256"], self.scenario.sha256)
        self.assertTrue(Path(upload["path"]).is_file())
        self.assertTrue(Path(upload["path"]).is_relative_to(self.base / "uploads"))

    def test_simultaneous_staging_of_one_workbook_is_safe(self):
        # Regression: run_launcher.stage_upload uses a shared temp name; unserialized, this raised
        # PermissionError on Windows when two requests staged the same workbook at once.
        import threading
        errors, barrier = [], threading.Barrier(16)

        def go():
            barrier.wait()
            try:
                self.registry.stage("a")
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

        threads = [threading.Thread(target=go) for _ in range(16)]
        [t.start() for t in threads]
        [t.join(30) for t in threads]
        self.assertEqual(errors, [])

    def test_one_changed_byte_makes_the_scenario_unavailable(self):
        (self.base / "a.xlsx").write_bytes(self.data[:-1] + b"X")
        with self.assertRaises(ScenarioUnavailable):
            self.registry.stage("a")
        self.assertFalse((self.base / "uploads").exists())          # nothing was staged

    def test_missing_file_and_oversized_file_are_unavailable(self):
        (self.base / "a.xlsx").unlink()
        with self.assertRaises(ScenarioUnavailable):
            self.registry.verified_bytes("a")
        (self.base / "a.xlsx").write_bytes(self.data)
        with patch.object(run_launcher, "MAX_UPLOAD_BYTES", 10):
            with self.assertRaises(ScenarioUnavailable):
                self.registry.verified_bytes("a")

    def test_only_registered_ids_are_accepted(self):
        for bad in ("b", "", "../a", "a/../a", "A", None, 5, ["a"], "a.xlsx", "..\\a"):
            with self.assertRaises(UnknownScenario, msg=repr(bad)):
                self.registry.get(bad)

    def test_malformed_registry_entries_are_refused_at_construction(self):
        good = self.scenario
        bad_entries = [
            Scenario("a", "t", "../escape.xlsx", good.sha256, "PASS", ""),
            Scenario("a", "t", "sub/dir.xlsx", good.sha256, "PASS", ""),
            Scenario("a", "t", "a.csv", good.sha256, "PASS", ""),
            Scenario("a", "t", "a.xlsx", "ABC", "PASS", ""),
            Scenario("a", "t", "a.xlsx", good.sha256.upper(), "PASS", ""),
            Scenario("Bad Id", "t", "a.xlsx", good.sha256, "PASS", ""),
        ]
        for entry in bad_entries:
            with self.assertRaises(ValueError, msg=entry.filename):
                ScenarioRegistry([entry], self.base)
        with self.assertRaises(ValueError):
            ScenarioRegistry([good, good], self.base)               # duplicate id


if __name__ == "__main__":
    unittest.main()
