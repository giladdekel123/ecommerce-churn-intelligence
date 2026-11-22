# Architecture V3, Phase 0: decision record

Status: **Phase 0 record, written before Phase 1.** It is kept as the historical decision record. Where it says "no work started" or lists items as proposed, Phase 1 (`docs/v3_phase1/`) has since implemented and tested them offline, and the Linux path defect it describes is fixed on the Phase 1 branch. The VPS has not been contacted. Gemini has not been called.

Companion documents: `13_V3_SECURITY_CHECKLIST.md`, `14_V3_VPS_READONLY_INSPECTION_CHECKLIST.md`.

## 1. Summary

**Provisional decision (ADR-V3-01):** a separate, protected live-execution Streamlit application, deployed as a **systemd service on the owner's Hostinger VPS, bound to 127.0.0.1**, reachable over **Tailscale Serve** (HTTPS without buying a domain), signing users in with **Google OIDC** and authorizing them against a **server-side allow-list of Google `sub` values**. It reuses the existing pipeline, `run_launcher` and independent gate unchanged. The public demo stays on Streamlit Community Cloud and is unaffected.

What this rests on, in one table:

| Question | Answer | Status |
|---|---|---|
| Does the pipeline and its test suite work on Linux? | 210 of 215 pass unmodified; 215 of 215 with a small output-path fix (validated in a throwaway copy) | **Verified**, with one real defect that must be fixed first |
| Can it coexist with n8n? | Yes, in principle, with systemd on loopback; depends on facts from the VPS | **Uncertain until the VPS is inspected** |
| Is there an HTTPS option without a domain? | Tailscale Serve (owner-only) | Verified as a mechanism; **Google accepting a `ts.net` redirect URI is untested** |
| Is the OIDC design sound? | Yes, with the specific hardening in section 5 | **Verified** against Google and Streamlit docs |
| Can spend be bounded to about $5? | Preventative limits plus a prepaid backstop | **Partly verified**; prepaid availability unknown |
| What does a run cost? | Estimate only: about $0.03 (FAIL) to $0.06 (PASS) central | **Estimated**, calibrate with one supervised run |

## 2. Linux compatibility (Task 1)

**Method.** A clean export of the tracked files of `main` (commit `8a158d7`; no `.env`, data, runs or Gemini key) was placed in a throwaway folder in WSL2 Ubuntu 24.04.3 (Python 3.12.3, the project's target version). A fresh virtual environment was built from the pinned `requirements.txt` (CrewAI 1.15.37) and the full suite was run with no Gemini key in the environment. No Gemini call, no training beyond the tests' own synthetic data, no VPS involvement.

**Result, unmodified code: 210 passed, 5 failed** (all five in `tests/test_agent_progress.py`; deterministic, three runs out of three; the same file passes 13 of 13 on Windows).

**Root cause (verified, not assumed).** The agent tasks are given an *absolute* `output_file` path. CrewAI strips a leading `/` from `output_file` ("for regular paths, leading slashes are stripped"), so on Linux `/home/.../runs/<id>/crew1/quality_review.md` is written to `<current directory>/home/.../runs/<id>/crew1/quality_review.md`. Windows paths start with a drive letter, so they are unaffected. Evidence: the three tools were called, the report files were missing from the run folder, and stray `src/tmp/tmp.../runs/...` folders appeared in the test copy. The pipeline **fails closed** (its evidence check marks the agents FAILED), so nothing is misreported, but **a live run on Linux cannot succeed until this is fixed.**

**Fix, validated only in the throwaway copy.** Give each task a bare filename and run `kickoff()` inside the run folder (`contextlib.chdir`), in both `crew1_agents.py` and `crew2_agents.py`. With that change **all 215 tests pass on Linux.** The real repository was not changed. The fix touches production crew code, so it needs your approval (section 9) and a Windows re-test before it is adopted.

**Other Linux findings**
- Process-liveness code already has a POSIX branch (`os.kill(pid, 0)`); the launcher passes `creationflags` only on Windows. No other Windows-only code was found by inspection.
- `python3-venv` is not installed on a stock Ubuntu 24.04, so a standard venv could not be created without `apt`; pip was bootstrapped into a venv created with `--without-pip`. On the VPS use `uv` or install `python3-venv` (an approved change).
- WSL2 shuts its VM down when idle and clears `/tmp` at boot; irrelevant to a VPS but it cost two retries here.

**Not verified:** a real end-to-end run on Linux (needs Gemini); the Windows behaviour after the fix; the VPS's own distribution and Python.

## 3. Coexistence with n8n and deployment style (Task 3)

### What is known
- Hostinger's n8n VPS template is documented as **Docker Compose with Traefik**, using a hostname like `srvNNNNNN.hstgr.cloud` (`TRAEFIK_HOST`). n8n's own Docker Compose guide publishes Traefik on **80 and 443** and n8n only on `127.0.0.1:5678`.
- **Not known:** whether the owner's n8n follows that template. This is the first thing the inspection checklist (file 14) establishes.

### Comparison (neither option assumes Docker is required)

| | systemd service + venv/uv | Docker Compose |
|---|---|---|
| Interference with n8n | Lowest. No Docker daemon change, no shared networks, no ports 80/443 | Medium. Shares the Docker daemon and firewall rules with n8n; a daemon restart touches both |
| Ports | Loopback only (for example 127.0.0.1:8501); exposure via Tailscale Serve | Must bind `127.0.0.1:PORT:PORT`; Docker-published ports bypass ufw (Docker docs) |
| Protecting n8n from our load | `MemoryMax`, `CPUQuota`, `TasksMax` on the unit (all documented) | Compose limits possible but weaker integration |
| Sandboxing | `NoNewPrivileges`, `ProtectSystem=strict`, `PrivateTmp`, `SystemCallFilter`, optional `IPAddressDeny` | Container isolation by default |
| Secrets | `LoadCredential=` (documented as the secure route; `EnvironmentFile=` is documented as unsuitable for secrets) | Docker secrets or env file |
| Run time limit | `RuntimeMaxSec=` plus an app watchdog | Watchdog only |
| Reproducibility | Pinned requirements + `uv`. Needs Python 3.12 (Ubuntu 24.04 ships it) | Strongest: image pins everything (about 2-3 GB, estimated) |
| Setup friction found in testing | `python3-venv` was **not installed** on a stock Ubuntu 24.04 test machine, so use `uv` to avoid `apt` | None for Python |
| Rollback | Stop and disable one unit | Compose down |

**Recommendation (ADR-V3-02):** systemd + Tailscale Serve, because it touches neither n8n's Docker/Traefik setup nor ports 80/443 and gives cgroup limits that protect n8n. Docker remains a fallback if the VPS inspection shows Python or system conflicts.

### Constraints to respect either way
- Do not share an origin with n8n (for example a path on the same hostname). Same-origin cookies and scripts would mix the two applications' trust boundaries.
- Serve on a **non-443 Tailscale port** (for example 8443) until a spike shows Tailscale Serve and Traefik coexist on 443 (**uncertain**).
- No change to n8n, Traefik or the firewall without a separate approval.

## 4. HTTPS without buying a domain (Task 6)

| Option | Public? | Works with Google login? | Verdict |
|---|---|---|---|
| **Tailscale Serve** (`*.ts.net`, automatic Let's Encrypt cert, free Personal plan, non-commercial, up to 6 users) | **No**, tailnet members only | Unknown: Google's rule that hosts be on the public suffix list was seen only in a summary | **Recommended for the owner-only phase** |
| Tailscale Funnel | Yes (beta, ports 443/8443/10000, bandwidth limits not stated) | Same question | Only if public access is later wanted |
| Hostinger default `srvNNNNNN.hstgr.cloud` | Yes | Unknown; not stated whether Hostinger issues certificates for it; the name belongs to Hostinger | Not recommended; also shares n8n's hostname |
| Let's Encrypt certificate for the raw IP | Yes | **No**: Google rejects raw-IP redirect hosts | Rejected |
| Cloudflare quick tunnel | Yes | Hostname changes every time, no uptime guarantee, no SSE | Rejected |
| DuckDNS free subdomain | Yes | Whether a certificate can be issued for it is not stated | Possible fallback, unverified |
| Buy a domain (about the cost of one coffee per month) | Yes | Yes | Cleanest long-term; **not purchased**, needs owner approval later |

**The single gating uncertainty** for this choice is whether Google accepts a `*.ts.net` redirect URI. It can be tested for free in the Google Cloud console by adding the URI to an OAuth client (an owner action). If Google rejects it, the fallbacks are a domain, or, for the owner-only phase, relying on the tailnet identity plus a local app-level allow-list.

## 5. Google OIDC design validation (Task 4)

Verified from Google and Streamlit documentation:
- Streamlit `st.login` supports **OIDC only**, redirects to the provider and keeps the identity in a browser cookie that lasts **30 days, not configurable, not tied to token expiry**. It "does not implicitly check" token expiry, and OIDC "supports authentication, but not authorization".
- Google's ID token gives `sub` ("unique among all Google Accounts and never reused"), `email` ("may not be unique ... could change over time"), `email_verified` and, for Workspace users, `hd`.
- Redirect URIs must be HTTPS (localhost exempt) and must match exactly.
- An External app left in **Testing** requesting only `openid email profile` does not need test users, shows no warning and is not subject to the 7-day expiry.

Resulting design:
1. **Authenticate** with `st.login` (Google).
2. **Authorize** server-side: `sub` in a root-owned allow-list file, `email_verified` true, `iss`/`aud`/`exp` checked, session age under a limit we set (12 h from `iat`, because the cookie alone would allow 30 days). A mismatch calls `st.logout()`.
3. **Re-authorize at every run start**, inside the start function, together with the ledger check.
4. **Never trust email alone**; log nothing from the tokens.
5. Owner enrollment: the owner logs in once, is shown their own `sub`, and adds it to the allow-list file.
6. `cookie_secret` and the OAuth client secret live in a 0600 file outside the repo.

Still to test (cost-free spikes): Streamlit auth behind Tailscale Serve (WebSocket pass-through, forwarded scheme); Google accepting the redirect host.

## 6. Budget and quota design (Task 5)

Provisional limits from the owner: owner-only; one active run globally; at most two run starts per user per day; about $5 per month; dedicated prepaid project if available; global kill switch; execution timeout.

### Preventative controls (enforced by our own code, immediate, independent of Google)
| Control | How |
|---|---|
| Owner-only | Allow-list of one `sub` |
| One active run | Existing exclusive-create lock file in `run_launcher` |
| Two starts per user per day | Counted in a persistent ledger at the start attempt, so failed and refreshed attempts count |
| Monthly budget | The ledger **reserves** a conservative cost per start and refuses once the reserved total reaches $5 (see below) |
| Kill switch | A flag file checked at every start and by the watchdog; `systemctl stop` as the second lever |
| Timeouts | Existing 30 s per LLM call and 90 s per agent; no retries (`max_retries=0`); plus a run-level watchdog that kills the pipeline process group; `RuntimeMaxSec` as the ceiling |
| Output cap per LLM call | Proposed `max_output_tokens`. **Not set today.** Without it, the largest single response the model permits (65,536 tokens, about $0.16) times up to 18 calls (6 agents with at most 3 iterations each) is a theoretical $2.95 per run |
| Idempotent start | One-time request token stored in the ledger; a refresh re-attaches to the existing run |

### Reserve arithmetic (estimates; every input is labelled)
- Published standard price for `gemini-3.5-flash-lite`: $0.30 per 1M input, $2.50 per 1M output (thinking included). **Verified.**
- With `max_output_tokens = 8,192` and at most 18 calls, **assumed** inputs of at most 20,000 tokens per call: 18 x 8,192 x $2.50/M = $0.37, plus 18 x 20,000 x $0.30/M = $0.11, so about **$0.48 per run as a provable ceiling** (if the cap really covers thinking tokens, **unverified**).
- Initial reserve per start: **$0.50**, which gives **10 starts per month** inside $5 with a guarantee that does not depend on measurements.
- After one supervised PASS and one FAIL run give real token counts, the reserve can be lowered to about 1.5 times the observed worst run (central estimate $0.06 to $0.09, which would allow about 50 to 80 starts, still limited by the two-per-day rule to 62).

### Controls that depend on delayed external data
| Control | Delay and limits |
|---|---|
| **Prepaid balance** on a dedicated billing account | Draws down "within minutes". At $0 all keys on **that billing account** fail with HTTP 402, so the billing account must be dedicated to this app. Minimum top-up $5, credits expire after 12 months, no refunds. Availability for the owner's account is **not stated by country** |
| Project monthly spend cap (AI Studio) | Labelled **experimental**; overage possible during roughly a 10-minute latency window |
| AI Studio / Cloud Billing views | Can take up to 24 hours (Cloud Billing sometimes longer) |
| Google budget alerts | Alerts only; they do not cap spend, and notifications can lag by hours |
| Account tier cap (Tier 1: $250) | Far too high to protect a $5 target |

So the dependable $5 ceiling is **the ledger**, with the prepaid balance as a delayed backstop, not the other way round. After each run the ledger also records the client-side usage counters (see section 2) and compares the monthly sum with Google's dashboard to detect drift.

## 7. Corrections to the feasibility audit

1. The audit said CrewAI's native token counts were unavailable. That warning concerns *pre-call input estimates*. The installed Gemini provider exposes usage counters, including `reasoning_tokens` and `successful_requests` (`LLM.get_token_usage_summary()`, `Crew.usage_metrics`, `CrewOutput.token_usage`). Whether they are populated for this model needs one live call to confirm (**not done**).
2. The existing crew code discards the `kickoff()` result, so usage was never recorded; capturing it is an additive change.
3. On the native Gemini provider the output cap is applied through `max_output_tokens`; `max_tokens` is accepted but is not what is sent in the generation config (read from the installed source). Phase 1 must confirm which parameter takes effect.

## 8. Cost assumptions

| Item | Assumption | Status |
|---|---|---|
| Gemini per run | FAIL about $0.03 (range $0.01 to $0.06); PASS about $0.06 (range $0.02 to $0.13) | **Estimate**; thinking tokens unknown |
| Gemini per month at the proposed limits | At most about $5 by design | Controlled by the ledger |
| Prepaid top-up | $5 minimum (expires in 12 months) | Verified (Google billing page) |
| VPS | $0 extra if the existing plan has headroom; otherwise KVM 1 $6.49 or KVM 2 $8.79 per month promotional (24-month prepaid), renewing at $11.99 or $14.99 | Verified (Hostinger) |
| Tailscale | $0 (Personal plan, non-commercial use, up to 6 users) | Verified |
| Domain | $0 now | Not purchased |
| Google OAuth client | $0 | Not created |

## 9. Phase 1 implementation proposal (approved; implemented offline, see `docs/v3_phase1/`)

**Goal:** the complete, secure execution core and UI, tested offline with the existing scripted fake LLM. **No real Gemini call, no VPS, no deployment in Phase 1.**

New code, in a new `live/` folder (the existing `app.py`, `src/` pipeline logic, gate and policy are not changed):
| File | Purpose |
|---|---|
| `live/auth.py` | Pure function: claims + allow-list + clock gives allow/deny with a reason (unit-testable without Streamlit) |
| `live/ledger.py` | SQLite (WAL) ledger: atomic "check limits and record start" transaction, idempotency tokens, daily and monthly counters, kill flag |
| `live/scenarios.py` | Registry of the PASS and FAIL scenarios: server-side paths, pinned SHA-256, expected outcome |
| `live/status_view.py` | Sanitized allow-list projection of `live_status.json` and report files |
| `live/live_app.py` | Streamlit app: login, pick a scenario, explicit Start, poll |

Small changes to the existing pipeline (**need explicit approval because they touch production crew code**):
- **Required for Linux:** bare `output_file` names and `contextlib.chdir(run_dir)` around `kickoff()` in `crew1_agents.py` and `crew2_agents.py` (section 2). Must be re-tested on Windows.
- `max_output_tokens` read from an environment variable; unset keeps today's behaviour exactly.
- Record `get_token_usage_summary()` per crew into the run folder.

Acceptance tests:
1. Unauthenticated, unlisted, unverified-email, expired-session and over-age sessions cannot start a run and leave the ledger unchanged.
2. Two simultaneous starts produce one run; a refresh or double click re-attaches to it.
3. The third start of a day and the start that would exceed the monthly reserve are refused; the kill switch refuses at once and stops a running job.
4. A run exceeding the watchdog is killed and recorded as failed.
5. The offline PASS scenario completes through the real pipeline and gate with the scripted LLM; the FAIL scenario ends with `CSV-007` and a blocked Crew 2.
6. The projected status and reports pass the leak scan (no paths, tokens, keys, emails).
7. Static checks: no upload widget, no raw log display, no secrets in the repository.
8. The existing 215 tests still pass, on Windows and Linux (Linux needs the output-path fix).

Cost-free spikes the owner may authorize alongside (each needs an owner action): Google accepting a `*.ts.net` redirect URI; Streamlit login behind Tailscale Serve on the owner's own PC; the VPS inspection.

## 10. Decisions requiring owner approval

1. Approve the systemd + Tailscale Serve direction (ADR-V3-01/02), or ask for Docker.
2. Authorize the **read-only VPS inspection** (file 14), and say who runs it.
3. Approve creating a Google OAuth client for the redirect-URI test (free), and whether to use External/Testing or Personal.
4. Approve installing Tailscale (on the PC for a spike; on the VPS later).
5. Confirm whether a prepaid Gemini billing account is available to you and approve the $5 top-up when the time comes.
6. Approve the three small pipeline changes in section 9 (the Linux output-path fix is required; the other two are additive and gated by environment variables).
7. Approve Phase 1 itself.
8. Defer: domain purchase, public (Funnel) access, additional users.
