# V3 Phase 0: Read-only VPS inspection checklist

Status: **prepared, not executed.** No connection to the VPS has been made and nothing on it has been changed.

## Ground rules
1. Run only with the owner's explicit authorization, preferably by the owner. If an assistant runs it, the owner must say so for that session.
2. Every command below is read-only. None changes configuration, restarts a service, installs a package or opens a port.
3. **Never run** `docker inspect`, `docker compose config`, `docker exec ... env`, `cat .env`, `cat *.json` of n8n, or print `/proc/*/environ`. They can reveal secrets (n8n encryption key, database passwords, API tokens). The commands below were chosen so they cannot.
4. Save output to a file, read it yourself, and redact the server's public IP and any login IP addresses (the `last` output lists them) before sharing it.
5. Write the answers into the table in section M. The answers decide the deployment option (see section N).

## A. Operating system and hardware
```bash
lsb_release -a            # Ubuntu version (24.04 expected)
uname -r
systemd-detect-virt       # kvm expected
nproc; lscpu | grep -E 'Model name|^CPU\(s\)'
free -h; swapon --show    # free RAM and swap
df -h; df -i              # disk and inode headroom
uptime                    # load average right now
timedatectl | head -5     # clock synchronized? (needed for OIDC token checks)
```

## B. What is already using resources
```bash
ps -eo pid,user,%mem,%cpu,comm --sort=-%mem | head -15   # comm only: no command-line arguments
systemctl list-units --type=service --state=running --no-pager
systemctl list-timers --no-pager | head -15
```

## C. Existing n8n installation (do not use `docker inspect`)
```bash
docker --version 2>/dev/null
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Ports}}\t{{.Status}}'   # no environment variables shown
docker compose ls 2>/dev/null          # shows each project and its compose file path (not its contents)
docker network ls; docker volume ls
# If there is no Docker, look for a non-Docker n8n:
systemctl status n8n --no-pager 2>/dev/null | head -5; pm2 ls 2>/dev/null | head
```
Look for: how n8n is started (Docker Compose, plain Docker, systemd, pm2), whether Traefik is in the list, which ports are published and on which addresses (`127.0.0.1:5678->5678` is safe; `0.0.0.0:5678` is public).

Hostinger's n8n template is documented as Docker + Traefik with a hostname like `srvNNNNNN.hstgr.cloud`. This is an expectation to confirm, not a fact about your server.

## D. Ports and reverse proxy
```bash
sudo ss -tlnp             # listening TCP sockets with owning process
sudo ss -ulnp             # UDP
systemctl is-active nginx caddy apache2 traefik 2>/dev/null
```
Note who owns 80 and 443, and whether anything listens on 5678, 8501, 8443 or 8080.

## E. Firewall
```bash
sudo ufw status verbose
sudo nft list ruleset 2>/dev/null | head -120
sudo iptables -S 2>/dev/null | head -60
sudo iptables -L DOCKER-USER -n 2>/dev/null
```
Also check the **Hostinger hPanel firewall** (outside the server). Docker-published ports bypass ufw rules (Docker's documentation states this), so a ufw "deny" does not protect a container that publishes `0.0.0.0:PORT`.

## F. TLS as served today (external view; no secrets involved)
```bash
# replace HOST with the n8n hostname
openssl s_client -connect HOST:443 -servername HOST </dev/null 2>/dev/null | openssl x509 -noout -issuer -subject -dates
curl -sI https://HOST | head -5
```

## G. SSH and access hardening
```bash
sudo sshd -T | grep -E '^(port|passwordauthentication|permitrootlogin|pubkeyauthentication|allowusers)'
last -n 8
systemctl is-active fail2ban 2>/dev/null
```

## H. Updates, backups, monitoring
```bash
apt list --upgradable 2>/dev/null | wc -l
systemctl is-enabled unattended-upgrades 2>/dev/null
ls /etc/cron.d /etc/cron.daily 2>/dev/null | head -20
```
In hPanel: backup schedule, CPU-credit/usage graph (Hostinger throttles to 20% when credits are empty), current plan (KVM 1/2/4).

## I. Python and tooling (decides how to build the environment)
```bash
python3 --version; command -v python3.12 uv
dpkg -s python3-venv 2>&1 | grep -E 'Status|not installed'
```
`python3-venv` was **missing** on the Ubuntu 24.04 test machine, so a venv could not be created without `apt`. `uv` (a user-space installer) avoids needing root for this.

## J. Tailscale presence
```bash
tailscale version 2>/dev/null; tailscale status 2>/dev/null | head -5
```

## K. Platform prerequisites for the planned systemd service
```bash
systemctl --version | head -1            # need >= 247 for LoadCredential= (Ubuntu 24.04 ships 255)
stat -fc %T /sys/fs/cgroup               # "cgroup2fs" is needed for MemoryMax=/CPUQuota=/TasksMax= to work
systemctl --failed --no-pager | head      # anything already broken?
grep -E '^IPV6=' /etc/default/ufw 2>/dev/null   # IPV6=no means ufw rules do not cover IPv6 at all
id live-svc 2>&1 | head -1               # confirm the planned service user name is unused
mount | grep -E ' /(tmp|var/tmp) ' | head -3     # noexec on /tmp can break some Python wheels
```

## L. Outbound reachability (network requests, but no key and no data are sent)
```bash
getent hosts generativelanguage.googleapis.com accounts.google.com | head
curl -sS -o /dev/null -w 'generativelanguage: HTTP %{http_code}
' https://generativelanguage.googleapis.com/ || true
curl -sS -o /dev/null -w 'accounts.google.com: HTTP %{http_code}
' https://accounts.google.com/ || true
curl -sS -o /dev/null -w 'pypi: HTTP %{http_code}
' https://pypi.org/simple/ || true
```
A `404` or `403` from the Google API root is a good result: it proves the server can reach it. No API key is used.

## One review-first script
Read it, then run it yourself; it only runs the commands above and writes the output to one file.
```bash
#!/bin/bash
# V3 Phase 0 read-only inspection. Changes nothing. Output: ~/vps_inspection.txt (review and redact IPs).
exec > ~/vps_inspection.txt 2>&1
run() { echo; echo "===== $*"; "$@" 2>&1 | head -80; }
run lsb_release -a; run uname -r; run systemd-detect-virt; run nproc; run free -h; run swapon --show
run df -h; run uptime; run timedatectl
run systemctl --version; run stat -fc %T /sys/fs/cgroup; run systemctl --failed --no-pager
run docker ps --format 'table {{.Names}}	{{.Image}}	{{.Ports}}	{{.Status}}'
run docker compose ls; run docker network ls
run sudo ss -tlnp; run sudo ufw status verbose; run sudo iptables -L DOCKER-USER -n
run python3 --version; run bash -c 'dpkg -s python3-venv | head -3'; run bash -c 'command -v uv'
run bash -c 'tailscale version'
echo; echo "===== done"
# Deliberately NOT included: docker inspect, compose config, env files, /proc/*/environ, any file under n8n's data folder.
```

## M. Answer sheet

| Question | Answer |
|---|---|
| Ubuntu version / kernel | |
| vCPU / RAM total / RAM free / swap | |
| Disk free / inode headroom | |
| Plan (KVM 1/2/4) and CPU credit state | |
| How is n8n run (Compose, Docker, systemd, pm2)? n8n version | |
| Reverse proxy and what owns 80/443 | |
| Is 5678 bound to 127.0.0.1 or 0.0.0.0? | |
| ufw state; Docker published ports; hPanel firewall | |
| n8n HTTPS hostname and certificate issuer | |
| SSH: password auth? root login? fail2ban? | |
| Pending security updates; unattended upgrades | |
| Backups: schedule and last success | |
| python3-venv / uv available? | |
| Tailscale installed? | |
| systemd version; cgroup v2 present? | |
| IPv6 covered by the firewall? | |
| Can the server reach `generativelanguage.googleapis.com` and `accounts.google.com`? | |
| Planned service user name free? `/tmp` noexec? | |

## N. How the answers change the plan

| Finding | Effect |
|---|---|
| Free RAM below about 1.5 GB at normal load, or swap in heavy use | Do not co-host. Upgrade the plan or use a separate small VPS |
| n8n is Docker + Traefik owning 80/443 | Preferred path: systemd service on 127.0.0.1 plus Tailscale Serve, with no change to Traefik or n8n |
| n8n or any service published on `0.0.0.0` unnecessarily | Fix before adding any new service (separate approval) |
| ufw shows rules but Docker publishes public ports | Treat the firewall as not protecting containers; bind everything to `127.0.0.1` |
| Clock not synchronized | Fix first; OIDC `exp`/`iat` checks depend on it |
| CPU credits frequently exhausted | A run (about 70 s of CPU) may be slow; consider a larger plan |
| No backups | Enable before storing run artifacts or the ledger |
| systemd below 247 or no cgroup v2 | `LoadCredential=` or the resource limits are unavailable; use a root-owned 0600 file and a watchdog only, or a newer image |
| Google endpoints unreachable from the VPS | The live service cannot work there; check the Hostinger firewall and DNS first |
| ufw has `IPV6=no` while the server has a public IPv6 address | IPv6 services are unfiltered; fix before adding anything |
