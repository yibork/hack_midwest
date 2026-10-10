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
os.environ["NTFY_TOPIC"] = ""
os.environ["RESEND_API_KEY"] = ""
