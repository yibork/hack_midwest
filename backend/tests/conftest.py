"""Point the app at a throwaway SQLite DB and snapshot dir before anything imports app.config."""
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="closurecall-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["SNAPSHOT_DIR"] = f"{_tmp}/snapshots"
# Never load the repo .env (real team key) in tests; inference is "missing" unless a test mocks it.
os.environ["CLOSURECALL_DOTENV"] = "0"
for name in ("OPENAI_API_KEY", "API_KEY", "OPENAI_BASE_URL", "BASE_URL"):
    os.environ[name] = ""
# No network for seeding, no background threads/loops racing the tests, stable ack links.
os.environ["RESOLVE_HOME_ZONES"] = "0"
os.environ["PREDRAFT"] = "0"
os.environ["ESCALATION_LOOP"] = "0"
os.environ["SCHEDULER_LOOP"] = "0"
os.environ["DEMO_MODE"] = "0"
os.environ["LOCAL_SIGNALS"] = "0"  # local-signal tests call the modules directly with fixtures
os.environ["PUBLIC_BASE_URL"] = "http://closurecall.test"
os.environ["NTFY_TOPIC"] = ""
os.environ["RESEND_API_KEY"] = ""

# ---------------------------------------------------------------- no live inference
# The Akamai endpoint is a shared team quota: any test that tries to resolve it fails, even if the
# code under test swallows the connection error and falls back to the template.
import socket  # noqa: E402

import pytest  # noqa: E402

LIVE_INFERENCE_HOST = "akamai-workshops.com"
live_inference_attempts: list[str] = []
_real_getaddrinfo = socket.getaddrinfo


def _guarded_getaddrinfo(host, *args, **kwargs):
    name = host.decode() if isinstance(host, bytes) else str(host or "")
    if name == LIVE_INFERENCE_HOST or name.endswith("." + LIVE_INFERENCE_HOST):
        live_inference_attempts.append(name)
        raise OSError(f"tests must not call the live inference endpoint ({name})")
    return _real_getaddrinfo(host, *args, **kwargs)


socket.getaddrinfo = _guarded_getaddrinfo


@pytest.fixture(autouse=True)
def _no_live_inference():
    live_inference_attempts.clear()
    yield
    if live_inference_attempts:
        pytest.fail(f"test tried to reach the live inference endpoint: {live_inference_attempts}")
