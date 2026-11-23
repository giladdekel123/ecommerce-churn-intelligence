"""Test-process hygiene: no real credential is ever loaded, and diagnostics cannot print one.

Importing this module (done by the shared test helpers) does three things, without ever reading a .env file:
  1. removes credential-looking variables inherited from the developer's shell or IDE;
  2. turns dotenv's load_dotenv() into a no-op, so a developer's .env is never pulled into a test process;
  3. makes repr(os.environ) mask the value of every sensitive-looking name, so an assertion that fails on the
     environment (or a traceback that shows it) prints names, never secrets.
Tests that need a key set their own obvious dummy value.
"""
import os
import re

SENSITIVE_NAME = re.compile(r"(?i)(api[_-]?key|secret|passw(or)?d|credential|private[_-]?key|(^|_)token$)")
MASK = "<redacted>"


def _masked_repr(environ) -> str:
    return "environ({" + ", ".join(f"{k!r}: {MASK if SENSITIVE_NAME.search(k) else v!r}"
                                   for k, v in environ.items()) + "})"


def install() -> None:
    for name in [n for n in os.environ if SENSITIVE_NAME.search(n)]:
        del os.environ[name]
    try:
        import dotenv
        dotenv.load_dotenv = lambda *args, **kwargs: False
    except ImportError:
        pass
    type(os.environ).__repr__ = lambda self: _masked_repr(self)


install()
