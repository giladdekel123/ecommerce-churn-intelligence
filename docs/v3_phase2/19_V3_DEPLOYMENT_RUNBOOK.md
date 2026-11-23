# V3 deployment runbook (PREPARED, NOT EXECUTED)

Nothing in this file has been run. Every step needs the owner's explicit approval at the time. Do not enable UFW; do not change Docker, Traefik, SSH or n8n at any point. Directives marked **(verify)** need confirmation on the VPS (Ubuntu 24.04.5, systemd 255, 1 vCPU, 3.8 GB, n8n and Traefik in Docker, ports 80/443 held by Traefik, Tailscale absent).

## 1. Accounts, groups and folders
| Item | Value |
|---|---|
| Accounts | `live-worker`, `live-web` (system accounts, no shell, no home beyond their state folder) |
| Group | `live-ipc` (members: `live-worker`, `live-web`) |
| `/opt/live/app` | checkout of `main`, owned by root, read-only to both units; **no `.env`** |
| `/opt/live/venv-worker`, `/opt/live/venv-web` | separate environments. Web: `streamlit` only. Worker: `requirements.txt` |
| `/var/lib/live` | `live-worker` 0700: `ledger.sqlite`, `runs/`, `uploads/`, `logs/`, `outputs/`, `home/` |
| `/opt/live/app/runs` and `/opt/live/app/outputs` | symlinks to `/var/lib/live/runs` and `/var/lib/live/outputs` (required: the pipeline writes under its checkout) |
| `/srv/live/scenarios` | `live-worker` 0750; the two pinned workbooks (CC BY-NC-SA derivatives, never committed or published) |
| `/etc/live` | `root:live-worker` 0750: `config.json`, `allowed_subs.json` (0640), `credentials/gemini_api_key` (root 0600), `KILL` (created by the owner) |
| `/etc/live-web` | `root:live-web` 0750: `web.json` (0640), `credentials/oauth_client_secret`, `credentials/cookie_secret` (root 0600) |
| `/run/live` | created from `deploy/systemd/live.tmpfiles.conf`: `live-worker:live-ipc` 0750; the socket is 0660 |

## 1a. Network and package needs of the worker
- The worker verifies Google ID tokens itself, so it needs outbound HTTPS to `https://www.googleapis.com/oauth2/v3/certs` (the units allow IPv4/IPv6 sockets; no firewall change is planned). If that endpoint is unreachable the worker refuses every request (fail closed).
- The worker environment needs the `cryptography` package (present as a dependency of the pinned requirements; confirm with `pip show cryptography`). The web environment must not have it.
- A Google ID token is valid for about an hour: after that the page asks the visitor to sign in again.

## 2. Secret handling
- **Gemini key:** a new key created only for this service, restricted to the Gemini API (and the VPS IP address if the console offers it), in a dedicated prepaid project with a small balance. Written once by root into `/etc/live/credentials/gemini_api_key` (type it into the file with an editor; never put it on a command line, in shell history, in a chat, in a repository or in an environment file). Delivered to the worker only through `LoadCredential=`.
- **Google OAuth client secret and cookie secret** go in `/etc/live-web/credentials/` the same way; the cookie secret is 32+ random characters. They are rendered into Streamlit's `[auth]` secrets on a private tmpfs at each start and exist nowhere else.
- The web unit never receives the Gemini key; the worker never receives the web secrets (`InaccessiblePaths` and file ownership both enforce this).
- Rotation: create the new secret, replace the file, `systemctl restart` the unit, delete the old secret at its provider. If the Gemini key may have leaked, do this before anything else and engage the kill switch first.
- Never commit, paste or log secrets. Logs and the browser projection are redacted, but the redaction is a second line of defence.

## 3. Install sequence (when approved)
1. Create accounts, group, folders and permissions (section 1). `systemd-analyze verify` on both units **(verify)**.
2. Check out `main` into `/opt/live/app`; build the two environments; confirm `pip list` for the web environment shows no `crewai`, `google-genai` or `scikit-learn`.
3. Place configuration from `deploy/config/*.example.json` (replace every `REPLACE-*`), then secrets, then the two scenario workbooks (verify their SHA-256 values against `live/scenarios.py`).
4. Copy the units to `/etc/systemd/system/`, `systemctl daemon-reload`, **start the worker only**, check `journalctl -u live-worker`: it must report no refusal. It refuses to start if a `.env` exists, a file is loosely protected, the key is missing, or `<checkout>/runs` is not the configured folder.
5. Dry run without a model key is not possible with the real pipeline: use the offline test suite on the VPS (`python -m unittest tests.test_live_ipc tests.test_live_worker`), then a **supervised** first run owned by the owner with the prepaid balance as the cap.
6. Start `live-web`; verify it listens on 127.0.0.1:8501 only (`ss -ltnp`).

## 4. Tailscale Serve plan (does not touch Traefik or n8n)
- Traefik owns `0.0.0.0:80/443`, so Serve must not use 443: `tailscale serve --bg --https=8443 http://127.0.0.1:8501` **(verify the flag form for the installed version)**. Serve listens on the tailnet address only and proxies to localhost. **Do not use Funnel** (public exposure).
- Installing Tailscale adds a network interface, a daemon and firewall rules of its own; this is the one infrastructure change in the plan and needs separate approval. UFW stays disabled; Docker, Traefik, SSH and n8n are not reconfigured.
- The web unit's rendered Streamlit settings include `expose_tokens = "id"` so that the page can hand the raw ID token to the worker for verification.
- Google OAuth redirect URI: `https://<machine>.<tailnet>.ts.net:8443/oauth2callback`, in `/etc/live-web/web.json`. Whether Google accepts this host form is untested. If it does not, fallbacks that need no purchase: none that are reliable; a custom domain would have to be bought (not planned) or the front end hosted elsewhere.
- Only the owner's devices on the tailnet can reach the page; Google sign-in and the worker's allow-list still apply.

## 5. Verification after install (owner-run)
- Page reachable over the tailnet only; not from the public IP.
- Unsigned visit shows only the sign-in button. A second Google account is refused by the worker.
- `touch /etc/live/KILL`: the page reports "switched off" and Start fails; remove it to resume.
- First supervised run: record start/finish time, memory (`systemd-cgtop`), and the recorded token counters; compare with file 17's assumptions.
- `systemctl stop live-worker` while a run is active: the child pipeline stops; after restart the run shows INTERRUPTED and its reservation stays counted.

## 6. Rollback
1. `touch /etc/live/KILL`; `systemctl stop live-web live-worker`.
2. `systemctl disable` both units (if enabled); remove the units and `daemon-reload`.
3. Remove the Tailscale Serve mapping (`tailscale serve reset` **(verify)**); uninstalling Tailscale is optional.
4. Revoke the Gemini key at its provider; keep `/var/lib/live` for evidence or archive it.
5. n8n and Traefik are not part of the rollback because they are never changed.

## 7. Emergency stop
| Situation | Action |
|---|---|
| Unexpected spending or behaviour | `sh /opt/live/app/deploy/bin/emergency-stop.sh` (kill switch + stop front end; the worker's supervisor stops a running job within about 5 s) |
| Suspected key leak | Run the emergency stop, then revoke the key at the provider, then rotate (section 2) |
| Worker misbehaves | `systemctl stop live-worker` (stops any child run too) |
| Host short of memory | The units are first in line for the OOM killer (OOMScoreAdjust); n8n is not limited by these units |

## 8. Resume after an emergency stop
Investigate; remove `/etc/live/KILL`; `systemctl start live-worker live-web`. The ledger keeps every reservation made before the stop, so quotas stay honest.

## 9. Limits chosen for the small host
| Unit | MemoryHigh / Max | CPU | Tasks | Notes |
|---|---|---|---|---|
| live-worker | 1200 MB / 1536 MB | 80% of one CPU, low weight | 128 | no swap |
| live-web | 350 MB / 450 MB | 25% | 64 | no swap |
Total ceiling about 2 GB of 3.8 GB, leaving the rest to n8n, Traefik and the system. These are estimates; the first supervised run replaces them with measurements.
