"""Regression tests: tests never load real credentials and diagnostics never print them."""
import ast
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env_hygiene  # noqa: E402

TESTS = Path(__file__).resolve().parent
CANARY = "CANARY-" + "NOT-A-REAL-SECRET-" + "0123456789"      # assembled at run time; obviously fake
DICT_ASSERTS = {"assertIn", "assertNotIn", "assertEqual", "assertNotEqual", "assertDictEqual", "assertCountEqual",
                "assertDictContainsSubset", "assertIs", "assertIsNot"}


def mentions_environ(node) -> bool:
    return any(isinstance(n, ast.Attribute) and n.attr == "environ" for n in ast.walk(node))


class SanitizedProcessTests(unittest.TestCase):
    def test_no_sensitive_variable_survives_in_the_test_process(self):
        present = [n for n in os.environ if env_hygiene.SENSITIVE_NAME.search(n)]
        # Names only. Tests may add their own dummy values later; at import time nothing real may remain.
        self.assertEqual([n for n in present if n in ("GEMINI_API_KEY", "GOOGLE_API_KEY") and
                          not os.environ[n].startswith(("offline", "FAKE", "AIzaSyFAKE", "CANARY"))], [])

    def test_load_dotenv_cannot_pull_a_dotenv_file_into_the_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, ".env").write_text(f"HYGIENE_CANARY_API_KEY={CANARY}\n", encoding="utf-8")
            previous = os.getcwd()
            os.chdir(tmp)
            try:
                from dotenv import load_dotenv
                self.assertFalse(load_dotenv())
                self.assertFalse(load_dotenv(Path(tmp) / ".env", override=True))
            finally:
                os.chdir(previous)
        self.assertNotIn("HYGIENE_CANARY_API_KEY", os.environ)

    def test_a_credential_in_the_inherited_environment_is_removed_before_tests_run(self):
        code = ("import os, sys; sys.path.insert(0, %r); import env_hygiene; "
                "print(sorted(n for n in os.environ if 'CANARY' in n))" % str(TESTS))
        env = {**os.environ, "GEMINI_API_KEY": CANARY, "CANARY_SERVICE_SECRET": CANARY, "CANARY_ACCESS_TOKEN": CANARY,
               "CANARY_HARMLESS_SETTING": "visible"}
        out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, env=env)
        self.assertEqual(out.stdout.strip(), "['CANARY_HARMLESS_SETTING']", out.stderr[-200:])
        self.assertNotIn(CANARY, out.stdout + out.stderr)


class DiagnosticOutputTests(unittest.TestCase):
    def test_repr_of_the_environment_masks_sensitive_values(self):
        with patch.dict(os.environ, {"X_SERVICE_API_KEY": CANARY, "X_CLIENT_SECRET": CANARY, "X_ID_TOKEN": CANARY,
                                     "X_DB_PASSWORD": CANARY, "X_PLAIN": "visible-value"}):
            text = repr(os.environ)
        self.assertNotIn(CANARY, text)
        self.assertIn("visible-value", text)
        self.assertIn(env_hygiene.MASK, text)

    def test_a_failing_assertion_on_the_environment_does_not_reveal_secrets(self):
        with patch.dict(os.environ, {"X_SERVICE_API_KEY": CANARY}):
            try:
                self.assertIn("NOT_PRESENT_NAME", os.environ)
            except AssertionError as exc:
                message = str(exc)
        self.assertNotIn(CANARY, message)
        self.assertIn("X_SERVICE_API_KEY", message)             # the name may appear; the value may not

    def test_a_real_failing_test_run_prints_no_secret(self):
        code = ("import os, sys, unittest; sys.path.insert(0, %r); import env_hygiene\n"
                "class T(unittest.TestCase):\n"
                "    def test_boom(self):\n"
                "        self.assertEqual(os.environ.get('NOPE'), os.environ)\n"
                "unittest.main(argv=['x'])\n" % str(TESTS))
        env = {**os.environ, "OTHER_SERVICE_API_KEY": CANARY}
        out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, env=env)
        self.assertIn("FAILED", out.stderr)
        self.assertNotIn(CANARY, out.stdout + out.stderr)

    def test_no_test_file_hands_the_whole_environment_to_an_assertion(self):
        """dict(os.environ) is a plain dict whose repr is not masked, so assertions must not receive it."""
        offenders = []
        for path in TESTS.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in DICT_ASSERTS and any(
                            isinstance(a, ast.Call) and mentions_environ(a) and isinstance(a.func, ast.Name)
                            and a.func.id in ("dict", "copy", "deepcopy") for a in node.args)):
                    offenders.append(f"{path.name}:{node.lineno}")
        self.assertEqual(offenders, [])

    def test_tests_never_read_a_dotenv_file_directly(self):
        for path in TESTS.glob("*.py"):
            if path.name == Path(__file__).name:
                continue
            self.assertNotRegex(path.read_text(encoding="utf-8"), r"open\([^)]*\.env['\"]|read_text\([^)]*\.env", path.name)


if __name__ == "__main__":
    unittest.main()
