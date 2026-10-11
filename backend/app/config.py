"""Settings from the repo-root .env (then the process environment).

Inference uses the Akamai workshop service via the starter-kit names OPENAI_BASE_URL,
OPENAI_API_KEY, CHAT_MODEL and EMBEDDING_MODEL. The starter kit's shorter API_KEY / BASE_URL
names are accepted as fallbacks. Values in the .env file always beat the shell, so an
OPENAI_API_KEY exported for another provider can never be used by mistake.

The key is never printed or logged; use redact() on any text that might contain it.
"""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env"

_file_values: dict[str, str] = {}
# Tests set CLOSURECALL_DOTENV=0 so the real team key is never used by the test suite.
if os.getenv("CLOSURECALL_DOTENV", "1") != "0" and ENV_FILE.exists():
    from dotenv import dotenv_values, load_dotenv

    load_dotenv(ENV_FILE, override=True)
    _file_values = {k: v for k, v in dotenv_values(ENV_FILE).items() if v}


def _pick(*names: str, default: str = "") -> str:
    """First non-empty value: .env file entries (in name order), then the environment."""
    for source in (_file_values, os.environ):
        for name in names:
            value = (source.get(name) or "").strip()
            if value:
                return value
    return default


def _database_url() -> str:
    # SQLITE_PATH lets Docker keep the fallback DB in a volume without touching DATABASE_URL.
    url = _pick("DATABASE_URL", default=f"sqlite:///{_pick('SQLITE_PATH', default='./closure_call.db')}")
    # Accept the common postgres:// forms and use the psycopg 3 driver.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


_PLACEHOLDER = re.compile(r"^(hk_)?(x+|\.+|\*+|<.*>|your.*|replace.*|changeme|placeholder|todo|none|null)$",
                          re.IGNORECASE)


class InferenceConfigError(RuntimeError):
    """OPENAI_API_KEY / OPENAI_BASE_URL / CHAT_MODEL missing or still a placeholder."""


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=_database_url)
    nws_user_agent: str = field(default_factory=lambda: _pick(
        "NWS_USER_AGENT", default="ClosureCall/0.1 (hackathon demo; contact@example.com)"))

    # Akamai workshop inference (OpenAI-compatible chat completions only).
    openai_base_url: str = field(default_factory=lambda: _pick("OPENAI_BASE_URL", "BASE_URL"))
    openai_api_key: str = field(default_factory=lambda: _pick("OPENAI_API_KEY", "API_KEY"), repr=False)
    chat_model: str = field(default_factory=lambda: _pick("CHAT_MODEL", default="hackathon-chat"))
    embedding_model: str = field(default_factory=lambda: _pick("EMBEDDING_MODEL", default="hackathon-embedding"))

    # Object storage (optional, not used yet; snapshots go to SNAPSHOT_DIR for now).
    obj_endpoint: str = field(default_factory=lambda: _pick("OBJ_ENDPOINT"))
    obj_bucket: str = field(default_factory=lambda: _pick("OBJ_BUCKET"))
    obj_access_key: str = field(default_factory=lambda: _pick("OBJ_ACCESS_KEY"), repr=False)
    obj_secret_key: str = field(default_factory=lambda: _pick("OBJ_SECRET_KEY"), repr=False)

    ntfy_topic: str = field(default_factory=lambda: _pick("NTFY_TOPIC"))
    demo_topic_suffix: str = field(default_factory=lambda: _pick("DEMO_TOPIC_SUFFIX"))
    resend_api_key: str = field(default_factory=lambda: _pick("RESEND_API_KEY"), repr=False)
    resend_from: str = field(default_factory=lambda: _pick("RESEND_FROM", default="Closure Call <onboarding@resend.dev>"))
    # For demos: send every email to this one inbox instead of the fake roster addresses.
    notify_email_override: str = field(default_factory=lambda: _pick("NOTIFY_EMAIL_OVERRIDE"))

    # Shortens ack reminder/escalation timers to 1/2/3 minutes and enables /api/demo/* simulation.
    demo_mode: bool = field(default_factory=lambda: _pick("DEMO_MODE", default="0").lower() in ("1", "true", "yes"))
    # Base URL for links in messages (ack links). In production set it to the public URL.
    public_base_url: str = field(default_factory=lambda: _pick("PUBLIC_BASE_URL", default="http://localhost:8000").rstrip("/"))
    # Local signals layer (agents/local): roads, schools, public safety, news. Tests turn it off.
    local_signals: bool = field(default_factory=lambda: _pick("LOCAL_SIGNALS", default="1") != "0")
    # Local agents run in parallel with larger inputs than the weather agent.
    local_agent_timeout: float = field(default_factory=lambda: float(_pick("LOCAL_AGENT_TIMEOUT_SECONDS", default="45")))
    # Background scheduled re-evaluations (15:00 the day before, 04:00 on the target day).
    scheduler_loop: bool = field(default_factory=lambda: _pick("SCHEDULER_LOOP", default="1") != "0")
    # Geocode "route + cross street" text for sources without coordinates (OSM Nominatim; tests off).
    geocode: bool = field(default_factory=lambda: _pick("GEOCODE", default="1") != "0")
    # Background road-source watcher: refreshes cached road facts when a road source changes.
    roads_watch: bool = field(default_factory=lambda: _pick("ROADS_WATCH", default="1") != "0")
    # Background ack reminder/escalation loop (tests call escalation.tick() directly instead).
    escalation_loop: bool = field(default_factory=lambda: _pick("ESCALATION_LOOP", default="1") != "0")
    # Draft message wording in the background right after each recommendation (tests turn it off).
    predraft: bool = field(default_factory=lambda: _pick("PREDRAFT", default="1") != "0")
    # Resolve employee home zones via NWS /points when seeding (tests turn this off: no network).
    resolve_home_zones: bool = field(default_factory=lambda: _pick("RESOLVE_HOME_ZONES", default="1") != "0")

    # Per-call agent timeouts (seconds). On timeout the deterministic path takes over.
    agent_timeout: float = field(default_factory=lambda: float(_pick("AGENT_TIMEOUT_SECONDS", default="45")))
    # Team quota is 60 requests/min across all endpoints; we keep headroom.
    llm_requests_per_minute: int = field(default_factory=lambda: int(_pick("LLM_REQUESTS_PER_MINUTE", default="45")))
    llm_calls_per_evaluation: int = field(default_factory=lambda: int(_pick("LLM_CALLS_PER_EVALUATION", default="12")))

    snapshot_dir: Path = field(default_factory=lambda: Path(_pick("SNAPSHOT_DIR", default="./snapshots")))
    frontend_dist: Path = field(default_factory=lambda: Path(
        _pick("FRONTEND_DIST", default=str(ROOT / "frontend" / "dist"))))

    def inference_problem(self) -> str | None:
        """Why inference can't be used, or None. Never includes the key itself."""
        if not self.openai_api_key:
            return f"OPENAI_API_KEY is not set (add it to {ENV_FILE})"
        if _PLACEHOLDER.match(self.openai_api_key):
            return "OPENAI_API_KEY is still a placeholder; put your team key (hk_...) in .env"
        if not self.openai_base_url:
            return "OPENAI_BASE_URL is not set (https://ws.akamai-workshops.com/v1)"
        if not self.chat_model:
            return "CHAT_MODEL is not set (hackathon-chat)"
        return None

    def require_inference(self) -> None:
        problem = self.inference_problem()
        if problem:
            raise InferenceConfigError(problem)


settings = Settings()

_KEY_SHAPE = re.compile(r"\bhk_[A-Za-z0-9_\-]{4,}")


def redact(text: str) -> str:
    """Remove secrets from text that may be logged, stored or returned."""
    if not text:
        return text
    for secret in (settings.openai_api_key, settings.obj_secret_key, settings.resend_api_key):
        if secret and len(secret) >= 6:
            text = text.replace(secret, "[REDACTED]")
    return _KEY_SHAPE.sub("hk_[REDACTED]", text)
