"""Worker-side verification of a Google-signed OpenID Connect ID token (RS256).

The worker no longer trusts identity claims forwarded by the web process: it receives the raw ID token that
Streamlit exposes (st.user.tokens["id"]) and verifies it itself, then hands the VERIFIED claims to
live.auth.authorize (allow-list, verified e-mail, maximum session age).

Checked here: three base64url segments and a size limit; the header algorithm is exactly RS256 (so "none" and
HMAC downgrade tricks are refused) and names a key id; the RSA signature against Google's published keys; issuer;
audience (our OAuth client ID); not expired; not issued in the future; a subject. Keys come from an injected
fetcher (Google's JWKS endpoint in production, a local key in tests) with a cache; the worker is the only process
that needs this module, the network access and the `cryptography` package.

A token is never logged, stored or echoed. Failures carry short machine codes only.
"""
from __future__ import annotations

import base64
import json
import re
import threading
import time
from typing import Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from live.auth import GOOGLE_ISSUERS

GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
MAX_TOKEN_CHARS = 6000          # Google ID tokens are roughly 1 to 2 KB
MAX_JWKS_BYTES = 64 * 1024
_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")
_FORBIDDEN_HEADERS = ("jku", "x5u", "jwk", "x5c", "crit")   # key-injection vectors: never honoured, so refused


class TokenError(ValueError):
    """The token is not acceptable. `code` is a short machine reason for server logs, never for the visitor."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _b64(segment: str) -> bytes:
    if not segment or not _SEGMENT.fullmatch(segment):
        raise TokenError("MALFORMED")
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _json_object(segment: str) -> dict:
    try:
        value = json.loads(_b64(segment).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise TokenError("MALFORMED") from None
    if not isinstance(value, dict):
        raise TokenError("MALFORMED")
    return value


def _public_key(jwk: dict):
    if jwk.get("kty") != "RSA" or not isinstance(jwk.get("n"), str) or not isinstance(jwk.get("e"), str):
        return None
    try:
        numbers = rsa.RSAPublicNumbers(int.from_bytes(_b64(jwk["e"]), "big"), int.from_bytes(_b64(jwk["n"]), "big"))
        key = numbers.public_key()
    except (ValueError, TokenError):
        return None
    return key if key.key_size >= 2048 else None


def fetch_google_jwks() -> dict:
    """Download Google's signing keys over HTTPS (worker only; short timeout, size-limited)."""
    import urllib.request
    request = urllib.request.Request(GOOGLE_JWKS_URL, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=5) as response:   # noqa: S310 (fixed https URL)
        return json.loads(response.read(MAX_JWKS_BYTES + 1)[:MAX_JWKS_BYTES].decode("utf-8"))


class JwksProvider:
    """Caches signing keys by key id. An unknown key id triggers at most one refetch per `min_refresh_s`."""

    def __init__(self, fetch: Callable[[], dict] = fetch_google_jwks, *, now=time.time, ttl_s: int = 3600,
                 min_refresh_s: int = 60, max_stale_s: int = 86400):
        self._fetch, self._now = fetch, now
        self._ttl, self._min_refresh, self._max_stale = ttl_s, min_refresh_s, max_stale_s
        self._keys: dict = {}
        self._fetched_at: float | None = None
        self._attempted_at: float | None = None
        self._lock = threading.Lock()

    def _refresh(self) -> None:
        now = self._now()
        self._attempted_at = now
        try:
            data = self._fetch()
            entries = data.get("keys") if isinstance(data, dict) else None
            keys = {}
            for jwk in entries if isinstance(entries, list) else []:
                if isinstance(jwk, dict) and isinstance(jwk.get("kid"), str):
                    key = _public_key(jwk)
                    if key is not None:
                        keys[jwk["kid"]] = key
            if keys:
                self._keys, self._fetched_at = keys, now
        except Exception:   # network or format problem: keep serving cached keys within the stale limit
            pass

    def key_for(self, kid: str):
        with self._lock:
            now = self._now()
            if self._fetched_at is not None and now - self._fetched_at > self._max_stale:
                self._keys, self._fetched_at = {}, None          # too old to trust
            expired = self._fetched_at is None or now - self._fetched_at > self._ttl
            can_try = self._attempted_at is None or now - self._attempted_at >= self._min_refresh
            if (kid not in self._keys or expired) and can_try:
                self._refresh()
            key = self._keys.get(kid)
        if key is None:
            raise TokenError("KEYS_UNAVAILABLE" if not self._keys else "UNKNOWN_KEY")
        return key


class TokenVerifier:
    def __init__(self, keys: JwksProvider, *, audience: str, issuers=GOOGLE_ISSUERS, now=time.time, skew_s: int = 60):
        self._keys, self._audience, self._issuers = keys, audience, tuple(issuers)
        self._now, self._skew = now, skew_s

    def __call__(self, token) -> dict:
        """Return the verified claims (with is_logged_in set by us) or raise TokenError."""
        if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_CHARS:
            raise TokenError("MISSING_OR_OVERSIZED")
        parts = token.split(".")
        if len(parts) != 3:
            raise TokenError("MALFORMED")
        header = _json_object(parts[0])
        if header.get("alg") != "RS256":
            raise TokenError("UNSUPPORTED_ALG")
        if any(name in header for name in _FORBIDDEN_HEADERS):
            raise TokenError("UNSUPPORTED_HEADER")
        kid = header.get("kid")
        if not isinstance(kid, str) or not 1 <= len(kid) <= 128:
            raise TokenError("NO_KEY_ID")
        signature = _b64(parts[2])
        key = self._keys.key_for(kid)
        try:
            key.verify(signature, f"{parts[0]}.{parts[1]}".encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
        except InvalidSignature:
            raise TokenError("BAD_SIGNATURE") from None
        payload = _json_object(parts[1])
        now = self._now()
        if payload.get("iss") not in self._issuers:
            raise TokenError("BAD_ISSUER")
        if payload.get("aud") != self._audience:
            raise TokenError("BAD_AUDIENCE")
        exp, iat = payload.get("exp"), payload.get("iat")
        for value in (exp, iat):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TokenError("BAD_TIMES")
        if exp + self._skew < now:
            raise TokenError("EXPIRED")
        if iat - self._skew > now:
            raise TokenError("ISSUED_IN_FUTURE")
        sub = payload.get("sub")
        if not isinstance(sub, str) or not 1 <= len(sub) <= 255:
            raise TokenError("NO_SUBJECT")
        return {**payload, "is_logged_in": True}   # set by this verifier only; a token cannot claim it
