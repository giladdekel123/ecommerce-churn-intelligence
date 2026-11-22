"""The execution core: the only place a live run can be started.

start() applies, in this order, and stops at the first failure:
  1. authorization (live.auth)               denied  -> nothing is recorded
  2. request validation                      scenario id must be in the closed registry; token well-formed
  3. kill switch                             engaged -> nothing is recorded
  4. idempotent re-attach                    a known token returns its existing outcome, never a second run
  5. scenario verification and staging       pinned SHA-256; unavailable -> nothing is recorded
  6. the atomic ledger gate                  busy / daily limit / monthly budget; reserves the cost
  7. launch via the existing run_launcher    (injected so tests need no subprocess or Gemini)
status() rebuilds a sanitized projection, only for the caller's own runs started through start().

Nothing here reads a secret, opens a socket or calls Gemini; the launcher it is given does the
actual process start, exactly as the existing application does.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from src import run_launcher
from src.dashboard_data import RUN_ID_RE

from live.auth import DEFAULT_MAX_SESSION_AGE_S, GOOGLE_ISSUERS, authorize
from live.control import KillSwitch
from live.ledger import Ledger, TOKEN_RE, usd_to_micro
from live.projection import project_run
from live.scenarios import ScenarioRegistry, ScenarioUnavailable, UnknownScenario
from live.supervisor import Supervisor


@dataclass(frozen=True)
class LiveConfig:
    allowed_subs: frozenset
    client_id: str
    runs_root: Path
    daily_start_limit: int = 2
    monthly_budget_usd: float = 5.00
    reserve_per_start_usd: float = 0.50   # conservative until a supervised run measures real cost
    run_timeout_s: int = 300              # recorded PASS run: about 77 s end to end
    max_session_age_s: int = DEFAULT_MAX_SESSION_AGE_S
    issuers: tuple = GOOGLE_ISSUERS


@dataclass(frozen=True)
class StartResult:
    status: str        # STARTED, REATTACHED, DENIED, DISABLED, DAILY_LIMIT, BUDGET, BUSY, INVALID_REQUEST,
                       # SCENARIO_UNAVAILABLE, LAUNCH_FAILED
    message: str
    run_id: str | None = None
    reason: str = field(default="", compare=False)  # for server logs only; never shown to the visitor


_MESSAGES = {
    "DENIED": "You are not authorized to start a live run.",
    "DISABLED": "Live execution is currently switched off.",
    "DAILY_LIMIT": "The daily limit of live runs has been reached.",
    "BUDGET": "The monthly live-run budget has been reached.",
    "BUSY": "A live run is already in progress.",
    "INVALID_REQUEST": "The request was not valid.",
    "SCENARIO_UNAVAILABLE": "That scenario is not available right now.",
    "LAUNCH_FAILED": "The run could not be started.",
}


class LiveService:
    def __init__(self, config: LiveConfig, ledger: Ledger, registry: ScenarioRegistry, kill_switch: KillSwitch,
                 supervisor: Supervisor, *, launch_fn: Callable | None = None, now=time.time):
        self.config, self.ledger, self.registry = config, ledger, registry
        self.kill_switch, self.supervisor, self._now = kill_switch, supervisor, now
        self._launch = launch_fn or (lambda upload: run_launcher.launch(upload, runs_root=config.runs_root))

    def _authorize(self, claims):
        return authorize(claims, allowed_subs=self.config.allowed_subs, client_id=self.config.client_id,
                         now=self._now(), issuers=self.config.issuers,
                         max_session_age_s=self.config.max_session_age_s)

    @staticmethod
    def _refuse(status: str, reason: str = "") -> StartResult:
        return StartResult(status, _MESSAGES[status], None, reason)

    def start(self, claims, scenario_id, request_token) -> StartResult:
        decision = self._authorize(claims)
        if not decision.allowed:
            return self._refuse("DENIED", decision.reason)
        sub = decision.sub
        if not isinstance(request_token, str) or not TOKEN_RE.fullmatch(request_token):
            return self._refuse("INVALID_REQUEST", "BAD_TOKEN")
        try:
            self.registry.get(scenario_id)
        except UnknownScenario:
            return self._refuse("INVALID_REQUEST", "UNKNOWN_SCENARIO")
        if self.kill_switch.engaged():
            return self._refuse("DISABLED", "KILL_SWITCH")

        known = self.ledger.get(request_token)
        if known is not None:
            if known["sub"] != sub:
                return self._refuse("INVALID_REQUEST", "FOREIGN_TOKEN")
            return StartResult("REATTACHED", "This request was already submitted.", known.get("run_id"), known["state"])

        try:
            upload = self.registry.stage(scenario_id)
        except ScenarioUnavailable as exc:
            return self._refuse("SCENARIO_UNAVAILABLE", str(exc))

        cfg = self.config
        reservation = self.ledger.try_start(
            request_token, sub, scenario_id, daily_limit=cfg.daily_start_limit,
            monthly_budget_micro=usd_to_micro(cfg.monthly_budget_usd),
            reserve_micro=usd_to_micro(cfg.reserve_per_start_usd), classify_active=self.supervisor.classify)
        outcome = reservation.outcome
        if outcome == "EXISTING":  # lost a race with the same token in another tab: same single run
            return StartResult("REATTACHED", "This request was already submitted.", reservation.row.get("run_id"),
                               reservation.row["state"])
        if outcome == "FOREIGN_TOKEN" or outcome == "INVALID_TOKEN":
            return self._refuse("INVALID_REQUEST", outcome)
        if outcome in ("BUSY", "DAILY_LIMIT", "BUDGET"):
            return self._refuse(outcome)
        assert outcome == "RESERVED"

        try:
            info = self._launch(upload)
        except run_launcher.AlreadyRunning:
            self.ledger.mark_refused_busy(request_token)
            return self._refuse("BUSY", "LAUNCHER_LOCK")
        except Exception as exc:  # any other failure: release the money, keep the attempt counted
            self.ledger.mark_launch_failed(request_token, type(exc).__name__)
            return self._refuse("LAUNCH_FAILED", type(exc).__name__)
        run_id = info.get("run_id")
        if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
            self.ledger.mark_launch_failed(request_token, "launcher returned no valid run id")
            return self._refuse("LAUNCH_FAILED", "BAD_RUN_ID")
        self.ledger.mark_started(request_token, run_id, info.get("pid"))
        return StartResult("STARTED", "The live run has started.", run_id)

    def status(self, claims, run_id) -> dict:
        """Sanitized progress for one of the caller's own runs. Raises PermissionError otherwise."""
        decision = self._authorize(claims)
        if not decision.allowed:
            raise PermissionError("not authorized")
        if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
            raise PermissionError("unknown run")
        row = self.ledger.by_run_id(run_id)
        if row is None or row["sub"] != decision.sub:  # historical or other users' runs are never shown
            raise PermissionError("unknown run")
        return project_run(run_id, runs_root=self.config.runs_root, ledger_row=row)

    def usage(self, claims) -> dict:
        decision = self._authorize(claims)
        if not decision.allowed:
            raise PermissionError("not authorized")
        used = self.ledger.usage(decision.sub)
        return {"starts_today": used["starts_today"], "daily_limit": self.config.daily_start_limit,
                "month_charged_usd": used["charged_micro_this_month"] / 1_000_000,
                "monthly_budget_usd": self.config.monthly_budget_usd,
                "kill_switch_engaged": self.kill_switch.engaged()}
