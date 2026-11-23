"""The only door between the web process and the execution worker.

One JSON request per connection over a Unix socket (Linux). Hard limits: request at most 8 KB, 5 s to
send it, strict schema, unknown fields rejected. No path, file name, command or environment value ever
crosses it, and answers are rebuilt by the worker (live.projection), so the web side only receives
already-sanitized JSON.

Identity (model B): the web process forwards the raw Google ID token (st.user.tokens["id"]). The worker verifies
its signature, issuer, audience and expiry itself (live.oidc), then authorizes the verified claims against its own
allow-list, limits and clock. Claims sent by the web process are never trusted: the protocol has no claims field.
The worker also checks the connecting process's user ID (SO_PEERCRED) and serves only the web service account.
Tokens are never logged or stored; denial reasons are logged as short codes.

The pure dispatcher (handle_request) has no socket, so it is tested on every platform; the socket server
and client are POSIX-only.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import struct
import threading
import time
from typing import Callable

MAX_REQUEST_BYTES = 16 * 1024
MAX_TOKEN_CHARS = 6000
MAX_RESPONSE_BYTES = 512 * 1024
READ_TIMEOUT_S = 5.0
log = logging.getLogger("live.ipc")
_STR = (str,)
_SCHEMA = {  # op -> {field: type}, all fields required; "id_token" is added for every op except ping
    "ping": {},
    "start": {"scenario_id": _STR, "request_token": _STR},
    "status": {"run_id": _STR},
    "latest": {},
    "usage": {},
}


class ProtocolError(ValueError):
    """The request is malformed; answered with a generic error and never executed."""


def parse_request(raw: bytes) -> dict:
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_REQUEST_BYTES:
        raise ProtocolError("too large")
    try:
        message = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ProtocolError("not json") from None
    if not isinstance(message, dict) or not isinstance(message.get("op"), str) or message["op"] not in _SCHEMA:
        raise ProtocolError("unknown op")
    op = message["op"]
    allowed = {"op", *_SCHEMA[op], *(() if op == "ping" else ("id_token",))}
    if set(message) - allowed:
        raise ProtocolError("unknown field")
    for name, types in _SCHEMA[op].items():
        value = message.get(name)
        if not isinstance(value, types) or len(value) > 128:
            raise ProtocolError(f"bad {name}")
    if op != "ping":
        token = message.get("id_token")
        if not isinstance(token, str) or not 1 <= len(token) <= MAX_TOKEN_CHARS:
            raise ProtocolError("bad id_token")
    return message


def handle_request(service, raw: bytes, verifier) -> dict:
    """Answer one request. Never raises; failures become short, generic, non-revealing answers.

    `verifier` turns a raw ID token into verified claims or raises live.oidc.TokenError (all ops but ping).
    """
    try:
        message = parse_request(raw)
    except ProtocolError:
        return {"ok": False, "error": "BAD_REQUEST"}
    op = message["op"]
    try:
        if op == "ping":
            return {"ok": True, "kill_switch_engaged": service.kill_switch.engaged()}
        try:
            claims = verifier(message["id_token"])
        except ValueError as exc:   # live.oidc.TokenError
            log.info("token rejected: %s", getattr(exc, "code", "INVALID"))   # the code only, never the token
            return {"ok": False, "error": "UNAUTHENTICATED"}
        if op == "start":
            result = service.start(claims, message["scenario_id"], message["request_token"])
            return {"ok": True, "status": result.status, "message": result.message, "run_id": result.run_id}
        if op == "status":
            return {"ok": True, "view": service.status(claims, message["run_id"])}
        if op == "latest":
            return {"ok": True, "latest": service.latest(claims)}
        return {"ok": True, "usage": service.usage(claims)}
    except PermissionError:
        return {"ok": False, "error": "DENIED"}
    except Exception:  # never leak a traceback or path across the boundary
        return {"ok": False, "error": "INTERNAL"}


def encode_response(response: dict) -> bytes:
    data = json.dumps(response, separators=(",", ":")).encode("utf-8")
    if len(data) > MAX_RESPONSE_BYTES:
        data = json.dumps({"ok": False, "error": "TOO_LARGE"}).encode("utf-8")
    return data


def _peer_uid(connection) -> int | None:
    try:
        creds = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        return struct.unpack("3i", creds)[1]
    except (AttributeError, OSError):
        return None


def _read_request(connection) -> bytes:
    deadline = time.monotonic() + READ_TIMEOUT_S
    data = b""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProtocolError("timeout")
        connection.settimeout(remaining)
        try:
            chunk = connection.recv(4096)
        except OSError:
            raise ProtocolError("read failed") from None
        if not chunk:
            return data
        data += chunk
        if len(data) > MAX_REQUEST_BYTES:
            raise ProtocolError("too large")


class SocketServer:
    """Serves handle_request on a Unix socket. Requests are handled one at a time (single-owner service)."""

    def __init__(self, service, path, verifier, *, allowed_uids=None, mode=0o660, group=None):
        if not hasattr(socket, "AF_UNIX"):
            raise RuntimeError("Unix sockets are not available on this platform")
        self.service, self.path, self.verifier = service, str(path), verifier
        self.allowed_uids = None if allowed_uids is None else frozenset(allowed_uids)
        self._mode, self._group = mode, group
        self._server = None
        self._stop = threading.Event()

    def bind(self) -> None:
        if os.path.exists(self.path):
            os.unlink(self.path)  # a stale socket from a previous run
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old_umask = os.umask(0o117)
        try:
            server.bind(self.path)
        finally:
            os.umask(old_umask)
        os.chmod(self.path, self._mode)
        if self._group is not None:
            os.chown(self.path, -1, self._group)
        server.listen(8)
        server.settimeout(0.5)
        self._server = server

    def serve_forever(self) -> None:
        if self._server is None:
            self.bind()
        while not self._stop.is_set():
            try:
                connection, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with connection:
                self._serve_one(connection)

    def _serve_one(self, connection) -> None:
        if self.allowed_uids is not None and _peer_uid(connection) not in self.allowed_uids:
            response = {"ok": False, "error": "DENIED"}  # wrong or unknown peer: never parsed, never executed
        else:
            try:
                response = handle_request(self.service, _read_request(connection), self.verifier)
            except ProtocolError:
                response = {"ok": False, "error": "BAD_REQUEST"}
        try:
            connection.settimeout(READ_TIMEOUT_S)
            connection.sendall(encode_response(response))
        except OSError:
            pass

    def stop(self) -> None:
        self._stop.set()
        if self._server is not None:
            self._server.close()
        try:
            os.unlink(self.path)
        except OSError:
            pass


class SocketClient:
    """What the web process uses. Holds no secret; every call is one short connection."""

    def __init__(self, path, *, timeout_s: float = 10.0):
        self.path, self.timeout_s = str(path), timeout_s

    def call(self, message: dict) -> dict:
        if not hasattr(socket, "AF_UNIX"):
            return {"ok": False, "error": "UNAVAILABLE"}
        payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(self.timeout_s)
                sock.connect(self.path)
                sock.sendall(payload)
                sock.shutdown(socket.SHUT_WR)
                chunks, total = [], 0
                while True:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_RESPONSE_BYTES:
                        return {"ok": False, "error": "TOO_LARGE"}
                    chunks.append(chunk)
            value = json.loads(b"".join(chunks).decode("utf-8"))
            return value if isinstance(value, dict) else {"ok": False, "error": "BAD_RESPONSE"}
        except (OSError, ValueError):
            return {"ok": False, "error": "UNAVAILABLE"}


class WorkerClient:
    """Typed calls for the web front end. `transport` is any callable(dict) -> dict (a SocketClient.call, or a fake).

    Every call except ping carries the visitor's raw ID token and nothing else about identity.
    """

    def __init__(self, transport: Callable[[dict], dict]):
        self._call = transport

    def ping(self):
        return self._call({"op": "ping"})

    def start(self, id_token, scenario_id, request_token):
        return self._call({"op": "start", "id_token": id_token, "scenario_id": scenario_id,
                           "request_token": request_token})

    def status(self, id_token, run_id):
        return self._call({"op": "status", "id_token": id_token, "run_id": run_id})

    def latest(self, id_token):
        return self._call({"op": "latest", "id_token": id_token})

    def usage(self, id_token):
        return self._call({"op": "usage", "id_token": id_token})
