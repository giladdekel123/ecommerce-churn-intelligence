# V3 credential boundary: an isolated execution worker

Status: **design only.** Nothing here is implemented, installed or deployed, and no credential exists yet. It describes the next phase. Directives marked **(verify)** were not confirmed against official documentation in Phase 0 and must be checked before use.

## 1. The requirement and the threat
The Streamlit web process faces the internet (through Tailscale Serve at first), parses browser input, and holds the Google sign-in secrets. It must **never hold, or be able to read, `GEMINI_API_KEY`**. If the web process is compromised, the attacker must not be able to:
- read the Gemini key, the run folders, the logs or the ledger;
- make the key do anything other than run the two predefined scenarios inside the quotas;
- change the allow-list, the quotas or the kill switch.

Today `live/service.py` would start the pipeline in the same process that serves the web page, and `run_launcher.launch` passes the parent's whole environment to the pipeline (`env = {**os.environ, ...}`). That would put the key in the web process. The design below separates them.

## 2. Components and identities

```
Browser ──HTTPS (Tailscale Serve)──► live-web.service            user: live-web
                                       Streamlit + st.login, no Gemini key
                                       holds: OAuth client secret, cookie secret
                                            │  Unix socket, JSON lines, one request per connection
                                            ▼
                                     live-worker.service         user: live-worker
                                       LiveService + Supervisor (+ the pipeline as a child)
                                       holds: Gemini key (systemd credential)
                                       owns: ledger, runs, uploads, logs, scenario workbooks, allow-list
                                            │  child process, explicit environment
                                            ▼
                                     full_pipeline.py --run ...  (Crew 1 -> gate -> Crew 2)
```

| Identity | Can read | Cannot read |
|---|---|---|
| `live-web` | its own OAuth/cookie secrets; the socket | Gemini key, `/var/lib/live`, scenario workbooks, allow-list, logs |
| `live-worker` | Gemini key, ledger, runs, workbooks, allow-list | the web unit's OAuth/cookie secrets |
| Owner (admin, over SSH/Tailscale) | everything via `sudo` | n/a |

## 3. Files and permissions (proposed)

| Path | Owner / mode | Contents |
|---|---|---|
| `/etc/live/credentials/gemini_api_key` | root:root 0600 | the dedicated Gemini key; handed to the worker only through `LoadCredential=` |
| `/etc/live/credentials/oauth_client_secret`, `cookie_secret` | root:root 0600 | for the web unit only |
| `/etc/live/allowed_subs.json` | root:live-worker 0640 | allow-listed Google `sub` values |
| `/etc/live/config.json` | root:live-worker 0640 | client ID, limits, paths (no secrets) |
| `/var/lib/live/` | live-worker 0700 | `ledger.sqlite`, `runs/`, `uploads/`, `logs/`, `home/` |
| `/srv/live/scenarios/` | live-worker 0750 | the two pinned workbooks (CC BY-NC-SA derivatives; never published) |
| `/run/live/worker.sock` | live-worker:live-ipc 0660 | the only door between web and worker; `live-web` is in group `live-ipc` |
| `/run/live/KILL` | created by the owner | the kill switch flag; both units can read it |

The web unit also sets `InaccessiblePaths=/var/lib/live /srv/live /etc/live/credentials` **(verify)** as a second layer behind the file permissions.

## 4. The worker's interface (what crosses the boundary)

JSON lines over the Unix socket, one request per connection, hard limits (request at most 8 KB, 5 s to read it), strict schema, unknown fields rejected:

| Request | Fields | Response |
|---|---|---|
| `start` | `scenario_id`, `request_token`, `claims` | the existing `StartResult` (`status`, `message`, `run_id`) |
| `status` | `run_id`, `claims` | the existing sanitized projection |
| `usage` | `claims` | counters only |
| `ping` | none | worker alive, kill-switch state |

No file path, file name, command or environment value ever crosses it. The worker rebuilds every answer from its own files through `live/projection.py`, so the web process receives only already-sanitized JSON. On Linux the worker also checks the peer's user ID with `SO_PEERCRED` and refuses anyone but `live-web`.

### Who vouches for the user?
| | A. Web forwards the ID-token claims; the worker re-checks them against its own allow-list and limits | B. Web forwards the raw ID token; the worker verifies Google's signature itself |
|---|---|---|
| Trust in the web process | A compromised web can claim any allow-listed identity | None for identity |
| What an attacker gains from a compromised web | Can start only the two predefined scenarios, inside the quotas and budget; cannot read the key, logs or data | Same, but cannot forge identity either |
| Cost | Smallest worker | The worker needs a JWT/JWKS library and outbound access to Google's key endpoint, which enlarges the process that holds the key |
| **Recommendation** | ~~Use A for the owner-only phase~~ (superseded) | **Implemented in Phase 2 (see file 18, section 7.3).** The worker verifies the raw ID token |

## 5. How the key reaches the pipeline without touching the web process
1. systemd places the credential in a private, in-memory directory for the worker only (`LoadCredential=gemini_api_key:/etc/live/credentials/gemini_api_key`). systemd documents `LoadCredential=` as the secure route and `EnvironmentFile=` as unsuitable for secrets.
2. At launch time the worker reads that file and puts the value **only in the child's environment dictionary**. The worker's own `os.environ` never contains it.
3. The child's environment is an explicit allow-list, not a copy of the parent's: `PATH`, `LANG`, `PYTHONIOENCODING`, `PYTHONUTF8`, `HOME` (a private folder), `CREW1_GEMINI_MODEL`, `CREW_RECORD_TOKEN_USAGE=1`, `CREWAI_DISABLE_TELEMETRY=true`, `OTEL_SDK_DISABLED=true`, and `GEMINI_API_KEY`.
4. Today's code cannot do step 3: `run_launcher.launch` always inherits the parent's environment. **This is the first code change of the next phase** (an additive `env=` argument whose default keeps today's behaviour).
5. **No `.env` on the server.** `full_pipeline.py` loads `ROOT/.env` explicitly and the crews call `load_dotenv()`. The worker must refuse to start if a `.env` file exists in the deployed checkout, and the deployment must never copy the developer's.
6. Logs: the historical pipeline logs were checked (four logs, zero key-shaped strings, zero mentions of the variable name). The pipeline's log still never goes to the browser.

## 6. Proposed systemd units (sketch, not installed)

```ini
# live-worker.service
[Service]
User=live-worker
Group=live-worker
WorkingDirectory=/var/lib/live            # no .env here
LoadCredential=gemini_api_key:/etc/live/credentials/gemini_api_key
ExecStart=/opt/live/venv-worker/bin/python -m live.worker --config /etc/live/config.json
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths=/var/lib/live /run/live
MemoryHigh=1536M
MemoryMax=2G              # last line of defence; protects n8n
CPUQuota=150%
TasksMax=128
SystemCallFilter=@system-service
SystemCallArchitectures=native
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6      # (verify)
ProtectProc=invisible                                 # (verify)
ProcSubset=pid                                        # (verify)
Restart=on-failure
# KillMode=control-group is the default: a restart also stops the pipeline child.

# live-web.service
[Service]
User=live-web
Group=live-web
SupplementaryGroups=live-ipc
LoadCredential=oauth_client_secret:/etc/live/credentials/oauth_client_secret
LoadCredential=cookie_secret:/etc/live/credentials/cookie_secret
ExecStart=/opt/live/venv-web/bin/streamlit run live_app.py --server.address 127.0.0.1 --server.port 8501
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
InaccessiblePaths=/var/lib/live /srv/live /etc/live/credentials   # (verify)
MemoryMax=512M
```

The web unit's Python environment contains **no CrewAI, no Gemini client library and no scikit-learn**, so even a compromised web process has no tooling for calling Gemini.

## 7. Failure behaviour
| Event | Result |
|---|---|
| Worker down | The page says live execution is unavailable. It cannot start anything |
| Worker restarted while a run is active | systemd stops the pipeline child with it. On start, the worker's supervisor marks the row INTERRUPTED and **keeps the reservation** |
| Web compromised | See section 4: bounded to predefined scenarios inside quotas |
| Kill switch | `touch /run/live/KILL` stops new starts at once and stops a running job within one supervisor pass |
| Key suspected leaked | Rotate: create a new key, update the credential, restart the worker, then delete the old key. The key is restricted to the Gemini API and the server's IP address |

## 8. What the next phase must build

| # | Change | Where | Notes |
|---|---|---|---|
| 1 | `launch(..., env=None)`: if given, the child gets exactly that environment | `src/run_launcher.py` (additive) | Default unchanged; tests that a given env contains no inherited secrets |
| 2 | Socket protocol: schema, server, client, size and time limits, peer-credential check | new `live/ipc.py` | Fuzz tests; the peer check is Linux-only |
| 3 | Worker entry point: loads config, reads the credential from `$CREDENTIALS_DIRECTORY`, builds `LiveService` with a launcher that sets the child env, runs the supervisor loop and the socket | new `live/worker.py` | Refuses to start if `.env` exists, a file has loose permissions, or the credential is missing |
| 4 | Config loader and validator | new `live/config.py` | No secrets in it |
| 5 | Web front end using only the socket client and `st.login` | new `live/live_app.py` | No upload widget; no Gemini imports (a test enforces it) |
| 6 | Separate requirement files for the web and worker environments | `live/requirements-*.txt` | Web has no Gemini libraries |
| 7 | Unit-file templates and an install guide (not applied) | `deploy/` | Needs the directives marked **(verify)** checked |
| 8 | Tests | `tests/` | Child environment allow-list; key never in the worker's own environment; permission checks; socket abuse cases |
| 9 | Feed measured token counters into the ledger (`finish(actual_micro=...)`) | worker | See the spending review |

Decisions for the owner: socket protocol versus a database-backed request queue; identity trust model A versus B; whether the web and worker live in one repository checkout or two; whether to restrict the worker's outbound traffic with `IPAddressAllow` (Google's address ranges change, so this needs care).

## 9. Residual risks
- The pipeline's dependencies run in the worker with the key in their environment. A malicious dependency could exfiltrate it. Mitigations: pinned versions, the key restricted to the Gemini API and the server IP, a small prepaid balance, optional outbound allow-listing.
- The child and the worker share a user ID, so the child can read the worker's files. Acceptable because the child is our own pipeline code.
- A compromised web process can still spend the budget (bounded by the ledger) and deny service.
- Systemd behaviour (credentials, cgroup limits, restart semantics) is untested until deployment.
