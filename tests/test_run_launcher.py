"""Phase 5 process control: staging, explicit launch, locking, truthful run states."""
import json
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phase5_support import FakeProcess, isolate, write_run  # noqa: E402

from src import full_pipeline, run_launcher  # noqa: E402
from test_v2_receipt_gate import synthetic_workbook  # noqa: E402


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.base = isolate(self)
        self.book = synthetic_workbook(self.base / "book.xlsx").read_bytes()

    def stage(self):
        return run_launcher.stage_upload("My Workbook.xlsx", self.book)

    def test_staging_stores_bytes_and_never_launches(self):
        info = self.stage()
        self.assertEqual(Path(info["path"]).read_bytes(), self.book)
        self.assertEqual(Path(info["path"]).name, info["sha256"] + ".xlsx")
        self.assertEqual(info["name"], "My Workbook.xlsx")
        self.assertEqual(self.stage()["path"], info["path"])  # same bytes, same content-addressed file
        self.assertEqual(FakeProcess.instances, [])
        self.assertIsNone(run_launcher.current_run())

    def test_invalid_uploads_are_rejected(self):
        for name, data in (("data.csv", self.book), ("empty.xlsx", b""), ("fake.xlsx", b"not a workbook")):
            with self.assertRaises(run_launcher.UploadError, msg=name):
                run_launcher.stage_upload(name, data)
        with patch.object(run_launcher, "MAX_UPLOAD_BYTES", 10):
            with self.assertRaisesRegex(run_launcher.UploadError, "exceeds"):
                self.stage()

    def test_launch_uses_interpreter_argument_list_and_ignored_log(self):
        info = run_launcher.launch(self.stage())
        process = FakeProcess.instances[0]
        self.assertEqual(process.argv[0], sys.executable)
        self.assertIsInstance(process.argv, list)
        self.assertNotIn("shell", process.kwargs)
        self.assertEqual(process.argv[1:4], [str(run_launcher.PIPELINE), "--run", "--upload"])
        self.assertEqual(process.argv[process.argv.index("--expected-sha256") + 1], info["source_sha256"])
        self.assertEqual(process.argv[process.argv.index("--run-id") + 1], info["run_id"])
        self.assertNotIn("--policy", process.argv)  # the upload never selects a policy
        self.assertEqual(process.kwargs["cwd"], str(run_launcher.ROOT))
        self.assertTrue(Path(info["log"]).is_relative_to(run_launcher.LOGS_DIR))
        lock = json.loads(run_launcher.lock_path().read_text(encoding="utf-8"))
        self.assertEqual((lock["run_id"], lock["pid"]), (info["run_id"], process.pid))

    def test_repeated_clicks_cannot_start_a_second_run(self):
        staged = self.stage()
        run_launcher.launch(staged)
        for _ in range(3):
            with self.assertRaises(run_launcher.AlreadyRunning):
                run_launcher.launch(staged)
        self.assertEqual(len(FakeProcess.instances), 1)

    def test_simultaneous_sessions_launch_exactly_once(self):
        staged = self.stage()
        barrier, results = threading.Barrier(6), []

        def session():
            barrier.wait()
            try:
                results.append(run_launcher.launch(staged)["run_id"])
            except run_launcher.AlreadyRunning:
                results.append("refused")
        threads = [threading.Thread(target=session) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(FakeProcess.instances), 1)
        self.assertEqual(results.count("refused"), 5)

    def test_finished_process_releases_the_lock(self):
        staged = self.stage()
        first = run_launcher.launch(staged)
        FakeProcess.instances[0].alive = False
        self.assertFalse(run_launcher.current_run()["alive"])
        self.assertFalse(run_launcher.lock_path().exists())
        self.assertNotEqual(run_launcher.launch(staged)["run_id"], first["run_id"])

    def test_stale_lock_after_server_restart_is_cleared(self):
        run_launcher.lock_path().write_text(json.dumps({"run_id": "old", "pid": 123}), encoding="utf-8")
        self.assertFalse(run_launcher.current_run()["alive"])  # pid_alive -> False, no Popen handle
        self.assertFalse(run_launcher.lock_path().exists())

    def test_lock_without_pid_counts_as_starting(self):
        run_launcher.lock_path().write_text(json.dumps({"run_id": "x", "pid": None}), encoding="utf-8")
        with self.assertRaises(run_launcher.AlreadyRunning):
            run_launcher.launch(self.stage())

    def test_tampered_or_foreign_upload_is_refused(self):
        staged = self.stage()
        Path(staged["path"]).write_bytes(self.book + b"x")
        with self.assertRaisesRegex(run_launcher.UploadError, "fingerprint"):
            run_launcher.launch(staged)
        outside = self.base / "outside.xlsx"
        outside.write_bytes(self.book)
        with self.assertRaisesRegex(run_launcher.UploadError, "no longer available"):
            run_launcher.launch({**staged, "path": str(outside)})
        self.assertFalse(run_launcher.lock_path().exists())

    def test_failed_process_start_releases_lock(self):
        with patch.object(run_launcher, "_popen", side_effect=OSError("cannot start")):
            with self.assertRaises(OSError):
                run_launcher.launch(self.stage())
        self.assertFalse(run_launcher.lock_path().exists())

    def test_run_states_are_truthful(self):
        runs = run_launcher.RUNS_ROOT
        info = run_launcher.launch(self.stage())
        self.assertEqual(run_launcher.run_state(info["run_id"])["state"], "RUNNING")  # starting, no manifest yet
        write_run(runs, info["run_id"], status="CREW1_COMPLETE_PENDING_GATE")
        self.assertEqual(run_launcher.run_state(info["run_id"])["state"], "RUNNING")
        FakeProcess.instances[0].alive = False  # process died before a final status
        self.assertEqual(run_launcher.run_state(info["run_id"])["state"], "INTERRUPTED")
        write_run(runs, "20261111T000000Z_aaaaaaaa", status="FAILED_VALIDATION")
        self.assertEqual(run_launcher.run_state("20261111T000000Z_aaaaaaaa")["state"], "FINISHED")

    def test_run_that_never_started_is_reported(self):
        info = run_launcher.launch(self.stage())
        FakeProcess.instances[0].alive = False  # e.g. readiness BLOCKED: no run folder created
        self.assertEqual(run_launcher.run_state(info["run_id"])["state"], "NOT_STARTED")


class PipelineRunIdTests(unittest.TestCase):
    def test_run_id_must_be_well_formed_and_only_for_uploads(self):
        for argv in (["--run", "--run-id", "../evil"], ["--run", "--upload", "x.xlsx", "--run-id", "../evil"]):
            with patch("sys.stderr"), self.assertRaises(SystemExit):
                full_pipeline.main(argv)


if __name__ == "__main__":
    unittest.main()
