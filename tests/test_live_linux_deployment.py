"""V3 Phase 2 deployment review on Linux: systemd's own analysis of the units, the filesystem permission model, and
the credential boundary, exercised with real operating-system users. Skipped where the tools or privileges are absent.
Nothing is installed: temporary folders only, and standard system accounts (nobody, daemon, www-data) as stand-ins."""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "systemd"
SYSTEMD = shutil.which("systemd-analyze")
IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0
PYTHON3 = "/usr/bin/python3"
sys.path.insert(0, str(ROOT))


def accounts_exist(*names):
    import pwd
    try:
        return all(pwd.getpwnam(n) for n in names)
    except (KeyError, ImportError):
        return False


@unittest.skipUnless(SYSTEMD and os.name == "posix", "systemd-analyze is not available")
class SystemdAnalysisTests(unittest.TestCase):
    UNITS = ("live-worker.service", "live-web.service")

    def test_units_verify_apart_from_the_binaries_that_are_not_installed_here(self):
        result = subprocess.run([SYSTEMD, "verify", "--man=no", *(str(DEPLOY / u) for u in self.UNITS)],
                                capture_output=True, text=True)
        problems = [line for line in (result.stdout + result.stderr).splitlines() if line.strip()
                    and "is not executable" not in line and "No such file or directory" not in line]
        self.assertEqual(problems, [])

    def test_exposure_scores_stay_in_the_ok_range_and_key_hardening_is_recognised(self):
        for unit in self.UNITS:
            out = subprocess.run([SYSTEMD, "security", "--offline=true", "--no-pager", str(DEPLOY / unit)],
                                 capture_output=True, text=True).stdout
            score = float(re.search(r"Overall exposure level for \S+: ([0-9.]+)", out).group(1))
            self.assertLess(score, 4.0, f"{unit}: {score}")          # systemd: below 4.0 is "OK"
            passed = {m.group(1) for m in re.finditer(r"^✓ (\S+)", out, re.M)}
            for directive in ("NoNewPrivileges=", "ProtectSystem=", "ProtectHome=", "PrivateTmp=", "PrivateDevices=",
                              "ProtectKernelTunables=", "RestrictSUIDSGID=", "LockPersonality=", "ProtectProc=",
                              "RestrictNamespaces=", "User=/DynamicUser="):
                self.assertTrue(any(p.startswith(directive.rstrip("=")) for p in passed), (unit, directive))
            self.assertNotIn("CapabilityBoundingSet=~CAP_SYS_ADMIN                        Service has administrator", out)


@unittest.skipUnless(IS_ROOT and accounts_exist("nobody", "daemon", "www-data") and os.path.exists(PYTHON3),
                     "needs Linux, root, and the standard accounts nobody/daemon/www-data (run as root in WSL)")
class PermissionModelTests(unittest.TestCase):
    """The runbook's permission model, with nobody = the web account, daemon = the worker account, www-data = a stranger."""
    WEB, WORKER, STRANGER = "nobody", "daemon", "www-data"
    WEB_GROUP = "nogroup"           # nobody's primary group (stands in for live-ipc, to which web and worker both belong)

    def setUp(self):
        import grp
        import pwd
        base = Path(tempfile.mkdtemp(prefix="v3perm_", dir="/var/tmp"))
        self.addCleanup(shutil.rmtree, base, True)
        os.chmod(base, 0o755)
        self.base = base
        uid = lambda n: pwd.getpwnam(n).pw_uid           # noqa: E731
        gid = lambda n: pwd.getpwnam(n).pw_gid           # noqa: E731
        layout = {  # path -> (mode, owner, group, is_dir)
            "etc_live": (0o750, "root", self.WORKER, True), "etc_live/credentials": (0o700, "root", "root", True),
            "etc_live/credentials/gemini_api_key": (0o600, "root", "root", False),
            "etc_live/config.json": (0o640, "root", self.WORKER, False),
            "etc_live/allowed_subs.json": (0o640, "root", self.WORKER, False),
            "etc_live_web": (0o750, "root", self.WEB_GROUP, True), "etc_live_web/credentials": (0o700, "root", "root", True),
            "etc_live_web/credentials/oauth_client_secret": (0o600, "root", "root", False),
            "etc_live_web/web.json": (0o640, "root", self.WEB_GROUP, False),
            "var_lib_live": (0o700, self.WORKER, self.WORKER, True), "var_lib_live/ledger.sqlite": (0o600, self.WORKER, self.WORKER, False),
            "srv_scenarios": (0o750, self.WORKER, self.WORKER, True),
            "run_live": (0o750, self.WORKER, self.WEB_GROUP, True),
            "opt_app": (0o755, "root", "root", True)}
        for rel, (mode, owner, group, is_dir) in layout.items():
            path = base / rel
            path.mkdir() if is_dir else path.write_text("x", encoding="utf-8")
            shutil.chown(path, owner, group)
            path.chmod(mode)
        self.sock = base / "run_live" / "w.sock"

    def can(self, user, action, path):
        code = {"read": "open(p,'rb').read(1)", "list": "__import__('os').listdir(p)",
                "write": "open(p+'/x_probe','w').write('x')" if (self.base / path).is_dir() else "open(p,'a').write('x')"}[action]
        result = subprocess.run(["runuser", "-u", user, "--", PYTHON3, "-c", f"p={str(self.base / path)!r}\n{code}"],
                                capture_output=True, text=True)
        return result.returncode == 0

    def test_the_web_account_cannot_reach_the_gemini_key_worker_state_or_configuration(self):
        for path, action in (("etc_live/credentials/gemini_api_key", "read"), ("etc_live", "list"),
                             ("etc_live/config.json", "read"), ("etc_live/allowed_subs.json", "read"),
                             ("var_lib_live", "list"), ("var_lib_live/ledger.sqlite", "read"),
                             ("srv_scenarios", "list"), ("var_lib_live", "write"), ("opt_app", "write")):
            self.assertFalse(self.can(self.WEB, action, path), (path, action))

    def test_the_worker_account_cannot_reach_the_web_secrets(self):
        for path, action in (("etc_live_web/credentials/oauth_client_secret", "read"), ("etc_live_web", "list"),
                             ("etc_live_web/web.json", "read"), ("opt_app", "write")):
            self.assertFalse(self.can(self.WORKER, action, path), (path, action))

    def test_each_account_can_use_what_it_needs(self):
        self.assertTrue(self.can(self.WEB, "read", "etc_live_web/web.json"))
        self.assertTrue(self.can(self.WORKER, "read", "etc_live/config.json"))
        self.assertTrue(self.can(self.WORKER, "read", "etc_live/allowed_subs.json"))
        self.assertTrue(self.can(self.WORKER, "write", "var_lib_live"))
        self.assertTrue(self.can(self.WORKER, "list", "srv_scenarios"))
        self.assertFalse(self.can(self.STRANGER, "read", "etc_live/config.json"))
        self.assertFalse(self.can(self.STRANGER, "list", "var_lib_live"))

    def test_the_socket_admits_the_web_account_only(self):
        from live import ipc

        class Service:
            class kill_switch:
                @staticmethod
                def engaged():
                    return False
        import grp
        import pwd
        ipc_gid = grp.getgrnam(self.WEB_GROUP).gr_gid
        os.setgroups([ipc_gid])                                 # the worker belongs to the shared socket group
        os.setegid(pwd.getpwnam(self.WORKER).pw_gid)
        os.seteuid(pwd.getpwnam(self.WORKER).pw_uid)            # bind as the worker account, as in production
        try:
            server = ipc.SocketServer(Service(), self.sock, verifier=None, allowed_uids={pwd.getpwnam(self.WEB).pw_uid},
                                      group=ipc_gid)
            server.bind()
        finally:
            os.seteuid(0)
            os.setegid(0)
            os.setgroups([])
        import threading
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.stop(), thread.join(2)))
        self.assertEqual(self.sock.stat().st_mode & 0o777, 0o660)
        probe = ("import socket,sys;s=socket.socket(socket.AF_UNIX);s.settimeout(3);s.connect(%r);s.sendall(b'{\"op\":\"ping\"}');"
                 "s.shutdown(socket.SHUT_WR);print(s.recv(200).decode())" % str(self.sock))
        web = subprocess.run(["runuser", "-u", self.WEB, "--", PYTHON3, "-c", probe], capture_output=True, text=True)
        stranger = subprocess.run(["runuser", "-u", self.STRANGER, "--", PYTHON3, "-c", probe], capture_output=True, text=True)
        self.assertEqual(json.loads(web.stdout), {"ok": True, "kill_switch_engaged": False}, web.stderr)
        self.assertNotEqual(stranger.returncode, 0)                 # filesystem permissions stop a stranger before any parsing

    def test_the_socket_peer_check_refuses_a_group_member_who_is_not_the_web_account(self):
        import pwd
        from live import ipc

        class Service:
            class kill_switch:
                @staticmethod
                def engaged():
                    return False
        (self.base / "open").mkdir(mode=0o755)
        self.sock = self.base / "open" / "w.sock"                  # a folder everyone may traverse
        server = ipc.SocketServer(Service(), self.sock, verifier=None, allowed_uids={pwd.getpwnam(self.WEB).pw_uid}, mode=0o666)
        server.bind()                                              # deliberately world-connectable: only the uid check remains
        import threading
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.stop(), thread.join(2)))
        probe = ("import socket;s=socket.socket(socket.AF_UNIX);s.settimeout(3);s.connect(%r);s.sendall(b'{\"op\":\"ping\"}');"
                 "s.shutdown(socket.SHUT_WR);print(s.recv(200).decode())" % str(self.sock))
        stranger = subprocess.run(["runuser", "-u", self.STRANGER, "--", PYTHON3, "-c", probe], capture_output=True, text=True)
        self.assertEqual(json.loads(stranger.stdout), {"ok": False, "error": "DENIED"})

    def test_the_worker_refuses_to_run_as_root(self):
        from live import worker
        cfg, subs = self.base / "etc_live/config.json", self.base / "etc_live/allowed_subs.json"
        self.assertEqual(worker.main(["--config", str(cfg), "--allowed-subs", str(subs)]), 2)

    def test_configuration_file_permission_checks_agree_with_the_runbook(self):
        from live.config import ConfigError, check_private_file
        for rel in ("etc_live/config.json", "etc_live/allowed_subs.json", "etc_live_web/web.json",
                    "etc_live/credentials/gemini_api_key"):
            check_private_file(self.base / rel)
        loose = self.base / "etc_live/config.json"
        loose.chmod(0o644)
        with self.assertRaises(ConfigError):
            check_private_file(loose)


@unittest.skipUnless(os.name == "posix" and not (ROOT / ".env").exists(),
                     "runs only in a clean checkout without a .env (never reads a developer's credentials)")
class DirectPipelineLaunchTests(unittest.TestCase):
    def test_a_process_without_the_key_cannot_get_the_pipeline_past_its_preflight(self):
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8", "HOME": tempfile.gettempdir()}
        result = subprocess.run([sys.executable, "-I", str(ROOT / "src" / "full_pipeline.py"), "--check"],
                                capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=120)
        text = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, text[-300:])
        self.assertRegex(text.lower(), r"gemini api key is not configured|key")


if __name__ == "__main__":
    unittest.main()
