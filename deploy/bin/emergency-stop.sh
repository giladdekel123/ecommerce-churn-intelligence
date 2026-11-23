#!/bin/sh
# Emergency stop for live execution. Run on the VPS as an administrator. Safe to run repeatedly.
#   1. engage the kill switch (no new starts; the supervisor stops a running job within seconds)
#   2. stop the web front end (nobody can reach the page)
# It does NOT touch n8n, Traefik, Docker, SSH or the firewall. To resume: scripts in the runbook, section 8.
set -eu
touch /etc/live/KILL
systemctl stop live-web.service
echo "Kill switch engaged and live-web stopped. A running job is stopped by the worker's supervisor within ~5 s."
echo "To stop the worker as well (this also stops any running pipeline): systemctl stop live-worker.service"
