"""The execution worker: the only process that ever holds the Gemini key.

Responsibilities (all enforced here, at the execution boundary, never in the browser-facing process):
  - read the Gemini key from the systemd credential directory ($CREDENTIALS_DIRECTORY/gemini_api_key);
    the key is NEVER placed in this process's own os.environ, only in the child pipeline's explicit environment
  - refuse to start if a .env file exists in the checkout (or above it), if configuration files are loosely
    protected, or if the pipeline's run folder is not the configured one
  - serve the Unix-socket protocol (live.ipc) with authorization, quotas, idempotency and the kill switch
    applied by LiveService, and run the Supervisor loop for timeout and kill-switch enforcement
  - start the existing pipeline (src/full_pipeline.py, Crew 1 -> independent gate -> Crew 2) unchanged
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
from pathlib import Path

from src import run_launcher

from live.config import ConfigError, WorkerConfig, load_worker_config
from live.control import KillSwitch
from live.ipc import SocketServer
from live.ledger import Ledger
from live.oidc import JwksProvider, TokenVerifier
from live.scenarios import DEFAULT_SCENARIOS, ScenarioRegistry
from live.service import LiveService
from live.supervisor import Supervisor

KEY_FILE = "gemini_api_key"
MODEL_ENV = "CREW1_GEMINI_MODEL"
# The child pipeline gets exactly these variables (plus the key), never a copy of the worker's environment.
CHILD_ENV_PASSTHROUGH = ("PATH", "LANG", "LC_ALL", "TZ", MODEL_ENV, "CREW2_GEMINI_MODEL")
CHILD_ENV_FIXED = {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "CREW_RECORD_TOKEN_USAGE": "1",
                   "CREWAI_DISABLE_TELEMETRY": "true", "OTEL_SDK_DISABLED": "true"}
# Deliberately absent: CREW_GEMINI_MAX_OUTPUT_TOKENS (the cap stays unset until a supervised run measures usage).
KEY_VARIABLES = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
ROOT = Path(__file__).resolve().parents[1]


class StartupRefused(RuntimeError):
    """A deployment precondition failed; the worker must not serve."""


def read_credential(environ=None) -> str:
    """Read the key from the systemd credential directory. Returns it; never stores it anywhere shared."""
    environ = os.environ if environ is None else environ
    directory = environ.get("CREDENTIALS_DIRECTORY")
    if not directory:
        raise StartupRefused("CREDENTIALS_DIRECTORY is not set: the Gemini key must come from a systemd credential")
    path = Path(directory) / KEY_FILE
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        raise StartupRefused("The Gemini credential file is missing or unreadable") from None
    if not 20 <= len(value) <= 256 or any(c.isspace() for c in value):
        raise StartupRefused("The Gemini credential does not look like an API key")
    return value


def assert_no_dotenv(root: Path = ROOT) -> None:
    """The pipeline would load a .env (here or in a parent folder) and could silently use a developer key."""
    for folder in (root, *root.resolve().parents):
        if (folder / ".env").exists():
            raise StartupRefused(f"A .env file exists at {folder.name or folder}: the deployment must not contain one")


def assert_run_folder_matches(config: WorkerConfig, root: Path = ROOT) -> None:
    """The pipeline always writes to <checkout>/runs; that must be the configured run folder (a symlink is fine)."""
    pipeline_runs = (root / "runs").resolve()
    if pipeline_runs != config.live.runs_root.resolve():
        raise StartupRefused("<checkout>/runs must resolve to <state_dir>/runs (see the deployment guide)")


def build_child_env(api_key: str, environ=None, *, home: str | None = None) -> dict:
    """The complete environment for the pipeline child: an allow-list, not an inherited copy."""
    environ = os.environ if environ is None else environ
    env = {name: environ[name] for name in CHILD_ENV_PASSTHROUGH if name in environ}
    env.update(CHILD_ENV_FIXED)
    if home:
        env["HOME"] = home
    env["GEMINI_API_KEY"] = api_key
    return env


def make_launcher(config: WorkerConfig, api_key: str):
    """launch_fn for LiveService: the existing launcher with an explicit child environment."""
    home = str(config.state_dir / "home")
    Path(home).mkdir(parents=True, exist_ok=True)

    def launch(upload):
        return run_launcher.launch(upload, runs_root=config.live.runs_root, env=build_child_env(api_key, home=home))
    return launch


def build_service(config: WorkerConfig, *, launch_fn, now=None, scenarios=DEFAULT_SCENARIOS):
    """Wire the live core from a validated configuration. `launch_fn` is injected (a fake in tests)."""
    config.state_dir.mkdir(parents=True, exist_ok=True)
    config.live.runs_root.mkdir(parents=True, exist_ok=True)
    run_launcher.UPLOADS_DIR = config.state_dir / "uploads"
    run_launcher.LOGS_DIR = config.state_dir / "logs"
    kwargs = {} if now is None else {"now": now}
    ledger = Ledger(config.ledger_path, **kwargs)
    kill = KillSwitch(config.kill_switch_path)
    supervisor = Supervisor(ledger, kill, timeout_s=config.live.run_timeout_s, runs_root=config.live.runs_root, **kwargs)
    registry = ScenarioRegistry(scenarios, config.scenarios_dir)
    return LiveService(config.live, ledger, registry, kill, supervisor, launch_fn=launch_fn, **kwargs), supervisor


def web_uid(name: str) -> int:
    import pwd
    try:
        return pwd.getpwnam(name).pw_uid
    except KeyError:
        raise StartupRefused(f"Service account {name!r} does not exist") from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="V3 live execution worker")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--allowed-subs", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if os.name == "nt":
            raise StartupRefused("The worker serves a Unix socket and runs on Linux only")
        if os.geteuid() == 0:
            raise StartupRefused("The worker must not run as root")
        config = load_worker_config(args.config, args.allowed_subs)
        assert_no_dotenv()
        assert_run_folder_matches(config)
        api_key = read_credential()
        for name in KEY_VARIABLES:   # a key in our own environment would defeat the boundary
            if name in os.environ:
                raise StartupRefused(f"{name} must not be set in the worker's environment")
        service, supervisor = build_service(config, launch_fn=make_launcher(config, api_key))
        group = None
        if config.socket_group:
            import grp
            try:
                group = grp.getgrnam(config.socket_group).gr_gid
            except KeyError:
                raise StartupRefused(f"Group {config.socket_group!r} does not exist") from None
        verifier = TokenVerifier(JwksProvider(), audience=config.live.client_id, issuers=config.live.issuers)
        server = SocketServer(service, config.socket_path, verifier, allowed_uids={web_uid(config.web_user)}, group=group)
    except (StartupRefused, ConfigError) as exc:
        print(f"worker refused to start: {exc}", file=sys.stderr)
        return 2
    stop = threading.Event()
    thread = threading.Thread(target=supervisor.run_forever, kwargs={"stop": stop}, daemon=True, name="supervisor")
    thread.start()
    server.bind()
    try:
        server.serve_forever()
    finally:
        stop.set()
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
