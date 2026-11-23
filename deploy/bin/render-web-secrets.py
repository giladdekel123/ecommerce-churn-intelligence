#!/usr/bin/env python3
"""Render Streamlit's [auth] secrets file for the web unit from systemd credentials (private tmpfs, mode 0600).

Reads the two secrets from $CREDENTIALS_DIRECTORY and the non-secret settings from a JSON file; writes
<out> (default under /run/live-web). Uses only the standard library so the web environment stays minimal.
Run by the unit's ExecStartPre; never writes a secret anywhere that survives a reboot.
"""
import argparse
import json
import os
import sys
from pathlib import Path

METADATA_URL = "https://accounts.google.com/.well-known/openid-configuration"


def toml_string(value: str) -> str:
    if any(c in value for c in "\r\n\x00"):
        raise ValueError("value contains a control character")
    return json.dumps(value)   # a JSON string is a valid TOML basic string


def render(config: dict, client_secret: str, cookie_secret: str) -> str:
    for name in ("client_id", "redirect_uri"):
        if not isinstance(config.get(name), str) or not config[name]:
            raise ValueError(f"web config is missing {name}")
    if not config["redirect_uri"].startswith("https://") or not config["redirect_uri"].endswith("/oauth2callback"):
        raise ValueError("redirect_uri must be an https URL ending in /oauth2callback")
    if len(cookie_secret) < 32:
        raise ValueError("cookie_secret must be at least 32 characters")
    lines = ["[auth]",
             f"redirect_uri = {toml_string(config['redirect_uri'])}",
             f"cookie_secret = {toml_string(cookie_secret)}",
             f"client_id = {toml_string(config['client_id'])}",
             f"client_secret = {toml_string(client_secret)}",
             f"server_metadata_url = {toml_string(METADATA_URL)}",
             # the worker verifies the raw ID token itself, so Streamlit must expose it to the page
             'expose_tokens = "id"',
             ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--credentials-dir", default=os.environ.get("CREDENTIALS_DIRECTORY"))
    args = parser.parse_args(argv)
    try:
        if not args.credentials_dir:
            raise ValueError("CREDENTIALS_DIRECTORY is not set")
        config = json.loads(args.config.read_text(encoding="utf-8"))
        secrets = {name: (Path(args.credentials_dir) / name).read_text(encoding="utf-8").strip()
                   for name in ("oauth_client_secret", "cookie_secret")}
        text = render(config, secrets["oauth_client_secret"], secrets["cookie_secret"])
        args.out.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.chmod(args.out, 0o600)
    except (OSError, ValueError) as exc:
        print(f"render-web-secrets: {exc}", file=sys.stderr)   # never prints a secret value
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
