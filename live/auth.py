"""Server-side authorization of a Google OpenID Connect identity (pure, no Streamlit import).

Streamlit's st.login authenticates the user and exposes the ID-token claims as st.user.
It does not authorize, and it does not check token expiry (its identity cookie lasts 30
days). Every check below is ours and must pass at every run start:

  - issuer is Google and audience is our OAuth client ID
  - sub (Google's stable, never-reused user ID) is on the allow-list; email is NOT the key,
    because Google says it "could change over time"
  - email_verified is true
  - the token has not expired, was not issued in the future, and the session is younger
    than our own maximum age (stricter than the 30-day cookie)
Any missing or malformed value denies. The reason is for server logs, never for the visitor.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping

GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
DEFAULT_MAX_SESSION_AGE_S = 12 * 3600
DEFAULT_SKEW_S = 60


@dataclass(frozen=True)
class AuthDecision:
    allowed: bool
    reason: str          # machine code for logs/tests, e.g. "OK", "NOT_ALLOWED", "EXPIRED"
    sub: str | None = None


def _get(claims: Any, name: str):
    try:
        return claims.get(name)
    except AttributeError:
        return getattr(claims, name, None)


def _epoch(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        return value.timestamp()
    return None


def _truthy_verified(value) -> bool:
    if value is True:
        return True
    return isinstance(value, str) and value.strip().lower() == "true"  # Google/Streamlit may give a string


def _audience_ok(aud, client_id: str) -> bool:
    if isinstance(aud, str):
        return aud == client_id
    if isinstance(aud, (list, tuple)):
        return client_id in aud
    return False


def authorize(claims: Mapping | Any, *, allowed_subs: Iterable[str], client_id: str, now: float,
              issuers: Iterable[str] = GOOGLE_ISSUERS, max_session_age_s: int = DEFAULT_MAX_SESSION_AGE_S,
              skew_s: int = DEFAULT_SKEW_S) -> AuthDecision:
    if not claims or not client_id:
        return AuthDecision(False, "NO_IDENTITY")
    if _get(claims, "is_logged_in") is False:
        return AuthDecision(False, "NO_IDENTITY")
    if _get(claims, "iss") not in set(issuers):
        return AuthDecision(False, "BAD_ISSUER")
    if not _audience_ok(_get(claims, "aud"), client_id):
        return AuthDecision(False, "BAD_AUDIENCE")
    sub = _get(claims, "sub")
    if not isinstance(sub, str) or not sub.strip():
        return AuthDecision(False, "MISSING_SUB")
    if not _truthy_verified(_get(claims, "email_verified")):
        return AuthDecision(False, "EMAIL_NOT_VERIFIED", sub)
    exp, iat = _epoch(_get(claims, "exp")), _epoch(_get(claims, "iat"))
    if exp is None or iat is None:
        return AuthDecision(False, "MISSING_TIMES", sub)
    if exp <= now:
        return AuthDecision(False, "EXPIRED", sub)
    if iat > now + skew_s:
        return AuthDecision(False, "ISSUED_IN_FUTURE", sub)
    if now - iat > max_session_age_s:
        return AuthDecision(False, "SESSION_TOO_OLD", sub)
    if sub not in set(allowed_subs):
        return AuthDecision(False, "NOT_ALLOWED", sub)
    return AuthDecision(True, "OK", sub)
