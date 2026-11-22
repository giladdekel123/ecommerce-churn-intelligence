# V3 security checklist (Phase 0 draft)

Legend: **V** = verified against an official source or by a local test in Phase 0. **D** = designed, to be implemented and tested in a later phase. **U** = uncertain; needs a test or an owner action. Nothing here is implemented yet.

## Phase 1 status update (offline; added after Phase 0)
The statuses in the tables below are the Phase 0 ones. Phase 1 implemented and tested the following offline (Windows and Linux); nothing has been deployed, so no row is marked verified in production.

| Rows | What Phase 1 did |
|---|---|
| 1.2-1.7 | `live/auth.py` and the order of checks in `live/service.py`: `sub` allow-list, verified email, issuer, audience, expiry, issue time, 12-hour session age, re-checked at every start, a single start path |
| 5.1-5.7 | Single active run (ledger and launcher lock), 2 starts per user per day, monthly reservation budget, file kill switch, run timeout via the supervisor, idempotent request tokens. The output-token cap exists as an option but is **unset by design** |
| 6.1-6.2 | `live/projection.py`: allow-listed, redacted browser view (tests include a leak scan) |
| 4.7 | `live/scenarios.py`: closed registry, pinned SHA-256, no visitor file reaches the launcher |
| 7.6 | Linux suite passes after the path fix and the staging fix (see file 15) |
| 3.3-3.4 | Design only: `docs/v3_phase1/16_V3_CREDENTIAL_BOUNDARY_DESIGN.md` |
| 5.8-5.10 | Limits explained in `docs/v3_phase1/17_V3_SPENDING_CONTROLS_REVIEW.md`; the $0.50 reserve and $5 target are **not** absolute guarantees |

Still not done: rows 1.13, 1.14, 2.x (network), 3.x (credentials), 4.1-4.6 (systemd), 7.1-7.5.

## 1. Identity and authorization

| # | Control | Status | Notes |
|---|---|---|---|
| 1.1 | Login by Google OpenID Connect via `st.login`. Streamlit supports OIDC only and notes that OIDC provides authentication, not authorization | V | Streamlit auth docs |
| 1.2 | Authorization is a **server-side allow-list keyed by the Google `sub` claim**, not by email. Google says `sub` is unique and never reused, and that `email` "could change over time" | V (claim semantics), D (allow-list) | Google OpenID Connect docs |
| 1.3 | Require `email_verified == true` as an additional condition | V (claim exists), D | Google returns it as a boolean or a string in different places; compare defensively |
| 1.4 | Check `iss` is Google, `aud` equals our client ID, and `exp` is in the future on every start request. Streamlit populates `st.user` from the ID token but "does not implicitly check" expiry | V (Streamlit docs), D | Our code must read `exp`/`iat` and call `st.logout()` |
| 1.5 | Maximum session age enforced by us (proposal: 12 h from `iat`). Streamlit's identity cookie lasts 30 days, is not configurable and is not tied to token expiry | V (cookie behaviour), D | Closes the gap between 30 days and what we want |
| 1.6 | Re-check authorization **at the moment of each run start**, not only at page load | D | The start function must not trust earlier page state |
| 1.7 | No execution path reachable without passing 1.2-1.6. The only way to start a run is one server-side function; the app exposes no upload widget and no custom HTTP endpoint | D | Test: unauthenticated and unlisted users get a refusal and the ledger is unchanged |
| 1.8 | Google consent screen "External / Testing" with only `openid email profile`: per Google, users need not be on the test list for these scopes, see no warning and do not hit the 7-day expiry. The allow-list in 1.2 remains the real gate | V | Google "Manage app audience" page |
| 1.9 | Alternative "Personal" consent-screen type is limited to the creator's account, and cannot be changed later | V (documented), U (fit) | Optional extra layer for the owner-only phase |
| 1.10 | Enrollment: the owner logs in once; the app shows the owner their own `sub` so it can be added to the allow-list file by the owner | D | Do not log tokens |
| 1.11 | Never expose, log or display ID/access tokens or `cookie_secret` ("Never expose tokens in your app's UI, logs, or error messages" per Streamlit docs) | V (rule), D | Test: grep logs for token-shaped strings |
| 1.12 | `redirect_uri` is HTTPS, exact-match, and `/oauth2callback` | V | Google requires exact match; HTTPS except localhost |
| 1.13 | Google accepts our redirect host (a `*.ts.net` name) | **U** | The rule "hosts must be on the public suffix list; no raw IPs" was seen only in a summary. Test: add the redirect URI in the Google console (free) |
| 1.14 | Streamlit auth works unchanged behind a reverse proxy / Tailscale Serve (WebSocket pass-through, forwarded scheme) | **U** | Docs are silent; Phase 1 spike |

## 2. Network exposure

| # | Control | Status | Notes |
|---|---|---|---|
| 2.1 | App listens on `127.0.0.1` only | D | Same pattern n8n's own compose file uses (`127.0.0.1:5678`) |
| 2.2 | Owner-only phase reachable only over Tailscale Serve (tailnet-only, not public); Funnel not used | V (Serve is tailnet-only) | Free Personal plan is for non-commercial use, up to 6 users |
| 2.3 | HTTPS certificate for the `*.ts.net` name via Tailscale. Machine names are published to Certificate Transparency | V | Do not put anything sensitive in the machine name |
| 2.4 | Do not use Docker-published ports for the new app; if Docker is ever used, bind `127.0.0.1:PORT:PORT` explicitly | V | Docker docs: published ports bypass ufw |
| 2.5 | No changes to n8n, Traefik or existing firewall rules | D | Separate approval for any exception |
| 2.6 | Tailscale Serve and Traefik both want 443 | **U** | Use a non-443 Serve port (for example 8443) to avoid a clash; Phase 1 spike |
| 2.7 | Cloudflare quick tunnels rejected: random changing hostname, no uptime guarantee, no SSE, "for testing" | V | |
| 2.8 | Let's Encrypt IP-address certificates exist (generally available since 2026, 6-day validity) but Google OAuth rejects raw-IP redirect hosts, so they do not solve login | V (both facts, the second from a summary) | |

## 3. Secrets

| # | Control | Status | Notes |
|---|---|---|---|
| 3.1 | Dedicated Gemini project and key for this app only | D | |
| 3.2 | Key restricted to the Generative Language (Gemini) API **and** to the VPS's public IP (IP is the only application restriction available for authorization keys) | V (features exist) | Google recommends both restrictions |
| 3.3 | Key stored root-owned mode 0600 (or a systemd credential), never in the repo, an image, a browser or a log. `EnvironmentFile=` is documented as unsuitable for secrets; `LoadCredential=` is the documented secure way | V | systemd.exec docs |
| 3.4 | The pipeline reads `GEMINI_API_KEY` from its environment, so the key must reach the child process environment. Keep it out of the web process's own environment where possible (read the credential at launch and pass it only to the child) | D | Optional stronger design: separate runner service |
| 3.5 | `cookie_secret` and OAuth `client_secret` in a root-owned 0600 file outside the repo | D | `secrets.toml` must never be committed |
| 3.6 | Key rotation runbook: create new key, update credential, restart, then delete the old key; do not delete the old key before the new one is active | V (Google's checklist) | |
| 3.7 | `.env` on the developer PC is separate from the VPS key; the VPS never receives the developer key | D | |

## 4. Process and host isolation (systemd option)

| # | Control | Status | Notes |
|---|---|---|---|
| 4.1 | Dedicated unprivileged user; no sudo | D | |
| 4.2 | `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, `ReadWritePaths=` only for the run folder and ledger | V (directives exist) | systemd.exec |
| 4.3 | `MemoryMax=` (last-line defence; use `MemoryHigh=` as the main control), `CPUQuota=`, `TasksMax=` so a run cannot starve n8n | V | systemd.resource-control |
| 4.4 | `IPAddressDeny=any` with `IPAddressAllow=` for the Gemini endpoints and the loopback/tailnet only | V (directive exists), U (needs eBPF support and hostname-to-IP handling) | May be impractical because Google IPs change; consider as optional |
| 4.5 | `RuntimeMaxSec=` on the service for a hard ceiling; a run-level watchdog kills the pipeline process group first | V (directive), D | |
| 4.6 | `SystemCallFilter=@system-service`, `SystemCallArchitectures=native` | V | Test that CrewAI and scikit-learn still run |
| 4.7 | Pipeline runs with a fixed scenario workbook; no visitor-supplied file ever reaches it | D | Existing `launch()` already enforces that the source lives in the uploads folder and matches a SHA-256 |

## 5. Abuse, budget and runaway control (details in the decision document)

| # | Control | Type | Status |
|---|---|---|---|
| 5.1 | One active run globally (exclusive-create lock file) | Preventative, immediate | V (existing code) |
| 5.2 | Max 2 run starts per user per day, counted at the start attempt | Preventative, immediate | D |
| 5.3 | Monthly start cap derived from the $5 target and a conservative per-run reserve | Preventative, immediate | D |
| 5.4 | Global kill switch (flag file checked at start and by the watchdog; plus `systemctl stop`) | Preventative, immediate | D |
| 5.5 | Per-call output-token cap (`max_output_tokens`) and the existing 30 s call / 90 s agent timeouts, plus a run watchdog | Preventative, immediate | D (cap is not set today) |
| 5.6 | Idempotent start: one-time request token in a persistent ledger, so double-clicks and refreshes re-attach to the existing run | Preventative | D |
| 5.7 | No automatic retries (existing `max_retries=0`, `max_retry_limit=0`) | Preventative | V (existing code) |
| 5.8 | Prepaid Gemini balance on a **dedicated billing account** | Delayed backstop | U (availability for your account unknown) |
| 5.9 | Project monthly spend cap in AI Studio ("experimental", about 10 minutes of overage possible) | Delayed backstop | V (documented limits) |
| 5.10 | Google budget alerts | Delayed alert only | V (alerts do not cap spend) |

## 6. Data, logging and disclosure

| # | Control | Status |
|---|---|---|
| 6.1 | Browser receives a sanitized status projection (allow-list of fields) and report files only; never raw logs, absolute paths or manifests | D |
| 6.2 | Reuse the public demo's leak scan on every projected status and report | D |
| 6.3 | Logs and run folders readable only by the service user; retention limit (for example delete runs after 30 days) | D |
| 6.4 | Scenario workbooks stay server-private; never published (CC BY-NC-SA 4.0; attribution kept) | D |
| 6.5 | Free-tier Gemini content may be used to improve Google products; a paid or prepaid project says it is not. The project is dedicated, so use paid/prepaid | V (Google billing page) |
| 6.6 | Do not reuse the full operator `app.py` (it has an upload widget and a raw log tail) | D |

## 7. Operations

| # | Control | Status |
|---|---|---|
| 7.1 | The live app is a separate deployment from the public demo; stopping it does not touch the demo | D |
| 7.2 | Pinned dependency versions; reproducible install | D |
| 7.3 | Backups of the ledger and allow-list; weekly Hostinger backups cover the VPS | U (confirm in hPanel) |
| 7.4 | OS security updates enabled | U (check with the inspection checklist) |
| 7.5 | Incident runbook: kill switch, rotate key, revoke Google client secret, remove user from allow-list | D |
| 7.6 | Linux test suite green before deployment. Phase 0 found 210 of 215 passing, caused by a CrewAI path-handling difference; Phase 1 fixed it in the repository branch and the full suite passes on Linux | V (offline) |
