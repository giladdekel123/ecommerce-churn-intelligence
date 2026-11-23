"""V3 Phase 2: worker configuration, credential boundary and child environment. Offline: no Gemini, no real pipeline."""
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_support import CLIENT_ID, ROOT  # noqa: E402

from src import run_launcher  # noqa: E402
from live import worker  # noqa: E402
from live.config import ConfigError, check_private_file, load_allowed_subs, load_worker_config  # noqa: E402

# Built at run time so no key-shaped literal sits in the repository.
FAKE_KEY = "AI" + "zaSy" + "FAKE-NOT-A-REAL-KEY-0123456789abcd"
POSIX = os.name != "nt"


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()

    def write(self, name, data, mode=0o640):
        path = self.tmp / name
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        if POSIX:
            path.chmod(mode)
        return path

    def config(self, **over):
        raw = {"client_id": CLIENT_ID, "state_dir": str(self.tmp / "state"), "scenarios_dir": str(self.tmp / "sc"),
               "socket_path": str(self.tmp / "w.sock"), "kill_switch_path": str(self.tmp / "KILL"), "web_user": "live-web"}
        raw.update(over)
        return self.write("config.json", raw)

    def subs(self, subs=("100000000000000000001",)):
        return self.write("allowed_subs.json", {"allowed_subs": list(subs)})


class ConfigTests(Base):
    def test_a_valid_configuration_loads_with_conservative_defaults(self):
        cfg = load_worker_config(self.config(), self.subs())
        self.assertEqual((cfg.live.daily_start_limit, cfg.live.monthly_budget_usd, cfg.live.reserve_per_start_usd,
                          cfg.live.run_timeout_s), (2, 5.0, 0.5, 300))
        self.assertEqual(cfg.live.runs_root, self.tmp / "state" / "runs")
        self.assertEqual(cfg.live.allowed_subs, frozenset({"100000000000000000001"}))

    def test_invalid_configurations_are_refused(self):
        bad = [{"unknown_key": 1}, {"gemini_api_key": "x"}, {"client_secret": "x"}, {"cookie_token": "x"},
               {"client_id": "not-google"}, {"state_dir": "relative/dir"}, {"daily_start_limit": 0},
               {"daily_start_limit": 99}, {"daily_start_limit": True}, {"monthly_budget_usd": 500},
               {"reserve_per_start_usd": 6, "monthly_budget_usd": 5}, {"run_timeout_s": 5}, {"run_timeout_s": 10 ** 6},
               {"web_user": ""}, {"socket_group": ""}]
        for over in bad:
            with self.assertRaises(ConfigError, msg=over):
                load_worker_config(self.config(**over), self.subs())

    def test_missing_keys_and_bad_json_are_refused(self):
        for text in ('{"client_id": "' + CLIENT_ID + '"}', "not json", "[]"):
            with self.assertRaises(ConfigError, msg=text):
                load_worker_config(self.write("c.json", text), self.subs())
        with self.assertRaises(ConfigError):
            load_worker_config(self.tmp / "absent.json", self.subs())

    def test_allow_list_must_be_digit_only_google_ids(self):
        for bad in ([], ["owner@example.com"], ["12"], ["abc12345"], [123456789], ["1" * 40], ["10000000000000001"] * 21):
            with self.assertRaises(ConfigError, msg=bad):
                load_allowed_subs(self.subs(bad))
        with self.assertRaises(ConfigError):
            load_allowed_subs(self.write("a.json", {"allowed_subs": ["10000000000000001"], "extra": 1}))

    @unittest.skipUnless(POSIX, "file modes: exercised on Linux")
    def test_loosely_protected_files_are_refused(self):
        for mode in (0o644, 0o666, 0o604, 0o660, 0o664, 0o777):
            path = self.write(f"loose{mode:o}.json", {"allowed_subs": ["10000000000000001"]}, mode)
            with self.assertRaises(ConfigError, msg=oct(mode)):
                check_private_file(path)
        for mode in (0o600, 0o640, 0o400, 0o440):
            check_private_file(self.write(f"ok{mode:o}.json", "{}", mode))


class CredentialTests(Base):
    def test_the_key_is_read_from_the_credential_directory_only(self):
        (self.tmp / "gemini_api_key").write_text(FAKE_KEY + "\n", encoding="utf-8")
        before = {k: hash(v) for k, v in os.environ.items()}   # hashes: a failure can name variables, never show values
        self.assertEqual(worker.read_credential({"CREDENTIALS_DIRECTORY": str(self.tmp)}), FAKE_KEY)
        self.assertEqual({k: hash(v) for k, v in os.environ.items()}, before)   # nothing stored in this process's environment
        self.assertFalse(any(FAKE_KEY in value for value in os.environ.values()))   # (never print the environment)

    def test_missing_or_implausible_credentials_stop_the_worker(self):
        with self.assertRaises(worker.StartupRefused):
            worker.read_credential({})
        with self.assertRaises(worker.StartupRefused):
            worker.read_credential({"CREDENTIALS_DIRECTORY": str(self.tmp)})            # file absent
        for value in ("", "short", "has space inside a key value that is long enough", "x" * 300):
            (self.tmp / "gemini_api_key").write_text(value, encoding="utf-8")
            with self.assertRaises(worker.StartupRefused, msg=value[:10]):
                worker.read_credential({"CREDENTIALS_DIRECTORY": str(self.tmp)})

    def test_a_dotenv_in_the_checkout_or_above_it_stops_the_worker(self):
        deep = self.tmp / "opt" / "live" / "app"
        deep.mkdir(parents=True)
        for where in (deep, deep.parent):
            (where / ".env").write_text("GEMINI_API_KEY=x", encoding="utf-8")
            with self.assertRaises(worker.StartupRefused):
                worker.assert_no_dotenv(deep)
            (where / ".env").unlink()

    def test_the_real_checkout_has_no_dotenv_check_bypass(self):
        # The developer's checkout may have a .env; the deployment must not. The function reports it either way.
        has = any((p / ".env").exists() for p in (ROOT, *ROOT.parents))
        if has:
            with self.assertRaises(worker.StartupRefused):
                worker.assert_no_dotenv(ROOT)
        else:
            worker.assert_no_dotenv(ROOT)

    def test_pipeline_run_folder_must_be_the_configured_one(self):
        cfg = load_worker_config(self.config(), self.subs())
        checkout = self.tmp / "app"
        checkout.mkdir()
        with self.assertRaises(worker.StartupRefused):
            worker.assert_run_folder_matches(cfg, checkout)
        cfg.live.runs_root.mkdir(parents=True)
        try:
            os.symlink(cfg.live.runs_root, checkout / "runs", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        worker.assert_run_folder_matches(cfg, checkout)


class ChildEnvironmentTests(Base):
    PARENT = {"PATH": "/usr/bin", "LANG": "C.UTF-8", "HOME": "/home/worker", "CREW1_GEMINI_MODEL": "gemini/gemini-3.5-flash-lite",
              "CREDENTIALS_DIRECTORY": "/run/credentials/live-worker.service", "OAUTH_CLIENT_SECRET": "s3cret",
              "COOKIE_SECRET": "c00kie", "SSH_AUTH_SOCK": "/tmp/agent", "AWS_SECRET_ACCESS_KEY": "aws",
              "CREW_GEMINI_MAX_OUTPUT_TOKENS": "8192", "GOOGLE_API_KEY": "should-not-pass"}

    def test_the_child_gets_an_allow_list_not_a_copy(self):
        env = worker.build_child_env(FAKE_KEY, self.PARENT, home="/var/lib/live/home")
        self.assertEqual(env["GEMINI_API_KEY"], FAKE_KEY)
        self.assertEqual(env["HOME"], "/var/lib/live/home")
        self.assertEqual(env["CREW_RECORD_TOKEN_USAGE"], "1")
        self.assertEqual(env["CREW1_GEMINI_MODEL"], "gemini/gemini-3.5-flash-lite")
        allowed = {"PATH", "LANG", "HOME", "CREW1_GEMINI_MODEL", "GEMINI_API_KEY", *worker.CHILD_ENV_FIXED}
        self.assertEqual(set(env) - allowed, set())
        for leaked in ("OAUTH_CLIENT_SECRET", "COOKIE_SECRET", "SSH_AUTH_SOCK", "AWS_SECRET_ACCESS_KEY",
                       "CREDENTIALS_DIRECTORY", "GOOGLE_API_KEY", "CREW_GEMINI_MAX_OUTPUT_TOKENS"):
            self.assertNotIn(leaked, env)

    def test_launcher_passes_exactly_the_given_environment_to_the_process(self):
        captured = {}
        class FakeProcess:
            pid = 4242
            def poll(self): return None
        def fake_popen(argv, **kw):
            captured.update(kw, argv=argv)
            return FakeProcess()
        upload_dir = self.tmp / "uploads"
        upload_dir.mkdir()
        workbook = upload_dir / "w.xlsx"
        workbook.write_bytes(b"PK\x03\x04data")
        import hashlib
        digest = hashlib.sha256(workbook.read_bytes()).hexdigest()
        env = {"PATH": "/usr/bin", "GEMINI_API_KEY": FAKE_KEY}
        with patch.object(run_launcher, "UPLOADS_DIR", upload_dir), patch.object(run_launcher, "LOGS_DIR", self.tmp / "logs"), \
                patch.object(run_launcher, "_popen", fake_popen), \
                patch.dict(os.environ, {"OAUTH_CLIENT_SECRET": "s3cret"}):
            info = run_launcher.launch({"path": str(workbook), "sha256": digest, "name": "w.xlsx"},
                                       runs_root=self.tmp / "runs", env=env)
        self.assertEqual(captured["env"], env)                  # nothing inherited, nothing added
        self.assertNotIn("OAUTH_CLIENT_SECRET", captured["env"])
        self.assertEqual(info["pid"], 4242)

    def test_default_launch_behaviour_is_unchanged(self):
        captured = {}
        class FakeProcess:
            pid = 1
            def poll(self): return None
        upload_dir = self.tmp / "uploads"
        upload_dir.mkdir()
        workbook = upload_dir / "w.xlsx"
        workbook.write_bytes(b"PK\x03\x04data")
        import hashlib
        digest = hashlib.sha256(workbook.read_bytes()).hexdigest()
        with patch.object(run_launcher, "UPLOADS_DIR", upload_dir), patch.object(run_launcher, "LOGS_DIR", self.tmp / "logs"), \
                patch.object(run_launcher, "_popen", lambda argv, **kw: captured.update(kw) or FakeProcess()), \
                patch.dict(os.environ, {"SOME_PARENT_VARIABLE": "inherited"}):
            run_launcher.launch({"path": str(workbook), "sha256": digest, "name": "w.xlsx"}, runs_root=self.tmp / "runs")
        self.assertEqual(captured["env"]["SOME_PARENT_VARIABLE"], "inherited")
        self.assertEqual(captured["env"]["PYTHONUTF8"], "1")

    def test_make_launcher_hands_the_key_only_to_the_child(self):
        cfg = load_worker_config(self.config(), self.subs())
        seen = {}
        with patch.object(run_launcher, "launch", lambda upload, **kw: seen.update(kw) or {"run_id": "x", "pid": 1}), \
                patch.dict(os.environ, {"OAUTH_CLIENT_SECRET": "s3cret"}):
            worker.make_launcher(cfg, FAKE_KEY)({"path": "p"})
            self.assertNotEqual(os.environ.get("GEMINI_API_KEY"), FAKE_KEY)   # (a developer .env may be loaded by other tests)
        self.assertEqual(seen["env"]["GEMINI_API_KEY"], FAKE_KEY)
        self.assertNotIn("OAUTH_CLIENT_SECRET", seen["env"])
        self.assertEqual(seen["runs_root"], cfg.live.runs_root)

    def test_worker_wiring_uses_private_state_folders(self):
        cfg = load_worker_config(self.config(), self.subs())
        saved = (run_launcher.UPLOADS_DIR, run_launcher.LOGS_DIR)
        self.addCleanup(lambda: (setattr(run_launcher, "UPLOADS_DIR", saved[0]), setattr(run_launcher, "LOGS_DIR", saved[1])))
        service, supervisor = worker.build_service(cfg, launch_fn=lambda u: {"run_id": "x"})
        self.assertEqual(run_launcher.UPLOADS_DIR, cfg.state_dir / "uploads")
        self.assertEqual(run_launcher.LOGS_DIR, cfg.state_dir / "logs")
        self.assertTrue(cfg.ledger_path.exists())
        self.assertEqual(supervisor.timeout_s, 300)

    def test_main_refuses_to_start_without_a_credential_or_on_windows(self):
        argv = ["--config", str(self.config()), "--allowed-subs", str(self.subs())]
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CREDENTIALS_DIRECTORY", None)
            self.assertEqual(worker.main(argv), 2)


class Phase2StaticSafetyTests(unittest.TestCase):
    LIVE = ROOT / "live"

    def source(self, name):
        return (self.LIVE / name).read_text(encoding="utf-8")

    def test_no_shell_eval_or_upload_in_any_phase2_module(self):
        for name in ("ipc.py", "worker.py", "web_client.py", "live_app.py", "config.py", "catalog.py"):
            text = self.source(name)
            for needle in ("shell=True", "os.system", "eval(", "exec(", "file_uploader", "pickle"):
                self.assertNotIn(needle, text, (name, needle))

    def test_the_worker_never_writes_the_key_into_its_own_environment(self):
        text = self.source("worker.py")
        for needle in ("os.environ[", "putenv", "environ.update", "environ.setdefault", "load_dotenv"):
            self.assertNotIn(needle, text, needle)

    def test_the_token_verifier_is_worker_only_and_the_web_side_cannot_import_it(self):
        import ast
        for name in ("live_app.py", "web_client.py", "ipc.py", "catalog.py"):
            tree = ast.parse(self.source(name))
            imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} |                        {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
            self.assertNotIn("live.oidc", imported, name)
            self.assertNotIn("cryptography", {(m or "").split(".")[0] for m in imported}, name)
        text = self.source("oidc.py")
        for needle in ("print(", "logging", "write(", "pickle", "eval(", "exec(", "os.environ", "sqlite"):
            self.assertNotIn(needle, text, needle)                       # a token is never logged, written or echoed
        import re
        self.assertIsNone(re.search(r"(?<!url)open\(", text))

    def test_the_socket_layer_cannot_reach_the_network_or_a_model(self):
        import ast
        tree = ast.parse(self.source("ipc.py"))
        roots = {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        roots |= {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        self.assertEqual(roots & {"requests", "urllib", "http", "httpx", "crewai", "google", "litellm", "subprocess"}, set())


if __name__ == "__main__":
    unittest.main()
