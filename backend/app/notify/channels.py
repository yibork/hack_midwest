"""Delivery channels. Each returns (ok, error). Unconfigured channels print to the console and
return (False, "not configured") so the fallback and the record show what happened."""
import logging

import httpx

from ..config import settings

log = logging.getLogger(__name__)


def send_push(topic: str | None, title: str, message: str, ack_url: str | None = None) -> tuple[bool, str | None]:
    """ntfy.sh, JSON publish (UTF-8 safe). The ack link becomes an "Acknowledge" button."""
    topic = topic or settings.ntfy_topic
    if not topic:
        print(f"\n[PUSH - not sent, no ntfy topic] {title}\n{message}\n", flush=True)
        return False, "no ntfy topic for this person"
    body = {"topic": topic, "title": title, "message": message, "tags": ["warning"]}
    if ack_url:
        body["actions"] = [{"action": "view", "label": "Acknowledge", "url": ack_url, "clear": True}]
    try:
        r = httpx.post("https://ntfy.sh/", json=body, timeout=10)
        r.raise_for_status()
        return True, None
    except httpx.HTTPError as e:
        log.error("ntfy send failed: %s", e)
        return False, f"ntfy error: {type(e).__name__}"


def send_email(to: str, subject: str, text: str, html: str | None = None) -> tuple[bool, str | None]:
    if not settings.resend_api_key:
        print(f"\n[EMAIL - not sent, RESEND_API_KEY unset] to={to} subject={subject}\n{text}\n", flush=True)
        return False, "email not configured"
    payload = {"from": settings.resend_from, "to": [settings.notify_email_override or to], "subject": subject,
               "text": text}
    if html:
        payload["html"] = html
    try:
        r = httpx.post("https://api.resend.com/emails", headers={"Authorization": f"Bearer {settings.resend_api_key}"},
                       json=payload, timeout=10)
        r.raise_for_status()
        return True, None
    except httpx.HTTPError as e:
        log.error("Resend send failed: %s", type(e).__name__)
        return False, f"email error: {type(e).__name__}"


def send_staff_push(topic: str | None, title: str, message: str, actions: list[dict]) -> tuple[bool, str | None]:
    """Explicit manager evidence request, separate from operational notices/DispatchGuard."""
    topic = topic or settings.ntfy_topic
    if not topic:
        return False, "No ntfy topic configured for facility operations"
    try:
        response = httpx.post("https://ntfy.sh/", json={"topic": topic, "title": title, "message": message,
                                                       "actions": actions, "tags": ["question"]}, timeout=10)
        response.raise_for_status()
        return True, None
    except httpx.HTTPError as exc:
        return False, f"ntfy error: {type(exc).__name__}"
