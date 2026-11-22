"""Small, CrewAI-facing helpers shared by the two agent crews (Architecture V3, Phase 1).

1. crew_working_directory(): CrewAI strips a leading "/" from a task's output_file,
   so on Linux an absolute report path silently becomes a path relative to the current
   directory. Tasks therefore get bare file names and the crew runs inside the folder
   that must receive them. See the function for the process-wide implications.
2. output_token_limit_kwargs(): an optional, environment-configured output-token cap.
   Unset means exactly the previous behaviour (no cap is sent).
3. record_token_usage(): optional recording of CrewAI's own usage counters.
   It records what the counters say, or that they reported nothing; it never estimates.
"""
from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path

OUTPUT_TOKEN_ENV = "CREW_GEMINI_MAX_OUTPUT_TOKENS"
RECORD_USAGE_ENV = "CREW_RECORD_TOKEN_USAGE"
USAGE_FILE = "agent_token_usage.json"
# Floor/ceiling for the optional cap. The floor exists so a mistyped value cannot silently
# truncate the required deliverables (the largest recorded review is about 4.4 KB, roughly
# 1,100 tokens, before any thinking tokens); the ceiling is the model's documented maximum.
MIN_OUTPUT_TOKENS = 2048
MAX_OUTPUT_TOKENS = 65536
_USAGE_FIELDS = ("total_tokens", "prompt_tokens", "cached_prompt_tokens", "completion_tokens",
                 "reasoning_tokens", "cache_creation_tokens", "successful_requests")

_CWD_LOCK = threading.RLock()


@contextmanager
def crew_working_directory(directory):
    """Run a crew with `directory` as the current directory, then restore the old one.

    Process-wide implications (reviewed for Phase 1):
    - The working directory belongs to the whole process, not to a thread. The lock makes
      two uses in one process take turns instead of overlapping; it cannot protect code that
      changes directory without it. The pipeline runs one crew at a time in its own
      subprocess, and the Streamlit app never calls this in-process.
    - Everything that runs while the directory is changed must use absolute paths. The crew
      modules resolve their run directory first, and LiveStatus resolves its file path, so
      the event-handler threads write to the right place.
    - The previous directory is restored in `finally`, including when the crew raises.
    - Behaviour is identical on Windows and Linux: a bare file name is resolved against the
      current directory on both.
    """
    target = Path(directory).resolve()
    if not target.is_dir():
        raise FileNotFoundError(f"Crew output directory does not exist: {target}")
    with _CWD_LOCK:
        previous = os.getcwd()
        os.chdir(target)
        try:
            yield target
        finally:
            os.chdir(previous)


def output_token_limit_kwargs(env=None) -> dict:
    """Extra LLM keyword arguments for the optional output-token cap ({} when not configured)."""
    env = os.environ if env is None else env
    raw = (env.get(OUTPUT_TOKEN_ENV) or "").strip()
    if not raw:
        return {}
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{OUTPUT_TOKEN_ENV} must be a whole number of tokens") from None
    if not MIN_OUTPUT_TOKENS <= value <= MAX_OUTPUT_TOKENS:
        raise ValueError(f"{OUTPUT_TOKEN_ENV} must be between {MIN_OUTPUT_TOKENS} and {MAX_OUTPUT_TOKENS}; "
                         "a smaller cap could truncate the required agent deliverables")
    # The native Gemini provider applies max_output_tokens; max_tokens is the generic name.
    return {"max_output_tokens": value, "max_tokens": value}


def _counters(result) -> dict | None:
    usage = getattr(result, "token_usage", None)
    if usage is None:
        return None
    values = {}
    for name in _USAGE_FIELDS:
        value = getattr(usage, name, None)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            values[name] = value
    return values or None


def record_token_usage(result, directory, *, crew: str, model: str, env=None) -> dict | None:
    """Write agent_token_usage.json next to the crew's outputs when recording is enabled.

    Returns the record, or None when recording is off. When the counters are missing or all
    zero (CrewAI's default for "nothing reported") the record says so and carries no numbers.
    """
    env = os.environ if env is None else env
    if (env.get(RECORD_USAGE_ENV) or "").strip().lower() not in ("1", "true", "yes"):
        return None
    counters = _counters(result)
    reported = bool(counters) and (counters.get("successful_requests", 0) > 0
                                   or counters.get("total_tokens", 0) > 0)
    record = {
        "crew": crew,
        "model": model,
        "source": "crewai CrewOutput.token_usage",
        "counters_reported": reported,
        "usage": counters if reported else None,
        "max_output_tokens_configured": output_token_limit_kwargs(env).get("max_output_tokens"),
        "note": ("Counters as reported by CrewAI for this crew; not a bill." if reported else
                 "CrewAI reported no usage counters for this crew; no usage has been estimated."),
    }
    target = Path(directory) / USAGE_FILE
    temp = target.with_name(target.name + ".tmp")
    try:
        temp.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        temp.replace(target)
    except OSError:
        return record  # optional telemetry must never fail an evidence-producing run
    return record
