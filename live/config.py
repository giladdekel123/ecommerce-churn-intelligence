"""Worker configuration: a strict, secret-free JSON file plus the owner's allow-list.

No credential belongs in these files (the Gemini key arrives through a systemd credential, never
through configuration). Unknown keys are rejected, and so is any key whose name looks like a secret,
so a mistaken paste fails loudly instead of being stored. On POSIX the files must not be readable or
writable by "other" users; the worker refuses to start otherwise.
"""
from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from live.service import LiveConfig

SUB_RE = re.compile(r"^[0-9]{5,32}$")
_SECRETISH = re.compile(r"(?i)secret|password|passwd|token|api[_-]?key|credential|private")
_KEYS = {"client_id", "state_dir", "scenarios_dir", "socket_path", "kill_switch_path", "web_user",
         "socket_group", "daily_start_limit", "monthly_budget_usd", "reserve_per_start_usd", "run_timeout_s", "max_session_age_s"}
_REQUIRED = {"client_id", "state_dir", "scenarios_dir", "socket_path", "kill_switch_path", "web_user"}


class ConfigError(ValueError):
    """The configuration is unsafe or malformed; the worker must not start."""


@dataclass(frozen=True)
class WorkerConfig:
    live: LiveConfig
    state_dir: Path
    scenarios_dir: Path
    socket_path: Path
    kill_switch_path: Path
    web_user: str
    socket_group: str | None = None

    @property
    def ledger_path(self) -> Path:
        return self.state_dir / "ledger.sqlite"


def check_private_file(path) -> None:
    """POSIX: refuse a file that "other" users can read or write, or that the group can write."""
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"Missing file: {path.name}")
    if os.name == "nt":
        return  # permission bits are not meaningful on Windows; the Linux suite covers this
    mode = path.stat().st_mode
    if mode & (stat.S_IROTH | stat.S_IWOTH | stat.S_IXOTH):
        raise ConfigError(f"{path.name} is accessible to other users (mode {stat.filemode(mode)})")
    if mode & stat.S_IWGRP:
        raise ConfigError(f"{path.name} is writable by its group (mode {stat.filemode(mode)})")


def _load_json(path) -> dict:
    check_private_file(path)
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"{Path(path).name} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"{Path(path).name} must contain a JSON object")
    return value


def _number(raw, name, low, high, kind):
    value = raw.get(name)
    if isinstance(value, bool) or not isinstance(value, kind):
        raise ConfigError(f"{name} must be a {kind.__name__}")
    if not low <= value <= high:
        raise ConfigError(f"{name} must be between {low} and {high}")
    return value


def load_allowed_subs(path) -> frozenset:
    data = _load_json(path)
    if set(data) != {"allowed_subs"}:
        raise ConfigError("allow-list file must contain exactly the key allowed_subs")
    subs = data["allowed_subs"]
    if not isinstance(subs, list) or not 1 <= len(subs) <= 20 or not all(isinstance(s, str) and SUB_RE.fullmatch(s) for s in subs):
        raise ConfigError("allowed_subs must be 1 to 20 Google user IDs (digits only)")
    return frozenset(subs)


def load_worker_config(config_path, allowed_subs_path) -> WorkerConfig:
    raw = _load_json(config_path)
    bad = [k for k in raw if _SECRETISH.search(k)]
    if bad:
        raise ConfigError(f"Secret-looking key(s) are not allowed in configuration: {', '.join(sorted(bad))}")
    unknown = set(raw) - _KEYS
    if unknown:
        raise ConfigError(f"Unknown configuration key(s): {', '.join(sorted(unknown))}")
    missing = _REQUIRED - set(raw)
    if missing:
        raise ConfigError(f"Missing configuration key(s): {', '.join(sorted(missing))}")
    for name in ("client_id", "state_dir", "scenarios_dir", "socket_path", "kill_switch_path", "web_user"):
        if not isinstance(raw[name], str) or not raw[name].strip():
            raise ConfigError(f"{name} must be a non-empty string")
    if not raw["client_id"].endswith(".apps.googleusercontent.com"):
        raise ConfigError("client_id must be a Google OAuth client ID")
    if "socket_group" in raw and (not isinstance(raw["socket_group"], str) or not raw["socket_group"]):
        raise ConfigError("socket_group must be a non-empty string")
    paths = {n: Path(raw[n]) for n in ("state_dir", "scenarios_dir", "socket_path", "kill_switch_path")}
    for name, value in paths.items():
        if not (value.is_absolute() or PurePosixPath(raw[name]).is_absolute()):
            raise ConfigError(f"{name} must be an absolute path")
    daily = _number(raw, "daily_start_limit", 1, 10, int) if "daily_start_limit" in raw else 2
    budget = _number(raw, "monthly_budget_usd", 0.5, 20.0, (int, float)) if "monthly_budget_usd" in raw else 5.0
    reserve = _number(raw, "reserve_per_start_usd", 0.05, 5.0, (int, float)) if "reserve_per_start_usd" in raw else 0.5
    timeout = _number(raw, "run_timeout_s", 30, 900, int) if "run_timeout_s" in raw else 300
    age = _number(raw, "max_session_age_s", 300, 86400, int) if "max_session_age_s" in raw else 12 * 3600
    if reserve > budget:
        raise ConfigError("reserve_per_start_usd cannot exceed monthly_budget_usd")
    live = LiveConfig(allowed_subs=load_allowed_subs(allowed_subs_path), client_id=raw["client_id"],
                      runs_root=paths["state_dir"] / "runs", daily_start_limit=daily,
                      monthly_budget_usd=float(budget), reserve_per_start_usd=float(reserve),
                      run_timeout_s=timeout, max_session_age_s=age)
    return WorkerConfig(live, paths["state_dir"], paths["scenarios_dir"], paths["socket_path"],
                        paths["kill_switch_path"], raw["web_user"],
                        raw.get("socket_group"))
