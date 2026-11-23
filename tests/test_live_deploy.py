"""V3 Phase 2: the prepared (not installed) deployment files. Static checks only; nothing is installed or run."""
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
sys.path.insert(0, str(ROOT))
from live.config import load_allowed_subs, load_worker_config  # noqa: E402


def parse_unit(path):
    sections, current = {}, None
    for raw in Path(path).read_text(encoding="utf-8").replace("\\\n", " ").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            current = sections.setdefault(line.strip("[]"), {})
        elif "=" in line and current is not None:
            key, value = line.split("=", 1)
            current.setdefault(key.strip(), []).append(value.strip())
    return sections


WORKER = parse_unit(DEPLOY / "systemd" / "live-worker.service")["Service"]
WEB = parse_unit(DEPLOY / "systemd" / "live-web.service")["Service"]


def load_renderer():
    spec = importlib.util.spec_from_file_location("render_web_secrets", DEPLOY / "bin" / "render-web-secrets.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class UnitSeparationTests(unittest.TestCase):
    def test_only_the_worker_receives_the_gemini_credential(self):
        self.assertTrue(any(v.startswith("gemini_api_key:") for v in WORKER["LoadCredential"]))
        self.assertFalse(any("gemini" in v.lower() for v in WEB.get("LoadCredential", [])))
        web_text = (DEPLOY / "systemd" / "live-web.service").read_text(encoding="utf-8")
        executable = "\n".join(l for l in web_text.splitlines() if not l.lstrip().startswith("#"))
        self.assertNotIn("GEMINI", executable.upper().replace("GEMINI_API_KEY_ABSENT", ""))
        for unit in (WORKER, WEB):
            self.assertNotIn("EnvironmentFile", unit)            # EnvironmentFile is not a safe route for secrets
            self.assertFalse(any(re.search(r"(?i)api[_-]?key=|secret=|password=|token=", v) for v in unit.get("Environment", [])))

    def test_services_run_as_different_unprivileged_accounts(self):
        self.assertEqual((WORKER["User"], WEB["User"]), (["live-worker"], ["live-web"]))
        self.assertNotIn("root", WORKER["User"] + WEB["User"])
        self.assertIn("live-ipc", WORKER["SupplementaryGroups"][0])
        self.assertIn("live-ipc", WEB["SupplementaryGroups"][0])

    def test_the_web_unit_cannot_see_worker_state(self):
        inaccessible = WEB["InaccessiblePaths"][0].split()
        for path in ("/var/lib/live", "/srv/live", "/etc/live"):
            self.assertIn(path, inaccessible)
        self.assertTrue(WEB["ReadWritePaths"][0].split() == ["/run/live-web"])
        self.assertIn("/etc/live-web", WORKER["InaccessiblePaths"][0].split())

    def test_web_listens_on_localhost_only(self):
        exec_start = WEB["ExecStart"][0]
        self.assertIn("--server.address 127.0.0.1", exec_start)
        self.assertNotIn("0.0.0.0", exec_start)
        self.assertIn("--server.enableXsrfProtection true", exec_start)
        self.assertEqual(WEB["Environment"].count("LIVE_SOCKET_PATH=/run/live/worker.sock"), 1)

    def test_resource_limits_protect_the_small_shared_host(self):
        def mb(value):
            return int(value.rstrip("M"))
        self.assertLessEqual(mb(WORKER["MemoryMax"][0]) + mb(WEB["MemoryMax"][0]), 2048)    # leaves >1.7 GB for n8n/Traefik/OS
        for unit in (WORKER, WEB):
            self.assertTrue(mb(unit["MemoryHigh"][0]) < mb(unit["MemoryMax"][0]))
            self.assertEqual(unit["MemorySwapMax"], ["0"])
            self.assertLessEqual(int(unit["TasksMax"][0]), 128)
            self.assertLessEqual(int(unit["CPUQuota"][0].rstrip("%")), 80)
            self.assertGreaterEqual(int(unit["OOMScoreAdjust"][0]), 400)                    # killed before n8n
        for name in ("NoNewPrivileges", "PrivateTmp", "ProtectSystem", "ProtectHome", "SystemCallFilter", "PrivateDevices"):
            self.assertIn(name, WORKER)
            self.assertIn(name, WEB)
        self.assertEqual(WORKER["ProtectSystem"], ["strict"])
        for unit in (WORKER, WEB):
            self.assertEqual(unit["CapabilityBoundingSet"], [""])      # empty: no capabilities
            self.assertEqual(unit["RemoveIPC"], ["yes"])

    def test_nothing_is_activated_or_touches_other_services(self):
        for path in DEPLOY.rglob("*"):
            if path.is_file() and path.suffix != ".pyc":
                text = path.read_text(encoding="utf-8")
                executable = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
                for forbidden in ("ufw ", "iptables", "nft ", "docker ", "traefik", "n8n", "sshd", "systemctl enable",
                                  "systemctl start", "tailscale funnel"):
                    self.assertNotIn(forbidden, executable.lower(), f"{path.name}: {forbidden}")
        install = {unit.get("WantedBy", [""])[0] for unit in
                   (parse_unit(DEPLOY / "systemd" / n)["Install"] for n in ("live-worker.service", "live-web.service"))}
        self.assertEqual(install, {"multi-user.target"})   # a declaration only; enabling is a separate, approved step

    def test_example_configuration_is_valid_but_contains_placeholders_and_no_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = json.loads((DEPLOY / "config" / "config.example.json").read_text(encoding="utf-8"))
            self.assertIn("REPLACE", cfg["client_id"])
            cfg["client_id"] = "x.apps.googleusercontent.com"
            path = Path(tmp) / "c.json"
            path.write_text(json.dumps(cfg), encoding="utf-8")
            subs = Path(tmp) / "s.json"
            subs.write_text(json.dumps({"allowed_subs": ["100000000000000000001"]}), encoding="utf-8")
            if os.name != "nt":
                path.chmod(0o640)
                subs.chmod(0o640)
            loaded = load_worker_config(path, subs)
            self.assertEqual(str(loaded.kill_switch_path).replace("\\", "/"), "/etc/live/KILL")
            self.assertEqual(loaded.live.run_timeout_s, 300)
        for file in (DEPLOY / "config").glob("*.json"):
            self.assertNotRegex(file.read_text(encoding="utf-8"), r"AIza|GOCSPX|eyJ")
        with self.assertRaises(Exception):
            load_allowed_subs(DEPLOY / "config" / "allowed_subs.example.json")   # the placeholder cannot be used as is


class SecretRenderingTests(unittest.TestCase):
    def setUp(self):
        self.module = load_renderer()

    def test_render_is_valid_toml_with_the_auth_section(self):
        import tomllib
        text = self.module.render({"client_id": "cid.apps.googleusercontent.com",
                                   "redirect_uri": "https://host.tailnet.ts.net:8443/oauth2callback"},
                                  'client"secret\\value', "c" * 40)
        data = tomllib.loads(text)["auth"]
        self.assertEqual(data["client_secret"], 'client"secret\\value')
        self.assertEqual(data["expose_tokens"], "id")            # the worker verifies the raw ID token itself
        self.assertEqual(data["redirect_uri"], "https://host.tailnet.ts.net:8443/oauth2callback")
        self.assertTrue(data["server_metadata_url"].startswith("https://accounts.google.com/"))

    def test_unsafe_inputs_are_refused(self):
        good = {"client_id": "c", "redirect_uri": "https://h.ts.net:8443/oauth2callback"}
        for config, secret, cookie in [({**good, "redirect_uri": "http://h/oauth2callback"}, "s", "c" * 40),
                                       ({**good, "redirect_uri": "https://h/other"}, "s", "c" * 40),
                                       ({"client_id": "c"}, "s", "c" * 40), (good, "s", "short"),
                                       (good, "multi\nline", "c" * 40)]:
            with self.assertRaises(ValueError):
                self.module.render(config, secret, cookie)

    def test_the_script_writes_a_private_file_and_never_prints_a_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            creds = tmp / "creds"
            creds.mkdir()
            (creds / "oauth_client_secret").write_text("S3CRET-VALUE-XYZ\n", encoding="utf-8")
            (creds / "cookie_secret").write_text("k" * 48 + "\n", encoding="utf-8")
            cfg = tmp / "web.json"
            cfg.write_text(json.dumps({"client_id": "cid", "redirect_uri": "https://h.ts.net:8443/oauth2callback"}), encoding="utf-8")
            out = tmp / "run" / ".streamlit" / "secrets.toml"
            result = subprocess.run([sys.executable, "-I", str(DEPLOY / "bin" / "render-web-secrets.py"), "--config", str(cfg),
                                     "--out", str(out), "--credentials-dir", str(creds)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("S3CRET", result.stdout + result.stderr)
            if os.name != "nt":
                self.assertEqual(out.stat().st_mode & 0o777, 0o600)
            bad = subprocess.run([sys.executable, "-I", str(DEPLOY / "bin" / "render-web-secrets.py"), "--config", str(cfg),
                                  "--out", str(tmp / "x.toml"), "--credentials-dir", str(tmp / "nope")], capture_output=True, text=True)
            self.assertEqual(bad.returncode, 2)
            self.assertFalse((tmp / "x.toml").exists())


if __name__ == "__main__":
    unittest.main()
