# V3 Phase 2: protected live application (offline)

Status: **implemented, security-reviewed and tested offline on branch `feature/v3-protected-live-app`. Not committed, not published, nothing deployed, installed or connected.** No Gemini call, no VPS connection, no billing or credential change. The public replay demo and the original operator app are untouched. Section 7 records the final review round (test hygiene, token verification, deployment review) and supersedes any earlier statement in this file about forwarded identity claims.

## 1. What was built

```
Browser ──(later: Tailscale Serve)──► live/live_app.py   user live-web   no Gemini key, no CrewAI
                                          │ Unix socket, one JSON request per connection (live/ipc.py)
                                          ▼
                                     live/worker.py       user live-worker  Gemini key via systemd credential only
                                          │ LiveService (auth, ledger, kill switch) + Supervisor thread
                                          ▼
                                     src/full_pipeline.py  child process, allow-listed environment
```

| File | Role |
|---|---|
| `live/ipc.py` | Protocol (16 KB request, 5 s read, strict schema, unknown fields rejected; the only identity field is the raw ID token), pure dispatcher `handle_request`, Unix-socket server (peer user-ID check via `SO_PEERCRED`) and client |
| `live/oidc.py` | Worker-only verification of the Google-signed ID token (RS256 signature, issuer, audience, expiry, key cache) |
| `live/worker.py` | Entry point. Reads the key from `$CREDENTIALS_DIRECTORY/gemini_api_key`, refuses to start in unsafe conditions, builds the child environment, serves the socket, runs the supervisor |
| `live/config.py` | Strict, secret-free configuration and allow-list loader; refuses loose file permissions |
| `live/web_client.py`, `live/catalog.py` | Web-side glue: socket client, claims, token generation, scenario titles (no pipeline imports) |
| `live/live_app.py` | The Streamlit page |
| `live/service.py`, `live/ledger.py` | Additive: `LiveService.latest()` and `Ledger.latest_for_sub()` so a refreshed browser re-attaches |
| `src/run_launcher.py` | Additive: `launch(..., env=None)`; a given `env` is the child's complete environment (default behaviour unchanged) |
| `deploy/` | systemd units, tmpfiles, example configuration, secret-rendering script, emergency-stop script (prepared, not installed) |
| `docs/v3_phase2/19_V3_DEPLOYMENT_RUNBOOK.md` | Deployment, rollback, emergency stop, Tailscale plan |

## 2. How each requirement is met

| Requirement | Mechanism | Test |
|---|---|---|
| Unauthenticated or unauthorized start rejected | Page shows only a sign-in button until `st.login` succeeds; the worker verifies the raw Google ID token itself (section 7.3) and then authorizes the verified subject (allow-list, verified e-mail, session age) before anything else, for starts, status and reports alike | `test_live_ipc` (8 forged/invalid claim cases, nothing launched, nothing recorded), `test_live_app` |
| Web process cannot access the Gemini key | The key exists only in the worker unit's credential directory and the child's environment dictionary. The web unit has no such credential, its modules import no Gemini/CrewAI code, it refuses to run if a key variable is present in its environment, and its unit cannot see worker paths | `test_live_app` static tests (AST + clean interpreter import), `test_live_deploy`, `test_live_worker` |
| Worker alone holds the key, and not in its own environment | `read_credential` returns the value without storing it; `build_child_env` is an allow-list; the worker refuses to start if a key variable is already in its environment or a `.env` exists in the checkout or above it | `test_live_worker` |
| Duplicate requests cannot launch twice | Idempotent request token (ledger) + single-active-run gate in one `BEGIN IMMEDIATE` transaction | 5 repeats of one token and 8 simultaneous distinct requests each launch exactly one run |
| Quotas, timeout, kill switch | Existing ledger/supervisor/kill switch, now enforced behind the socket | `test_live_ipc` (daily limit, kill switch), earlier phase-1 suites for timeout |
| PASS permits Crew 2; FAIL blocks it | Real Flow, crews, independent gate and training on 40 synthetic rows with a scripted offline LLM, driven through the socket dispatcher | `test_live_ipc_pipeline` |
| Status never exposes secrets or private paths | Worker rebuilds every answer from an allow-listed, redacted projection; errors become fixed codes (`DENIED`, `INTERNAL`, `BAD_REQUEST`) | leak scans in `test_live_ipc`, `test_live_ipc_pipeline` |
| Refresh / duplicate protection in the page | A per-session request token (rotated after a successful start) plus `latest()` re-attachment; Start is disabled while a run is active | `test_live_app` |
| Live vs historical clearly distinguished | Permanent red banner "LIVE EXECUTION ... not the historical replay"; the public demo says "historical replay" | `test_live_app` |

## 3. The page
Sign in (Google) → shows budget/starts today, the kill-switch state and the caller's latest run (if any) → radio with exactly two scenarios (no upload, no text input) → required confirmation checkbox → **Start Live Analysis** → a fragment polls the worker every 3 s while the run is active: agent status (7 components), gate PASS/FAIL with findings, agent reports as each agent completes (shown as plain text, never as HTML), and retrospective held-out metrics.

## 4. Findings made while building
1. **The pipeline always writes to `<checkout>/runs`** (`src/full_pipeline.py` has no run-folder argument). The worker therefore checks that `<checkout>/runs` resolves to the configured `<state_dir>/runs`; the deployment makes both `runs` and `outputs` symlinks into `/var/lib/live`. Not changing the original pipeline was a deliberate choice.
2. **Kill switch must not live on `/run`** (a tmpfs cleared at reboot would silently release it). It is `/etc/live/KILL`.
3. **Web and worker need separate configuration trees** (`/etc/live`, `/etc/live-web`); otherwise the web account could read the allow-list and the worker's key.
4. **Port 443 cannot be used for Tailscale Serve** while Traefik publishes `0.0.0.0:443`; the plan uses 8443 (see the runbook).
5. `st.login` reads its secrets from a Streamlit secrets file, so the web unit renders a private one on tmpfs from systemd credentials at start (`deploy/bin/render-web-secrets.py`).
6. The first design had `live_app` import the scenario registry, which pulls in the pipeline launcher. `live/catalog.py` removed that; a test keeps the two lists identical.

## 5. Unresolved risks
- **Identity trust.** Resolved in the review round: the worker verifies the Google-signed ID token (model B). A compromised web process can no longer invent an identity. It can still replay a genuine token it has seen, for that token's remaining life (about an hour), and deny service.
- **Real OAuth behaviour is untested.** Everything about Google itself is assumed from documentation until a real sign-in: that `expose_tokens = "id"` yields the raw JWT in `st.user.tokens["id"]` (confirmed in the installed Streamlit 1.67 source, not run against Google), that the token carries `iss`, `aud`, `sub`, `email_verified`, `iat` and `exp`, that Google's key endpoint is reachable from the VPS, and that `kid`/RS256 is what Google signs with. Any mismatch fails closed (every request refused).
- **Everything systemd-related is untested** until a VPS exists: credentials, cgroup limits, `ProtectProc`/`ProcSubset`, `InaccessiblePaths`, symlinked run folders under `ProtectSystem=strict`. `systemd-analyze verify` was used only for syntax where available.
- **Google OAuth with a `*.ts.net:8443` redirect URI is untested.**
- Spending limits remain as described in file 17 (reserve and caps are not absolute guarantees; usage counters unmeasured; output cap unset).
- The pipeline's dependencies run with the key in their environment (see file 16, residual risks).
- The Unix-socket server handles one request at a time. Fine for one owner; a request that stalls for its full 5 s read timeout blocks others for that time.
- Windows can run the dispatcher, page and all non-socket tests; the socket server, peer-credential check and file-mode checks are exercised on Linux only.

## 6. Requirements before a real deployment
Owner decisions and actions (none performed): create the dedicated Gemini key and a prepaid project; create the Google OAuth client; choose and approve the exposure path (Tailscale install on the VPS); place the two pinned scenario workbooks on the server; approve the install steps in file 19. Then: a supervised first run to measure real token usage, time and memory, and a decision on the output cap and per-run reserve.

## 7. Final security and regression review round

### 7.1 Credential exposure during tests (fixed)
Cause: the first full run loaded the developer's `.env` into the test process (the crews call `load_dotenv()`), and one failing assertion printed the whole environment. Fixed in `tests/env_hygiene.py`, imported by the shared test helpers before anything else:
1. credential-looking variables inherited from the shell or IDE are removed from the test process;
2. `load_dotenv()` is a no-op, so no developer `.env` is ever read;
3. `repr(os.environ)` masks the value of every sensitive-looking name, so even a failing `assertIn(..., os.environ)` prints names and `<redacted>`.
The new tests do not read, print or compare real credentials (they use run-time-assembled dummy values and compare hashes). `tests/test_env_hygiene.py` (9 tests) covers: inherited canary secrets are removed in a clean interpreter; a `.env` containing a canary is not loaded; the masked environment repr; a real failing test run prints no secret; no test file passes `dict(os.environ)` to an assertion; no test reads a `.env` directly. The final Windows and Linux logs were scanned for the canary and for the exposed key's prefix: no hits.
Residual: a developer's shell can still hold a real key for non-test commands, and a test that spawns a *subprocess* which itself loads a `.env` (not the case in this suite) would bypass the in-process guard.

### 7.2 Regression results (final tree)
| Platform | Tests | Passed | Failed | Errors | Skipped |
|---|---|---|---|---|---|
| Windows 11, Python 3.12, `unittest discover -s tests` | 481 | 465 | 0 | 0 | 16 (POSIX-only: sockets, file modes, systemd, root-account tests, a clean-checkout-only test) |
| Linux (Ubuntu 24.04, Python 3.12.3) as an ordinary user | 481 | 474 | 0 | 0 | 7 (need root) |
| Linux, run as root (account-permission and socket tests included) | 481 | 481 | 0 | 0 | 0 |
Each Linux run extracted the final file list into a fresh folder (an earlier run reused a folder holding stale files and was discarded). The 215 original tests and the 38 public-demo tests are part of these totals.

### 7.3 Worker-side authentication (model B)
- The web process sends the raw ID token from `st.user.tokens["id"]` (needs `expose_tokens = "id"`, which `render-web-secrets.py` now writes). The protocol has no claims field; a request carrying one is rejected as malformed.
- `live/oidc.py` verifies, in order: size and three-segment shape; header algorithm exactly `RS256` (so `none`, HMAC and other algorithms are refused); no key-injection headers (`jku`, `x5u`, `jwk`, `x5c`, `crit`); a `kid`; the RSA-PKCS1v15-SHA256 signature against Google's published key (2048 bits or more); issuer; audience equal to our OAuth client ID; not expired (60 s skew); not issued in the future; a subject. Only then does `live.auth.authorize` apply the allow-list of subjects, verified e-mail and the 12 h maximum session age. `is_logged_in` is set by the verifier alone.
- Keys: fetched from Google's JWKS endpoint by the worker only, cached for an hour, refetched at most once a minute for an unknown key id (an attacker cannot make the worker hammer Google), stale keys served for at most 24 h if Google is unreachable, and **no key at all means every request is refused**.
- Authorization covers `start`, `status`, `latest` and `usage`: status and reports are returned only to a verified, allow-listed caller and only for that caller's own runs.
- Tokens are never logged, stored or echoed: the module has no logging, file or database access (checked statically); the dispatcher logs only a short reason code; tests assert the token never appears in answers, in logs or in the ledger file, and that the page keeps no token in session state.
- Responses: `UNAUTHENTICATED` (token missing/invalid/expired: the page asks the visitor to sign in again), `DENIED` (valid identity, not on the allow-list), `BAD_REQUEST`, `INTERNAL`.
- Tests (offline, a throwaway RSA key stands in for Google): `tests/test_live_oidc.py` (13) and the token cases in `tests/test_live_ipc.py` (forged, wrong key, tampered payload, unknown key id, `alg=none`, HS256, RS512, injected headers, expired, future-dated, wrong audience/issuer, missing subject, session too old, rotation, unreachable key endpoint, weak keys).
- **Remaining dependency on real OAuth configuration:** the OAuth client ID and redirect URI, the cookie secret, `expose_tokens`, the allow-listed `sub`, outbound HTTPS from the worker to `www.googleapis.com`, and the `cryptography` package in the worker environment (already installed as a dependency of the pinned packages in the test environments; confirm in the real one). An ID token lives about an hour, so a signed-in visitor must sign in again after that (a long-running page then shows the "sign in again" message).

### 7.4 Deployment review (local Linux tests)
- `systemd-analyze verify` reports nothing on either unit except the executables that are not installed here. `systemd-analyze security --offline` scores both units **below 4.0 (the tool's "OK" band)**; the review added an empty capability bounding set, no ambient capabilities and `RemoveIPC=yes` to both. Deliberately not set: `MemoryDenyWriteExecute`, `PrivateUsers` and `SystemCallFilter=~@privileged ~@resources` (each may break Python, joblib or the socket group change, and cannot be tried without a real host).
- With real operating-system accounts (`nobody` as the web account, `daemon` as the worker, `www-data` as a stranger) on a copy of the runbook's layout, the tests prove: the web account cannot read the Gemini credential, the worker's state, configuration, allow-list or scenario folder, and cannot write the checkout; the worker account cannot read the web secrets; each account can read what it needs; the socket (mode 0660, shared group) admits the web account and not a stranger; even a world-connectable socket refuses a peer whose user ID is not the web account; the worker refuses to run as root; the configuration permission checker agrees with the layout.
- The web process cannot launch the pipeline: it has no key (the unit has no Gemini credential and cannot see the worker's files), the page refuses to run if a key variable is present, its Python environment has no CrewAI or Gemini client, and in a clean checkout the pipeline's own preflight fails without a key (tested on Linux). A compromised web process could still run code that exists in the checkout, but nothing that can reach a model.
- Not testable offline: that systemd actually applies these directives on the VPS (credentials, cgroup limits, `ProtectProc`, `InaccessiblePaths`, symlinked run folders under `ProtectSystem=strict`).

### 7.5 Resource limits against the VPS (1 vCPU, 3.8 GB, shared with n8n and Traefik)
Measured on Linux with the real pipeline code and the scripted LLM on 40 synthetic rows: three complete runs in one process peaked at **about 448 MB** resident (27 s wall clock, about 20 s of CPU); a Streamlit page import with the live modules, about 65 MB; the web modules alone, about 44 MB. These are lower bounds: the real dataset (5,630 rows) trains larger models and the real Gemini client adds some memory. Limits chosen: worker `MemoryHigh` 1200 MB / `MemoryMax` 1536 MB, web 350 / 450 MB, no swap, CPU 80% and 25% of one core, OOM score raised so these units go first. Total ceiling about 2 GB leaves about 1.8 GB for n8n, Traefik and the system. Headroom looks adequate on paper; the first supervised run must replace this with a measurement (`systemd-cgtop`, the recorded token counters) before the numbers are trusted. CPU contention with n8n is the less certain risk: one vCPU shared means a run may take noticeably longer than the recorded 77 s, and the 300 s timeout may need review after measurement.

### 7.6 Rollback and emergency stop
Unchanged and re-checked: the kill switch is a persistent file (`/etc/live/KILL`, fail-closed if unreadable), `deploy/bin/emergency-stop.sh` engages it and stops only the web unit, the worker's supervisor stops a running job within about 5 s, and rollback removes only the two new units and the Tailscale Serve mapping. The scripts and units do not mention Docker, Traefik, n8n, SSH or the firewall (a test enforces this).

### 7.7 Open questions (unchanged, documented in file 19)
- Tailscale Serve on port 8443 (not 443, which Traefik holds) and the exact flag form for the installed Tailscale version.
- Whether Google accepts `https://<machine>.<tailnet>.ts.net:8443/oauth2callback` as an OAuth redirect URI. Untested; no no-purchase fallback is known.
