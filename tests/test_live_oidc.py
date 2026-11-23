"""V3 Phase 2: worker-side verification of Google-signed ID tokens (offline: a throwaway RSA key stands in for Google)."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_support import (CLIENT_ID, NOW, OTHER_KEY, OWNER, TEST_KEY, TEST_KID, Clock, b64u, claims, jwks_for,  # noqa: E402
                          sign_token)

from live.oidc import JwksProvider, MAX_TOKEN_CHARS, TokenError, TokenVerifier  # noqa: E402


def code(verifier, token):
    try:
        verifier(token)
    except TokenError as exc:
        return exc.code
    return "ACCEPTED"


class VerifierTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.fetches = []

        def fetch():
            self.fetches.append(self.clock())
            return jwks_for(TEST_KEY)
        self.provider = JwksProvider(fetch, now=self.clock)
        self.verify = TokenVerifier(self.provider, audience=CLIENT_ID, now=self.clock)

    def test_a_genuine_token_is_accepted_and_verified_claims_are_returned(self):
        verified = self.verify(sign_token(claims()))
        self.assertEqual((verified["sub"], verified["is_logged_in"]), (OWNER, True))

    def test_a_token_cannot_claim_to_be_logged_in_on_its_own_authority(self):
        # Only the verifier sets is_logged_in; whatever the (genuinely signed) payload says is overwritten.
        self.assertTrue(self.verify(sign_token(claims(is_logged_in=False)))["is_logged_in"])

    def test_signature_forgery_is_rejected(self):
        self.assertEqual(code(self.verify, sign_token(claims(), key=OTHER_KEY)), "BAD_SIGNATURE")
        head, body, sig = sign_token(claims()).split(".")
        other_body = b64u(json.dumps(claims(sub="999999999999999999999")).encode())
        self.assertEqual(code(self.verify, f"{head}.{other_body}.{sig}"), "BAD_SIGNATURE")
        flipped = sig[:-3] + ("AAA" if not sig.endswith("AAA") else "BBB")
        self.assertEqual(code(self.verify, f"{head}.{body}.{flipped}"), "BAD_SIGNATURE")
        self.assertEqual(code(self.verify, f"{head}.{body}.{b64u(b'short')}"), "BAD_SIGNATURE")

    def test_claims_are_checked(self):
        cases = {"BAD_ISSUER": claims(iss="https://evil.example"), "BAD_AUDIENCE": claims(aud="other.apps.googleusercontent.com"),
                 "EXPIRED": claims(exp=NOW - 120), "ISSUED_IN_FUTURE": claims(iat=NOW + 600, exp=NOW + 4000),
                 "NO_SUBJECT": claims(sub=""), "BAD_TIMES": claims(exp="soon")}
        for expected, payload in cases.items():
            self.assertEqual(code(self.verify, sign_token(payload)), expected)
        missing_aud = claims()
        del missing_aud["aud"]
        self.assertEqual(code(self.verify, sign_token(missing_aud)), "BAD_AUDIENCE")
        self.assertEqual(code(self.verify, sign_token(claims(sub=12345))), "NO_SUBJECT")
        self.assertEqual(code(self.verify, sign_token(claims(exp=True))), "BAD_TIMES")
        self.assertEqual(code(self.verify, sign_token(claims(iss="accounts.google.com"))), "ACCEPTED")   # Google's second form

    def test_small_clock_skew_is_tolerated_but_not_more(self):
        self.assertEqual(code(self.verify, sign_token(claims(exp=NOW - 30))), "ACCEPTED")
        self.assertEqual(code(self.verify, sign_token(claims(exp=NOW - 90))), "EXPIRED")

    def test_unsupported_or_dangerous_tokens_are_rejected(self):
        body = b64u(json.dumps(claims()).encode())
        def raw(header, sig=b"x" * 256):
            return f"{b64u(json.dumps(header).encode())}.{body}.{b64u(sig)}"
        self.assertEqual(code(self.verify, raw({"alg": "none", "kid": TEST_KID}, b"")), "UNSUPPORTED_ALG")   # alg none, empty signature
        for alg in ("none", "HS256", "RS384", "ES256", "rs256", None):
            self.assertEqual(code(self.verify, raw({"alg": alg, "kid": TEST_KID})), "UNSUPPORTED_ALG", alg)
        for extra in ("jku", "x5u", "jwk", "x5c", "crit"):
            self.assertEqual(code(self.verify, raw({"alg": "RS256", "kid": TEST_KID, extra: "x"})), "UNSUPPORTED_HEADER")
        self.assertEqual(code(self.verify, raw({"alg": "RS256"})), "NO_KEY_ID")
        self.assertEqual(code(self.verify, raw({"alg": "RS256", "kid": 5})), "NO_KEY_ID")
        self.assertEqual(code(self.verify, raw({"alg": "RS256", "kid": "unknown"})), "UNKNOWN_KEY")

    def test_malformed_inputs_never_raise_anything_else(self):
        for bad in (None, 123, b"bytes", [], "", "a", "a.b", "a.b.c.d", "...", "a.b.c", "!!!.???.***",
                    "eyJhbGciOiJSUzI1NiJ9.e30.", "x" * (MAX_TOKEN_CHARS + 1), "é.é.é", "e30.e30.e30", "W10.W10.W10"):
            self.assertIn(code(self.verify, bad), ("MALFORMED", "MISSING_OR_OVERSIZED", "UNSUPPORTED_ALG", "NO_KEY_ID"), repr(bad)[:30])

    def test_a_token_that_is_not_a_json_object_is_refused(self):
        sig = b64u(b"x" * 256)
        header = b64u(json.dumps({"alg": "RS256", "kid": TEST_KID}).encode())
        self.assertEqual(code(self.verify, f"{header}.{b64u(b'[1,2]')}.{sig}"), "BAD_SIGNATURE")   # signature checked first
        signing = f"{header}.{b64u(b'[1,2]')}"
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        good_sig = b64u(TEST_KEY.sign(signing.encode(), padding.PKCS1v15(), hashes.SHA256()))
        self.assertEqual(code(self.verify, f"{signing}.{good_sig}"), "MALFORMED")


class KeyProviderTests(unittest.TestCase):
    def test_keys_are_cached_and_refreshed_after_the_ttl(self):
        clock, calls = Clock(), []
        provider = JwksProvider(lambda: calls.append(1) or jwks_for(TEST_KEY), now=clock, ttl_s=3600)
        verify = TokenVerifier(provider, audience=CLIENT_ID, now=clock)
        token = sign_token(claims(exp=NOW + 100000))
        for _ in range(5):
            verify(token)
        self.assertEqual(len(calls), 1)
        clock.advance(3601)
        verify(token)
        self.assertEqual(len(calls), 2)

    def test_unknown_key_ids_cannot_make_the_worker_hammer_google(self):
        clock, calls = Clock(), []
        provider = JwksProvider(lambda: calls.append(1) or jwks_for(TEST_KEY), now=clock, min_refresh_s=60)
        verify = TokenVerifier(provider, audience=CLIENT_ID, now=clock)
        for _ in range(50):
            self.assertEqual(code(verify, sign_token(claims(), kid="attacker-kid")), "UNKNOWN_KEY")
        self.assertEqual(len(calls), 1)
        clock.advance(61)
        code(verify, sign_token(claims(), kid="attacker-kid"))
        self.assertEqual(len(calls), 2)

    def test_key_rotation_is_picked_up(self):
        clock, state = Clock(), {"key": TEST_KEY, "kid": "k-old"}
        provider = JwksProvider(lambda: jwks_for(state["key"], state["kid"]), now=clock, min_refresh_s=60)
        verify = TokenVerifier(provider, audience=CLIENT_ID, now=clock)
        self.assertEqual(code(verify, sign_token(claims(), kid="k-old")), "ACCEPTED")
        state.update(key=OTHER_KEY, kid="k-new")
        clock.advance(120)
        self.assertEqual(code(verify, sign_token(claims(), key=OTHER_KEY, kid="k-new")), "ACCEPTED")

    def test_an_unreachable_key_endpoint_fails_closed_then_keeps_serving_cached_keys(self):
        clock, state = Clock(), {"up": False}
        def fetch():
            if not state["up"]:
                raise OSError("network down")
            return jwks_for(TEST_KEY)
        provider = JwksProvider(fetch, now=clock, ttl_s=100, min_refresh_s=10, max_stale_s=3600)
        verify = TokenVerifier(provider, audience=CLIENT_ID, now=clock)
        token = sign_token(claims(exp=NOW + 100000))
        self.assertEqual(code(verify, token), "KEYS_UNAVAILABLE")           # never fetched: refuse
        state["up"] = True
        clock.advance(20)
        self.assertEqual(code(verify, token), "ACCEPTED")
        state["up"] = False
        clock.advance(500)                                                  # stale but within the limit
        self.assertEqual(code(verify, token), "ACCEPTED")
        clock.advance(4000)                                                 # stale beyond the limit
        self.assertEqual(code(verify, token), "KEYS_UNAVAILABLE")

    def test_weak_or_malformed_keys_are_ignored(self):
        from cryptography.hazmat.primitives.asymmetric import rsa
        weak = rsa.generate_private_key(65537, 1024)
        for data in ({"keys": jwks_for(weak)["keys"]}, {"keys": [{"kty": "EC", "kid": "x"}]}, {"keys": "nope"}, {}, [], None):
            clock = Clock()
            verify = TokenVerifier(JwksProvider(lambda d=data: d, now=clock), audience=CLIENT_ID, now=clock)
            self.assertEqual(code(verify, sign_token(claims(), key=TEST_KEY)), "KEYS_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
