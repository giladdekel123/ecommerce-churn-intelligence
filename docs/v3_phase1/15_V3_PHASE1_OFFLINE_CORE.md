# V3 Phase 1: offline execution core

Status: **implemented, hardened and tested offline on branch `feature/v3-offline-execution-core`.** No Gemini call, no VPS connection, no installation, no credentials. Companion documents: `16_V3_CREDENTIAL_BOUNDARY_DESIGN.md` (design for the next phase) and `17_V3_SPENDING_CONTROLS_REVIEW.md` (what the spending limits do and do not guarantee).

## 1. Linux report-path fix (src/crew_support.py, crew1_agents.py, crew2_agents.py)

**Problem (proved in Phase 0):** CrewAI strips a leading `/` from a task's `output_file`, so on Linux an absolute report path silently became a path relative to the current directory.

**Fix:** tasks get bare file names and `kickoff()` runs inside the folder that must receive them, through `crew_working_directory()`.

**Process-wide implications reviewed**
| Concern | Resolution |
|---|---|
| The working directory belongs to the whole process, not a thread | A re-entrant lock makes two uses in one process take turns (tested with two threads). It cannot guard code that changes directory without it; the pipeline runs one crew at a time in its own subprocess and the Streamlit app never calls this in-process |
| Event-handler threads write the status file while the directory is changed | `LiveStatus` now resolves its file path to an absolute path (one-line change); tested with a relative run folder |
| Crew tools read run files | Both crew modules already resolved `run_dir` to an absolute path before building tools |
| Restoring the directory | Done in `finally`, including when the crew raises (tested) |
| Run isolation | Reports land in the run's own folder; the test asserts the unrelated working directory stays empty and that no stray files appear in the crew folders |
| Windows | A bare file name resolves against the current directory on both systems; the whole existing suite and the new tests pass on Windows |
| Asynchronous CrewAI events | CrewAI delivers events on worker threads. A test spies on every status write and proves that some happen on non-main threads while the crew's directory is current, and that all of them go to the one absolute status path |
| A relative `--run-dir` argument | Tested: both crews resolve it to an absolute path before the directory changes |
| A crew that raises | The directory is restored and nothing stray is written (tested) |

**Remaining concurrency assumptions (documented, not enforced by code)**
1. One crew runs per process at a time. The lock serializes two uses in one process but cannot stop other code from changing the directory without it.
2. While a crew runs, no other thread in that process may rely on a relative path. In the pipeline that is true: it runs in its own subprocess, started once per run by `run_launcher`, so two runs never share a working directory; the Streamlit app and the live service never call the helper in-process.
3. The lock is per process; it does nothing across processes (which is why each run is its own process).
4. Everything that runs during `kickoff()` must use absolute paths. `LiveStatus` and both crews' tools now do; any future code that runs inside a crew must too.

## 2. Optional token-usage recording
- Enabled only when `CREW_RECORD_TOKEN_USAGE=1`; off by default, so existing run folders are unchanged.
- Writes `agent_token_usage.json` beside each crew's outputs, using CrewAI's own `CrewOutput.token_usage` counters, exactly as reported.
- If the counters are missing, malformed or all zero (CrewAI's default for "nothing reported"), the file says `counters_reported: false` with `usage: null`. Nothing is estimated and no cost is computed here.
- A failure to write the file never fails a run.
- **Unverified:** whether the counters are populated for `gemini-3.5-flash-lite` needs one real call (not made).

## 3. Optional output-token cap
- `CREW_GEMINI_MAX_OUTPUT_TOKENS`, unset by default. Unset sends exactly the previous LLM arguments (tested).
- When set, both `max_output_tokens` (what the native Gemini provider applies) and `max_tokens` are passed. A real `LLM` object accepts and applies it offline (tested by construction only, no request).
- **Refused, not ignored:** non-numeric values, below **2,048** or above **65,536** raise an error that stops the run before any model use.
- **Truncation risk, honestly stated:**
  - The floor leaves about 1.8 times the room needed by the largest recorded deliverable (4,424 bytes, roughly 1,100 tokens).
  - Whether the cap also limits Gemini's hidden thinking tokens is **not verified** and affects how much room is left for the visible text.
  - The pipeline's evidence check confirms a report file exists but cannot detect a report cut off mid-sentence, and truncation by the real model cannot be tested offline.
  - **Recommendation:** leave the cap unset until the supervised Phase 4 run; then compare the recorded completion tokens and the report completeness against the historical reports before choosing a value (8,192 is the working proposal).

## 4. The live core (`live/`)

| Module | Responsibility |
|---|---|
| `auth.py` | Pure authorization of Google OIDC claims: issuer, audience, `sub` allow-list, verified email, expiry, issue time, maximum session age (12 h). Email is never the key |
| `scenarios.py` | Closed registry of the PASS and FAIL scenarios; fixed server-side file names; pinned SHA-256 (`db70f1e3...` and `1d37e8a6...`, from the genuine runs' receipts); one changed byte makes a scenario unavailable; staging is serialized because the existing launcher's temp-file name is shared |
| `ledger.py` | SQLite (WAL) ledger. One `BEGIN IMMEDIATE` transaction checks busy/daily/monthly limits and reserves cost. Idempotent by request token; refused attempts that never got a row are retryable; failed launches count against the daily limit but release the money |
| `control.py` | Fail-closed file kill switch; process termination (taskkill on Windows, SIGTERM then SIGKILL on POSIX, reaps zombies); the only module allowed to start a subprocess |
| `supervisor.py` | Enforces the run timeout and kill switch without any browser session; settles finished and dead runs; only declares a run stopped once the process is really gone |
| `projection.py` | Allow-listed, redacted view for the browser; reports only for COMPLETED agents; no identity, token, PID, path, manifest or log |
| `service.py` | `start()` and `status()` in a fixed order of checks (see its docstring); the only way to start a run |

The existing pipeline, `run_launcher`, `dashboard_data`, the independent gate and the approved policy are reused unchanged. There is no UI yet.

## 5. Findings made while building (all fixed or recorded)
1. `run_launcher.stage_upload` was not safe for simultaneous staging of the same file (one shared temp name per workbook; the pre-fix function raised `PermissionError` in 15 of 15 concurrent rounds on Windows). **Fixed in `src/run_launcher.py`:** every call writes its own uniquely named temp file, flushes it to disk, then atomically renames it over the content-addressed target; a locked target is retried a bounded number of times, identical content written by someone else counts as success, a persistent failure raises `UploadError`, a failed or interrupted call leaves no temp file, orphaned temp files older than an hour are removed, and the launch-time verification tolerates a momentary lock. The return value and validation are unchanged. The registry's lock is kept as a second layer, but the regression tests call `stage_upload` directly (13 tests in `tests/test_run_launcher_staging.py`).
2. The first redactor missed `GEMINI_API_KEY: ...` because `\b` does not fire after an underscore; fixed and covered by tests.
3. **Interrupted runs could have been free.** A reservation older than 120 s used to be released with the money returned; if the service had died after the pipeline started, that run was uncounted and unsupervised. Expired reservations are now kept and counted, and the supervisor adopts a live run that the ledger lost track of (details in file 17).

## 6. Residual risks and unresolved decisions
- **The key reaches the pipeline through its environment** (the existing code reads `GEMINI_API_KEY`), and `run_launcher.launch` passes the parent's whole environment. The design in file 16 keeps it out of the web process with a separate worker; not built.
- **`pid`-based termination** can in theory hit a reused process ID; the ledger stores the PID and the start time, and termination refuses its own process, but a hardened design would use a systemd unit per run.
- The timeout is tested against a real subprocess; the kill-switch path is tested with an injected terminator plus the same real termination function. Behavior under systemd (cgroups, `RuntimeMaxSec`) is untested until deployment.
- No rate limit on refused or denied attempts (they cost nothing). A reverse-proxy limit is advisable later.
- **Not yet built:** the Streamlit front end, `st.login` wiring, config loading from files, the allow-list enrollment flow, packaging and systemd units.
- Decisions still open: output-cap value (stays unset until supervised measurements); per-run reserve (currently $0.50, **not** a proven ceiling, see file 17); run timeout (300 s vs about 77 s recorded); the key-delivery design in file 16 (a worker with a systemd credential) awaiting approval.
