"""Process-wide model request limiter and last-call status.

The team quota (60 requests/min across all endpoints, shared by everyone on the team) is
enforced by the service; we stay under it with a sliding-window cap (default 45/min).
"""
import asyncio
import threading
import time
from collections import deque
from datetime import datetime, timezone

from ..config import redact, settings


class RequestLimiter:
    def __init__(self, per_minute: int, window: float = 60.0):
        self.per_minute = per_minute
        self.window = window
        self._times: deque[float] = deque()
        self._lock = threading.Lock()

    def _reserve(self) -> float:
        """Take a slot if one is free (returns 0), else return seconds to wait."""
        with self._lock:
            now = time.monotonic()
            while self._times and now - self._times[0] >= self.window:
                self._times.popleft()
            if len(self._times) < self.per_minute:
                self._times.append(now)
                return 0.0
            return self.window - (now - self._times[0]) + 0.01

    def acquire(self) -> None:
        while (wait := self._reserve()) > 0:
            time.sleep(wait)

    async def acquire_async(self) -> None:
        while (wait := self._reserve()) > 0:
            await asyncio.sleep(wait)


limiter = RequestLimiter(settings.llm_requests_per_minute)

_last_call: dict = {"ok": None, "at": None, "error": None}
_status_lock = threading.Lock()


def mark_call(ok: bool, error: str | None = None) -> None:
    with _status_lock:
        _last_call.update(ok=ok, at=datetime.now(timezone.utc).isoformat(),
                          error=redact(error)[:200] if error else None)


def last_call() -> dict:
    with _status_lock:
        return dict(_last_call)
