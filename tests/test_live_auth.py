"""V3 live core: server-side authorization of Google OIDC claims. Pure functions, no I/O."""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_support import CLIENT_ID, NOW, OTHER, OWNER, claims  # noqa: E402

from live.auth import authorize  # noqa: E402


def decide(c, **kw):
    args = dict(allowed_subs={OWNER}, client_id=CLIENT_ID, now=NOW)
    args.update(kw)
    return authorize(c, **args)


class AuthorizationTests(unittest.TestCase):
    def test_valid_allow_listed_owner_is_allowed(self):
        d = decide(claims())
        self.assertTrue(d.allowed)
        self.assertEqual((d.reason, d.sub), ("OK", OWNER))

    def test_denied_by_default(self):
        cases = {
            "no claims": (None, "NO_IDENTITY"),
            "empty claims": ({}, "NO_IDENTITY"),
            "not logged in": (claims(is_logged_in=False), "NO_IDENTITY"),
            "wrong issuer": (claims(iss="https://evil.example.com"), "BAD_ISSUER"),
            "missing issuer": (claims(iss=None), "BAD_ISSUER"),
            "wrong audience": (claims(aud="someone-elses-client"), "BAD_AUDIENCE"),
            "audience list without us": (claims(aud=["x", "y"]), "BAD_AUDIENCE"),
            "no sub": (claims(sub=None), "MISSING_SUB"),
            "empty sub": (claims(sub="  "), "MISSING_SUB"),
            "numeric sub": (claims(sub=12345), "MISSING_SUB"),
            "email not verified": (claims(email_verified=False), "EMAIL_NOT_VERIFIED"),
            "email_verified missing": (claims(email_verified=None), "EMAIL_NOT_VERIFIED"),
            "email_verified odd string": (claims(email_verified="yes"), "EMAIL_NOT_VERIFIED"),
            "expired": (claims(exp=NOW - 1), "EXPIRED"),
            "expires exactly now": (claims(exp=NOW), "EXPIRED"),
            "missing exp": (claims(exp=None), "MISSING_TIMES"),
            "missing iat": (claims(iat=None), "MISSING_TIMES"),
            "boolean exp": (claims(exp=True), "MISSING_TIMES"),
            "string exp": (claims(exp="9999999999"), "MISSING_TIMES"),
            "issued in the future": (claims(iat=NOW + 3600), "ISSUED_IN_FUTURE"),
            "session too old": (claims(iat=NOW - 13 * 3600, exp=NOW + 100), "SESSION_TOO_OLD"),
            "not on the allow-list": (claims(sub=OTHER), "NOT_ALLOWED"),
        }
        for name, (c, reason) in cases.items():
            d = decide(c)
            self.assertFalse(d.allowed, name)
            self.assertEqual(d.reason, reason, name)

    def test_empty_allow_list_denies_everyone(self):
        self.assertEqual(decide(claims(), allowed_subs=set()).reason, "NOT_ALLOWED")

    def test_email_is_never_the_key(self):
        # An allow-listed e-mail address with an unlisted sub must not pass, and vice versa.
        self.assertFalse(decide(claims(sub=OTHER, email="owner@example.com"), allowed_subs={"owner@example.com"}).allowed)
        self.assertTrue(decide(claims(sub=OWNER, email="changed@example.com")).allowed)

    def test_missing_client_id_configuration_denies(self):
        self.assertFalse(decide(claims(), client_id="").allowed)

    def test_accepted_formats(self):
        self.assertTrue(decide(claims(email_verified="true")).allowed)       # Google/Streamlit may send a string
        self.assertTrue(decide(claims(email_verified="True")).allowed)
        self.assertTrue(decide(claims(aud=[CLIENT_ID, "other"])).allowed)
        self.assertTrue(decide(claims(iss="accounts.google.com")).allowed)
        stamp = datetime.fromtimestamp(NOW + 500, timezone.utc)
        self.assertTrue(decide(claims(exp=stamp)).allowed)                   # datetime claims
        self.assertTrue(decide(claims(iat=NOW - 60.5, exp=NOW + 100.5)).allowed)

    def test_attribute_style_claims_like_st_user(self):
        c = SimpleNamespace(**claims())  # no .get method: exercises the attribute path
        self.assertTrue(decide(c).allowed)

    def test_session_age_boundary_and_clock_skew(self):
        self.assertTrue(decide(claims(iat=NOW - 12 * 3600, exp=NOW + 100)).allowed)
        self.assertFalse(decide(claims(iat=NOW - 12 * 3600 - 1, exp=NOW + 100)).allowed)
        self.assertTrue(decide(claims(iat=NOW + 59)).allowed)     # within the 60 s skew allowance
        self.assertFalse(decide(claims(iat=NOW + 61)).allowed)
        self.assertFalse(decide(claims(iat=NOW - 4000), max_session_age_s=3600).allowed)

    def test_decision_never_echoes_secrets(self):
        d = decide(claims(sub=OTHER))
        self.assertEqual(set(vars(d)), {"allowed", "reason", "sub"})


if __name__ == "__main__":
    unittest.main()
