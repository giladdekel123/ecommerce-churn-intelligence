"""Glue for the browser-facing process. Imports nothing that can call Gemini and holds no secret.

The web process authenticates the visitor (st.login) and forwards only the raw ID token; the worker
decides whether they may do anything. Tests replace current_id_token() and get_client().
"""
from __future__ import annotations

import os
import secrets

from live.ipc import SocketClient, WorkerClient

SOCKET_ENV = "LIVE_SOCKET_PATH"
DEFAULT_SOCKET = "/run/live/worker.sock"
FORBIDDEN_ENV = ("GEMINI_API_KEY", "GOOGLE_API_KEY")


def forbidden_secrets_present(environ=None) -> list[str]:
    """Names of Gemini key variables present in this process (there must be none)."""
    environ = os.environ if environ is None else environ
    return [name for name in FORBIDDEN_ENV if environ.get(name)]


def new_request_token() -> str:
    return secrets.token_urlsafe(16)   # 22 URL-safe characters


def get_client() -> WorkerClient:
    return WorkerClient(SocketClient(os.environ.get(SOCKET_ENV, DEFAULT_SOCKET)).call)


def current_id_token():
    """The visitor's raw Google ID token (needs `expose_tokens = "id"` in the auth settings), or None.

    The token is read at call time and passed straight to the worker; it is never stored in session state,
    logged or written anywhere by this process.
    """
    import streamlit as st
    user = st.user
    if not getattr(user, "is_logged_in", False):
        return None
    try:
        token = user.tokens.get("id")
    except AttributeError:
        return None
    return token if isinstance(token, str) and token else None


def is_logged_in() -> bool:
    import streamlit as st
    return bool(getattr(st.user, "is_logged_in", False))
