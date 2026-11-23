"""Shared helpers for the V3 live-core tests. Offline: no Gemini, no network, no real pipeline unless a
test explicitly uses the scripted-LLM harness."""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
import env_hygiene  # noqa: F401  (sanitize the process before anything else is imported)
import hashlib
import itertools
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import base64  # noqa: E402
import json  # noqa: E402

from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding, rsa  # noqa: E402

from src import run_launcher  # noqa: E402
from live.control import KillSwitch  # noqa: E402
from live.ipc import WorkerClient, encode_response, handle_request  # noqa: E402
from live.ledger import Ledger  # noqa: E402
from live.oidc import JwksProvider, TokenVerifier  # noqa: E402
from live.scenarios import Scenario, ScenarioRegistry  # noqa: E402
from live.service import LiveConfig, LiveService  # noqa: E402
from live.supervisor import Supervisor  # noqa: E402

CLIENT_ID = "test-client-id.apps.googleusercontent.com"
OWNER = "100000000000000000001"
OTHER = "100000000000000000002"
NOW = 1_800_000_000.0   # a fixed instant: 2027-01-15 08:00:00 UTC


# ---- offline stand-in for Google's signing keys: a throwaway RSA key made for the test process ------------------
TEST_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)   # a different (attacker's) key
TEST_KID = "test-key-1"


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def jwks_for(key, kid=TEST_KID) -> dict:
    numbers = key.public_key().public_numbers()
    size = (numbers.n.bit_length() + 7) // 8
    return {"keys": [{"kty": "RSA", "alg": "RS256", "use": "sig", "kid": kid,
                      "n": b64u(numbers.n.to_bytes(size, "big")), "e": b64u(numbers.e.to_bytes(3, "big"))}]}


def sign_token(payload: dict, *, key=TEST_KEY, kid=TEST_KID, header=None) -> str:
    head = {"alg": "RS256", "typ": "JWT", "kid": kid, **(header or {})}
    signing = f"{b64u(json.dumps(head).encode())}.{b64u(json.dumps(payload).encode())}"
    signature = key.sign(signing.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing}.{b64u(signature)}"


def make_verifier(now):
    return TokenVerifier(JwksProvider(lambda: jwks_for(TEST_KEY), now=now), audience=CLIENT_ID, now=now)


def transport_for(service, verifier=None):
    """In-process transport: exactly the bytes the socket would carry, through the real dispatcher and verifier."""
    verifier = verifier or make_verifier(service._now)

    def transport(message):
        return json.loads(encode_response(handle_request(service, json.dumps(message).encode(), verifier)))
    return transport


class ClaimsClient:
    """Test convenience: takes a claims dict, signs it into a genuine RS256 token, and calls the worker with it."""

    def __init__(self, transport):
        self.raw = WorkerClient(transport)

    def ping(self):
        return self.raw.ping()

    def start(self, claims, scenario_id, request_token):
        return self.raw.start(sign_token(claims), scenario_id, request_token)

    def status(self, claims, run_id):
        return self.raw.status(sign_token(claims), run_id)

    def latest(self, claims):
        return self.raw.latest(sign_token(claims))

    def usage(self, claims):
        return self.raw.usage(sign_token(claims))


class Clock:
    def __init__(self, start=NOW):
        self.t = float(start)

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def claims(sub=OWNER, **overrides):
    base = {"is_logged_in": True, "iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": sub,
            "email": "someone@example.com", "email_verified": True, "iat": NOW - 60, "exp": NOW + 3000}
    base.update(overrides)
    return base


_counter = itertools.count(1)


def token() -> str:
    return f"tok{next(_counter):013d}abcdef"   # 22 chars, valid format


def run_id() -> str:
    return f"20270115T08{next(_counter) % 10000:04d}Z_{next(_counter):08x}"


def fake_xlsx(label: str) -> bytes:
    return b"PK\x03\x04" + label.encode() * 50


class Fixture:
    """A complete, isolated live core in a temp folder with synthetic scenario workbooks."""

    def __init__(self, test, *, daily=2, budget=5.0, reserve=0.5, timeout=300, allowed=(OWNER,), launch_fn=None,
                 scenario_bytes=None, classify_state="RUNNING"):
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.clock = Clock()
        (self.base / "runs").mkdir()
        (self.base / "scenarios").mkdir()
        scenario_bytes = scenario_bytes or {"pass": fake_xlsx("pass"), "fail": fake_xlsx("fail")}
        scenarios = []
        for sid, data in scenario_bytes.items():
            (self.base / "scenarios" / f"{sid}.xlsx").write_bytes(data)
            scenarios.append(Scenario(sid, sid.upper(), f"{sid}.xlsx", hashlib.sha256(data).hexdigest(),
                                      "PASS" if sid == "pass" else "FAIL", ""))
        self.registry = ScenarioRegistry(scenarios, self.base / "scenarios")
        for patcher in (patch.object(run_launcher, "RUNS_ROOT", self.base / "runs"),
                        patch.object(run_launcher, "UPLOADS_DIR", self.base / "uploads"),
                        patch.object(run_launcher, "LOGS_DIR", self.base / "logs")):
            patcher.start()
            test.addCleanup(patcher.stop)
        self.ledger = Ledger(self.base / "ledger.sqlite", now=self.clock)
        self.kill = KillSwitch(self.base / "KILL")
        self.verifier = make_verifier(self.clock)
        self.states = {}                      # run_id -> state returned by the fake run_state
        self.default_state = classify_state
        self.alive = set()                    # pids the fake liveness check reports as alive
        self.supervisor = Supervisor(
            self.ledger, self.kill, timeout_s=timeout, runs_root=self.base / "runs", now=self.clock,
            terminate=self._terminate, run_state=lambda rid, runs_root=None: {"state": self.states.get(rid, self.default_state)},
            is_alive=lambda pid: pid in self.alive)
        self.terminated = []
        self.terminate_succeeds = True
        self.config = LiveConfig(allowed_subs=frozenset(allowed), client_id=CLIENT_ID, runs_root=self.base / "runs",
                                 daily_start_limit=daily, monthly_budget_usd=budget, reserve_per_start_usd=reserve,
                                 run_timeout_s=timeout)
        self.launches = []
        self._lock = threading.Lock()
        self.service = LiveService(self.config, self.ledger, self.registry, self.kill, self.supervisor,
                                   launch_fn=launch_fn or self.default_launch, now=self.clock)

    def _terminate(self, pid):
        self.terminated.append(pid)
        return self.terminate_succeeds

    def default_launch(self, upload):
        with self._lock:
            rid, pid = run_id(), 50000 + len(self.launches)
            self.launches.append(upload)
            self.alive.add(pid)
            return {"run_id": rid, "pid": pid}

    def finish_active(self, state="FINISHED"):
        """Pretend the active run ended normally (as the supervisor would record)."""
        for row in self.ledger.active_rows():
            if row["state"] == "STARTED":
                self.ledger.finish(row["request_token"], state)
