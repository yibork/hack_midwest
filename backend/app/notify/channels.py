"""Delivery channels. Without keys/topic configured they log to the console and return False."""
import logging

import httpx

from ..config import settings

log = logging.getLogger(__name__)


def send_push(title: str, message: str) -> bool:
    if not settings.ntfy_topic:
        print(f"\n[PUSH - not sent, NTFY_TOPIC unset] {title}\n{message}\n", flush=True)
        return False
    try:
        r = httpx.post(f"https://ntfy.sh/{settings.ntfy_topic}", content=message.encode("utf-8"),
                       headers={"Title": title, "Tags": "warning"}, timeout=10)
        r.raise_for_status()
        return True
    except httpx.HTTPError as e:
        log.error("ntfy send failed: %s", e)
        return False


def send_email(to: str, subject: str, body: str) -> bool:
    if not settings.resend_api_key:
        print(f"\n[EMAIL - not sent, RESEND_API_KEY unset] to={to} subject={subject}\n{body}\n", flush=True)
        return False
    try:
        r = httpx.post("https://api.resend.com/emails",
                       headers={"Authorization": f"Bearer {settings.resend_api_key}"},
                       json={"from": settings.resend_from, "to": [settings.notify_email_override or to],
                             "subject": subject, "text": body},
                       timeout=10)
        r.raise_for_status()
        return True
    except httpx.HTTPError as e:
        log.error("Resend send failed: %s", e)
        return False
