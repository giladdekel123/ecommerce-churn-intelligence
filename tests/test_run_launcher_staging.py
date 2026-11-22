"""Regression tests for run_launcher.stage_upload: concurrent and interrupted staging.

Found in V3 Phase 1: staging used one shared temp file name per workbook, so two simultaneous
calls collided (PermissionError on Windows). These tests call stage_upload directly, with no
scenario-registry lock in front of it.
"""
import hashlib
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phase5_support import isolate  # noqa: E402

from src import run_launcher  # noqa: E402


def workbook(label="a", size=200):
    return b"PK\x03\x04" + (label.encode() * size)[:size]


class StagingTests(unittest.TestCase):
    def setUp(self):
        self.base = isolate(self)
        self.uploads = self.base / "outputs" / "uploads"

    def listing(self):
        return sorted(p.name for p in self.uploads.iterdir()) if self.uploads.exists() else []

    def hammer(self, count, target):
        barrier = threading.Barrier(count)
        results, errors = [None] * count, []

        def worker(i):
            barrier.wait()
            try:
                results[i] = target(i)
            except BaseException as exc:  # noqa: BLE001 - the test reports every failure
                errors.append(repr(exc))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
        [t.start() for t in threads]
        [t.join(60) for t in threads]
        return results, errors

    # ---- concurrency ---------------------------------------------------------------------------
    def test_many_simultaneous_stagings_of_one_workbook_are_safe(self):
        data = workbook()
        for _ in range(15):  # repeat, deleting the target each round, to force the creation race every time
            for leftover in self.uploads.glob("*") if self.uploads.exists() else []:
                try:
                    leftover.unlink()
                except OSError:
                    pass
            results, errors = self.hammer(16, lambda i: run_launcher.stage_upload("a.xlsx", data))
            self.assertEqual(errors, [])
            self.assertEqual({r["sha256"] for r in results}, {hashlib.sha256(data).hexdigest()})
        digest = hashlib.sha256(data).hexdigest()
        self.assertEqual((self.uploads / f"{digest}.xlsx").read_bytes(), data)
        self.assertEqual(self.listing(), [f"{digest}.xlsx"], "no temp file may be left behind")

    def test_simultaneous_stagings_of_different_workbooks_do_not_interfere(self):
        books = [workbook(chr(97 + i), 300 + i) for i in range(10)]
        results, errors = self.hammer(10, lambda i: run_launcher.stage_upload(f"{i}.xlsx", books[i]))
        self.assertEqual(errors, [])
        for book, info in zip(books, results):
            self.assertEqual(Path(info["path"]).read_bytes(), book)
        self.assertEqual(len(self.listing()), 10)

    def test_a_reader_never_sees_a_partial_file_while_it_is_being_staged(self):
        data = workbook("r", 2_000_000)           # large enough that a non-atomic write would be observable
        digest = hashlib.sha256(data).hexdigest()
        target = self.uploads / f"{digest}.xlsx"
        stop, bad = threading.Event(), []

        def reader():
            while not stop.is_set():
                try:
                    seen = target.read_bytes()
                except OSError:
                    continue                       # not there yet, or transiently locked: fine
                if hashlib.sha256(seen).hexdigest() != digest:
                    bad.append(len(seen))

        watcher = threading.Thread(target=reader)
        watcher.start()
        try:
            for _ in range(5):
                for p in (self.uploads.glob("*") if self.uploads.exists() else []):
                    try:
                        p.unlink()
                    except OSError:
                        pass
                _, errors = self.hammer(4, lambda i: run_launcher.stage_upload("r.xlsx", data))
                self.assertEqual(errors, [])
        finally:
            stop.set()
            watcher.join(30)
        self.assertEqual(bad, [], "a reader saw a truncated or partial workbook")

    def test_launch_verification_tolerates_concurrent_staging(self):
        data = workbook("l")
        upload = run_launcher.stage_upload("l.xlsx", data)

        def stage_again(_):
            for p in self.uploads.glob("*"):   # force re-creation while the other threads verify
                pass
            return run_launcher.stage_upload("l.xlsx", data)

        def verify(_):
            return run_launcher._sha256_stable(Path(upload["path"]))

        results, errors = self.hammer(12, lambda i: stage_again(i) if i % 2 else verify(i))
        self.assertEqual(errors, [])

    # ---- interrupted operations ----------------------------------------------------------------
    def test_a_failure_while_publishing_leaves_no_target_and_no_temp_file(self):
        data = workbook("f")
        digest = hashlib.sha256(data).hexdigest()
        with patch.object(run_launcher.os, "replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                run_launcher.stage_upload("f.xlsx", data)
        self.assertEqual(self.listing(), [], "an interrupted stage must leave nothing behind")
        info = run_launcher.stage_upload("f.xlsx", data)                  # and the next attempt works
        self.assertEqual(Path(info["path"]).name, f"{digest}.xlsx")

    def test_a_failure_while_writing_is_cleaned_up(self):
        with patch.object(run_launcher.os, "fsync", side_effect=OSError("io error")):
            with self.assertRaises(OSError):
                run_launcher.stage_upload("w.xlsx", workbook("w"))
        self.assertEqual(self.listing(), [])

    def test_a_locked_target_is_retried_until_it_succeeds(self):
        real_replace, calls = os.replace, {"n": 0}

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] <= 3:
                raise PermissionError("target is open elsewhere")
            return real_replace(src, dst)

        with patch.object(run_launcher.os, "replace", side_effect=flaky), \
                patch.object(run_launcher, "REPLACE_DELAY_SECONDS", 0):
            info = run_launcher.stage_upload("p.xlsx", workbook("p"))
        self.assertEqual(calls["n"], 4)
        self.assertEqual(Path(info["path"]).read_bytes(), workbook("p"))
        self.assertEqual(len(self.listing()), 1)

    def test_a_target_that_stays_locked_fails_cleanly_not_with_a_raw_permission_error(self):
        with patch.object(run_launcher.os, "replace", side_effect=PermissionError("locked")), \
                patch.object(run_launcher, "REPLACE_ATTEMPTS", 3), patch.object(run_launcher, "REPLACE_DELAY_SECONDS", 0):
            with self.assertRaises(run_launcher.UploadError):
                run_launcher.stage_upload("s.xlsx", workbook("s"))
        self.assertEqual(self.listing(), [])

    def test_identical_content_written_by_someone_else_counts_as_success(self):
        data = workbook("c")
        digest = hashlib.sha256(data).hexdigest()
        target = self.uploads / f"{digest}.xlsx"
        real_replace = os.replace

        def another_writer_wins(src, dst):
            real_replace(src, dst)                      # the "other" writer publishes identical bytes first
            raise PermissionError("target busy")        # ...then our own rename is refused

        with patch.object(run_launcher.os, "replace", side_effect=another_writer_wins), \
                patch.object(run_launcher, "REPLACE_DELAY_SECONDS", 0):
            info = run_launcher.stage_upload("c.xlsx", data)
        self.assertEqual(target.read_bytes(), data)
        self.assertEqual(info["sha256"], digest)

    def test_orphan_temp_files_from_a_crash_never_break_staging_and_old_ones_are_removed(self):
        data = workbook("o")
        digest = hashlib.sha256(data).hexdigest()
        self.uploads.mkdir(parents=True)
        old = self.uploads / f"{digest}.xlsx.crashed.tmp"
        recent = self.uploads / f"{digest}.xlsx.running.tmp"
        legacy = self.uploads / f"{digest}.xlsx.tmp"
        for orphan in (old, recent, legacy):
            orphan.write_bytes(b"partial")
        long_ago = time.time() - 2 * run_launcher.STALE_TEMP_SECONDS
        os.utime(old, (long_ago, long_ago))
        os.utime(legacy, (long_ago, long_ago))
        info = run_launcher.stage_upload("o.xlsx", data)
        self.assertEqual(Path(info["path"]).read_bytes(), data)
        self.assertFalse(old.exists() or legacy.exists(), "stale orphans are cleaned")
        self.assertTrue(recent.exists(), "a recent temp file may belong to a call still in progress")

    def test_a_truncated_target_is_repaired_not_trusted(self):
        data = workbook("t", 500)
        digest = hashlib.sha256(data).hexdigest()
        self.uploads.mkdir(parents=True)
        (self.uploads / f"{digest}.xlsx").write_bytes(data[:100])         # e.g. damaged by an older crash
        info = run_launcher.stage_upload("t.xlsx", data)
        self.assertEqual(Path(info["path"]).read_bytes(), data)

    # ---- behaviour that must not change --------------------------------------------------------
    def test_return_value_and_validation_are_unchanged(self):
        data = workbook("v")
        info = run_launcher.stage_upload("My Workbook.xlsx", data)
        self.assertEqual(set(info), {"name", "size", "sha256", "path"})
        self.assertEqual((info["name"], info["size"]), ("My Workbook.xlsx", len(data)))
        for bad_name, bad_data in (("a.csv", data), ("a.xlsx", b""), ("a.xlsx", b"not a zip"),
                                   ("a.xlsx", b"PK\x03\x04" + b"x" * (run_launcher.MAX_UPLOAD_BYTES + 1))):
            with self.assertRaises(run_launcher.UploadError):
                run_launcher.stage_upload(bad_name, bad_data)

    def test_restaging_existing_content_does_not_rewrite_it(self):
        data = workbook("e")
        run_launcher.stage_upload("e.xlsx", data)
        with patch.object(run_launcher.os, "replace", side_effect=AssertionError("must not rewrite")):
            run_launcher.stage_upload("e.xlsx", data)


if __name__ == "__main__":
    unittest.main()
