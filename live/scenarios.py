"""Closed registry of the two predefined demonstration scenarios.

A visitor can only name a scenario id. The workbook for each id lives in a server-private
directory under a fixed file name, and its SHA-256 is pinned here: if the file is missing,
resized beyond the limit, or differs by one byte, the scenario is unavailable and nothing is
staged or launched. No path or file name ever comes from the visitor.

The pinned hashes are those of the two workbooks used by the genuine recorded runs
(the original public workbook, and the local copy with one SatisfactionScore set to 9).
The workbooks themselves are CC BY-NC-SA 4.0 derivatives: they stay server-private and are
never committed or published.
"""
from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from src import run_launcher

SCENARIO_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class UnknownScenario(KeyError):
    """The requested scenario id is not in the registry."""


class ScenarioUnavailable(RuntimeError):
    """The scenario's workbook is missing or does not match its pinned hash."""


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    filename: str            # fixed, no directory part
    sha256: str              # pinned
    expected_outcome: str    # "PASS" or "FAIL": what the genuine gate recorded for this workbook
    description: str


DEFAULT_SCENARIOS = (
    Scenario("pass", "PASS: original dataset", "E Commerce Dataset.xlsx",
             "db70f1e34bc5326887ea3fb3d2a0854e82c577d55ef3a76fdfb25328631bd44d", "PASS",
             "The original, unmodified public dataset workbook."),
    Scenario("fail", "FAIL: deliberately corrupted workbook", "E Commerce Dataset - SatisfactionScore 9.xlsx",
             "1d37e8a64759c100fc90a0df3cba0af5f93ca613fa1f7f43b25470dd392c783a", "FAIL",
             "A local test copy with one SatisfactionScore set to 9 (approved range 1-5)."),
)


class ScenarioRegistry:
    def __init__(self, scenarios, directory):
        self._by_id = {}
        for scenario in scenarios:
            if not SCENARIO_ID_RE.fullmatch(scenario.id) or scenario.id in self._by_id:
                raise ValueError(f"Invalid or duplicate scenario id: {scenario.id!r}")
            if scenario.filename != Path(scenario.filename).name or not scenario.filename.lower().endswith(".xlsx"):
                raise ValueError(f"Scenario file must be a bare .xlsx name: {scenario.filename!r}")
            if not re.fullmatch(r"[0-9a-f]{64}", scenario.sha256):
                raise ValueError(f"Scenario hash must be a lowercase SHA-256: {scenario.id}")
            self._by_id[scenario.id] = scenario
        self._directory = Path(directory)
        # run_launcher.stage_upload writes through one shared ".tmp" name per workbook, so two
        # simultaneous stagings of the same file collide. Serialize them here (existing code unchanged).
        self._stage_lock = threading.Lock()

    def ids(self) -> list[str]:
        return list(self._by_id)

    def get(self, scenario_id) -> Scenario:
        if not isinstance(scenario_id, str) or scenario_id not in self._by_id:
            raise UnknownScenario("Unknown scenario")
        return self._by_id[scenario_id]

    def verified_bytes(self, scenario_id) -> bytes:
        """The workbook bytes, only if they match the pinned SHA-256 exactly."""
        scenario = self.get(scenario_id)
        path = self._directory / scenario.filename
        try:
            size = path.stat().st_size
            if not path.is_file() or size > run_launcher.MAX_UPLOAD_BYTES:
                raise ScenarioUnavailable("Scenario workbook is not available")
            data = path.read_bytes()
        except OSError:
            raise ScenarioUnavailable("Scenario workbook is not available") from None
        if hashlib.sha256(data).hexdigest() != scenario.sha256:
            raise ScenarioUnavailable("Scenario workbook does not match its pinned hash")
        return data

    def stage(self, scenario_id) -> dict:
        """Verify, then store content-addressed via the existing launcher; returns its upload dict."""
        scenario = self.get(scenario_id)
        with self._stage_lock:
            upload = run_launcher.stage_upload(scenario.filename, self.verified_bytes(scenario_id))
        if upload["sha256"] != scenario.sha256:  # defence in depth: staging must preserve the hash
            raise ScenarioUnavailable("Staged workbook does not match its pinned hash")
        return upload
