"""People, access and notification engine. Deterministic code decides who is affected, what they
must do, who may enter the building and who is told; the LLM only words messages.

impact.py (who/what/why) -> plan.py (waves, channels, role messages, versioning) ->
notices.py (persist, send, call-ins, acks) ; escalation.py (reminders and escalation)."""
from .notices import NoDecisionError, acknowledge, call_in, create_notice, send_notice

__all__ = ["NoDecisionError", "acknowledge", "call_in", "create_notice", "send_notice"]
