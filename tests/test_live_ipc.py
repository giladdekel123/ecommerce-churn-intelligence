"""V3 Phase 2: the worker's socket protocol. The dispatcher is tested on every platform; real Unix sockets on POSIX."""
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_support import (CLIENT_ID, NOW, OTHER, OTHER_KEY, OWNER, TEST_KID, ClaimsClient, Fixture, claims,  # noqa: E402
                          jwks_for, make_verifier, sign_token, token, transport_for)

from live import ipc  # noqa: E402
from live.ipc import MAX_REQUEST_BYTES, WorkerClient, handle_request, parse_request, ProtocolError  # noqa: E402

POSIX = hasattr(socket, "AF_UNIX") and os.name != "nt"


def wire(service):
    return ClaimsClient(transport_for(service))


class ProtocolSchemaTests(unittest.TestCase):
    def good(self, **kw):
        msg = {"op": "start", "id_token": sign_token(claims()), "scenario_id": "pass", "request_token": token()}
        msg.update(kw)
        return json.dumps(msg).encode()

    def test_a_valid_request_parses(self):
        self.assertEqual(parse_request(self.good())["op"], "start")
        self.assertEqual(parse_request(b'{"op":"ping"}')["op"], "ping")

    def test_malformed_requests_are_rejected(self):
        bad = [b"", b"not json", b"[]", b"null", b'"x"', b'{"op":"nope"}', b'{"op":5}', b"\xff\xfe",
               b'{"op":"ping","extra":1}',                       # unknown field
               self.good(path="/etc/passwd"),                    # a path may never cross
               self.good(command="rm -rf /"),
               self.good(scenario_id=7), self.good(request_token=None), self.good(scenario_id="x" * 129),
               self.good(claims={"sub": OWNER}),                 # forwarded claims are not part of the protocol at all
               self.good(id_token=5), self.good(id_token=""), self.good(id_token="x" * 6001),
               json.dumps({"op": "start", "scenario_id": "pass", "request_token": token()}).encode(),   # no token
               b'{"op":"status","id_token":"a.b.c"}',            # missing run_id
               b"x" * (MAX_REQUEST_BYTES + 1)]
        for raw in bad:
            with self.assertRaises(ProtocolError, msg=raw[:60]):
                parse_request(raw)

    def test_bad_requests_get_a_generic_answer(self):
        fx = None
        class Boom:
            def __getattr__(self, name):
                raise AssertionError("service must not be touched for a malformed request")
        for raw in (b"garbage", b'{"op":"start"}', b"{" * 5000):
            self.assertEqual(handle_request(Boom(), raw, None), {"ok": False, "error": "BAD_REQUEST"})

    def test_the_client_sends_only_the_raw_token_about_identity(self):
        sent = []
        WorkerClient(lambda m: sent.append(m) or {"ok": True}).start("a.b.c", "pass", token())
        self.assertEqual(set(sent[0]), {"op", "id_token", "scenario_id", "request_token"})
        self.assertEqual(sent[0]["id_token"], "a.b.c")


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture(self)
        self.client = wire(self.fx.service)

    def start(self, who=OWNER, scenario="pass", tok=None, **claim_overrides):
        return self.client.start(claims(who, **claim_overrides), scenario, tok or token())

    @staticmethod
    def rejected(response):
        return (response.get("ok") is False and response.get("error") in ("UNAUTHENTICATED", "DENIED")) or \
               (response.get("ok") is True and response.get("status") == "DENIED")

    def test_unauthorized_clients_cannot_launch(self):
        raw = WorkerClient(transport_for(self.fx.service))
        good = claims()
        cases = {
            "not on the allow-list": self.start(OTHER),
            "e-mail not verified": self.start(email_verified=False),
            "wrong audience": self.start(aud="someone-elses-client.apps.googleusercontent.com"),
            "expired": self.start(exp=NOW - 600),
            "wrong issuer": self.start(iss="https://evil.example"),
            "issued in the future": self.start(iat=NOW + 3600, exp=NOW + 7200),
            "no identity": self.client.start({}, "pass", token()),
            "no audience": self.client.start({"sub": OWNER, "iss": good["iss"], "exp": NOW + 100, "iat": NOW}, "pass", token()),
            "session too old": self.start(iat=NOW - 13 * 3600, exp=NOW + 100),
            "forged (attacker's key)": raw.start(sign_token(good, key=OTHER_KEY), "pass", token()),
            "unknown key id": raw.start(sign_token(good, kid="not-a-google-key"), "pass", token()),
            "tampered payload": raw.start(self.tampered(good), "pass", token()),
            "garbage": raw.start("not.a.token", "pass", token()),
        }
        for name, response in cases.items():
            self.assertTrue(self.rejected(response), (name, response))
        self.assertEqual(self.fx.launches, [])
        self.assertEqual(self.fx.ledger.active_rows(), [])

    def tampered(self, payload):
        """A genuine signature over one payload, attached to another (someone else's identity)."""
        genuine = sign_token(payload).split(".")
        forged = sign_token({**payload, "sub": "999999999999999999999"}).split(".")
        return ".".join([genuine[0], forged[1], genuine[2]])

    def test_every_operation_requires_a_verified_token(self):
        raw = WorkerClient(transport_for(self.fx.service))
        started = self.start()
        forged = sign_token(claims(), key=OTHER_KEY)
        for response in (raw.start(forged, "pass", token()), raw.status(forged, started["run_id"]),
                         raw.latest(forged), raw.usage(forged),
                         raw.status("x.y.z", started["run_id"])):
            self.assertEqual(response.get("error"), "UNAUTHENTICATED", response)
        self.assertEqual(raw.latest("")["error"], "BAD_REQUEST")      # no token at all never reaches verification
        self.assertEqual(len(self.fx.launches), 1)

    def test_alg_none_and_hmac_downgrades_and_key_injection_headers_are_refused(self):
        raw = WorkerClient(transport_for(self.fx.service))
        from live_support import b64u
        body = b64u(json.dumps(claims()).encode())
        none_token = f"{b64u(json.dumps({'alg': 'none', 'kid': TEST_KID}).encode())}.{body}."
        hs_token = f"{b64u(json.dumps({'alg': 'HS256', 'kid': TEST_KID}).encode())}.{body}.{b64u(b'x' * 32)}"
        for bad in (none_token, hs_token, sign_token(claims(), header={"jku": "https://evil.example/keys"}),
                    sign_token(claims(), header={"jwk": {"kty": "RSA"}}), sign_token(claims(), header={"alg": "RS512"}),
                    sign_token(claims(), kid=""), sign_token(claims(), header={"crit": ["x"]})):
            self.assertEqual(raw.start(bad, "pass", token()).get("error"), "UNAUTHENTICATED")
        self.assertEqual(self.fx.launches, [])

    def test_status_and_reports_are_authorized_not_just_starts(self):
        started = self.start()
        raw = WorkerClient(transport_for(self.fx.service))
        self.assertEqual(raw.status(sign_token(claims(OTHER)), started["run_id"]), {"ok": False, "error": "DENIED"})
        self.assertEqual(raw.status(sign_token(claims(), key=OTHER_KEY), started["run_id"])["error"], "UNAUTHENTICATED")
        self.assertEqual(raw.status(sign_token(claims(exp=NOW - 600)), started["run_id"])["error"], "UNAUTHENTICATED")
        self.assertTrue(raw.status(sign_token(claims()), started["run_id"])["ok"])

    def test_an_expired_sign_in_stops_working_as_time_passes(self):
        started = self.start()
        short = claims(exp=NOW + 120)
        self.assertTrue(self.client.status(short, started["run_id"])["ok"])
        self.fx.clock.advance(300)                                  # beyond expiry plus clock skew
        self.assertEqual(self.client.status(short, started["run_id"]), {"ok": False, "error": "UNAUTHENTICATED"})

    def test_a_token_is_never_written_back_in_answers_the_ledger_or_logs(self):
        import logging
        token_value = sign_token(claims())
        raw = WorkerClient(transport_for(self.fx.service))
        with self.assertLogs("live.ipc", level="INFO") as logs:
            bad = sign_token(claims(), key=OTHER_KEY)
            replies = [raw.start(token_value, "pass", token()), raw.status(token_value, "20270101T000000Z_deadbeef"),
                       raw.usage(token_value), raw.latest(token_value), raw.usage(bad)]
        blob = json.dumps(replies) + "\n".join(logs.output)
        for secret in (token_value, bad, token_value.split(".")[1], token_value.split(".")[2]):
            self.assertNotIn(secret, blob)
        self.assertIn("BAD_SIGNATURE", blob)                        # the reason code is logged, not the token
        database = Path(self.fx.ledger.path if hasattr(self.fx.ledger, "path") else self.fx.base / "ledger.sqlite")
        if database.exists():
            self.assertNotIn(token_value.split(".")[2].encode(), database.read_bytes())

    def test_a_forged_sub_that_is_not_allowed_is_refused_even_with_valid_looking_claims(self):
        self.assertEqual(self.start("999999999999999999999")["status"], "DENIED")

    def test_start_then_status_for_the_owner(self):
        started = self.start()
        self.assertEqual((started["ok"], started["status"]), (True, "STARTED"))
        view = self.client.status(claims(), started["run_id"])
        self.assertTrue(view["ok"])
        self.assertEqual(view["view"]["run_id"], started["run_id"])

    def test_duplicate_requests_launch_one_run(self):
        tok = token()
        results = [self.start(tok=tok) for _ in range(5)]
        self.assertEqual([r["status"] for r in results], ["STARTED"] + ["REATTACHED"] * 4)
        self.assertEqual(len({r["run_id"] for r in results}), 1)
        self.assertEqual(len(self.fx.launches), 1)

    def test_simultaneous_distinct_requests_launch_one_run(self):
        results, barrier = [], threading.Barrier(8)
        def go():
            barrier.wait()
            results.append(self.start())
        threads = [threading.Thread(target=go) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(self.fx.launches), 1)
        self.assertEqual(sorted(r["status"] for r in results), ["BUSY"] * 7 + ["STARTED"])

    def test_quota_budget_and_kill_switch_apply_through_the_boundary(self):
        self.assertEqual(self.start()["status"], "STARTED")
        self.fx.finish_active()
        self.assertEqual(self.start()["status"], "STARTED")
        self.fx.finish_active()
        self.assertEqual(self.start()["status"], "DAILY_LIMIT")           # two per day
        self.assertEqual(len(self.fx.launches), 2)
        fx2 = Fixture(self)
        fx2.kill.engage("test")
        self.assertEqual(wire(fx2.service).start(claims(), "pass", token())["status"], "DISABLED")
        self.assertEqual(fx2.launches, [])

    def test_unknown_scenarios_cannot_be_named(self):
        for bad in ("../../etc/passwd", "PASS", "all", "", "x" * 40):
            self.assertEqual(self.start(scenario=bad)["status"], "INVALID_REQUEST", bad)
        self.assertEqual(self.fx.launches, [])

    def test_status_is_denied_for_other_users_and_unknown_runs(self):
        started = self.start()
        wrong = self.client.status(claims(OTHER), started["run_id"])
        self.assertEqual(wrong, {"ok": False, "error": "DENIED"})
        for bad in ("20270101T000000Z_deadbeef", "../x", "runs", ""):
            self.assertEqual(self.client.status(claims(), bad), {"ok": False, "error": "DENIED"})

    def test_latest_lets_a_refreshed_browser_reattach(self):
        self.assertIsNone(self.client.latest(claims())["latest"])
        started = self.start()
        latest = self.client.latest(claims())["latest"]
        self.assertEqual((latest["run_id"], latest["scenario_id"]), (started["run_id"], "pass"))
        self.assertEqual(self.client.latest(claims(OTHER)), {"ok": False, "error": "DENIED"})

    def test_usage_and_ping(self):
        self.assertTrue(self.client.ping()["ok"])
        self.assertEqual(self.client.usage(claims())["usage"]["daily_limit"], 2)
        self.assertEqual(self.client.usage(claims(OTHER)), {"ok": False, "error": "DENIED"})

    def test_internal_errors_never_leak_details(self):
        def explode(*a, **k):
            raise RuntimeError(r"secret C:\Users\x\.env AIza" + "SyD-leak")
        with patch.object(self.fx.service, "usage", explode):
            response = self.client.usage(claims())
        self.assertEqual(response, {"ok": False, "error": "INTERNAL"})

    def test_answers_carry_no_identity_paths_or_internals(self):
        started = self.start()
        dumped = json.dumps([started, self.client.status(claims(), started["run_id"]), self.client.latest(claims()),
                             self.client.usage(claims()), self.client.ping()])
        for needle in (OWNER, str(self.fx.base), "request_token", "someone@example.com", "ledger", "KILL"):
            self.assertNotIn(needle, dumped, needle)


@unittest.skipUnless(POSIX, "Unix sockets: exercised on Linux")
class SocketTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture(self)
        tmp = tempfile.TemporaryDirectory(dir="/tmp" if os.path.isdir("/tmp") else None)  # short path: AF_UNIX limit
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "w.sock")

    def serve(self, **kw):
        server = ipc.SocketServer(self.fx.service, self.path, self.fx.verifier, **kw)
        server.bind()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.stop(), thread.join(2)))
        return server

    def raw(self, payload: bytes, *, shutdown=True) -> bytes:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect(self.path)
            s.sendall(payload)
            if shutdown:
                s.shutdown(socket.SHUT_WR)
            return s.recv(65536)

    def test_round_trip_and_socket_permissions(self):
        self.serve(allowed_uids={os.getuid()})
        self.assertEqual(stat_mode(self.path), 0o660)
        client = ClaimsClient(ipc.SocketClient(self.path).call)
        self.assertTrue(client.ping()["ok"])
        started = client.start(claims(), "pass", token())
        self.assertEqual(started["status"], "STARTED")
        self.assertEqual(len(self.fx.launches), 1)

    def test_a_peer_with_the_wrong_user_id_is_refused_without_being_parsed(self):
        self.serve(allowed_uids={os.getuid() + 1})
        reply = json.loads(self.raw(json.dumps({"op": "start", "id_token": sign_token(claims()), "scenario_id": "pass",
                                                "request_token": token()}).encode()))
        self.assertEqual(reply, {"ok": False, "error": "DENIED"})
        self.assertEqual(self.fx.launches, [])

    def test_oversized_garbage_and_slow_requests_are_survived(self):
        self.serve(allowed_uids={os.getuid()})
        self.assertEqual(json.loads(self.raw(b"x" * (MAX_REQUEST_BYTES + 100)))["error"], "BAD_REQUEST")
        self.assertEqual(json.loads(self.raw(b"\x00\xff garbage"))["error"], "BAD_REQUEST")
        with patch.object(ipc, "READ_TIMEOUT_S", 0.4):
            reply = json.loads(self.raw(b'{"op":"pi', shutdown=False))   # never finishes: dropped after the timeout
        self.assertEqual(reply["error"], "BAD_REQUEST")
        self.assertTrue(WorkerClient(ipc.SocketClient(self.path).call).ping()["ok"])  # still serving
        self.assertEqual(self.fx.launches, [])

    def test_client_reports_unavailable_when_nobody_listens(self):
        self.assertEqual(ipc.SocketClient(self.path + ".none").call({"op": "ping"}), {"ok": False, "error": "UNAVAILABLE"})


def stat_mode(path):
    return os.stat(path).st_mode & 0o777


if __name__ == "__main__":
    unittest.main()
