"""Turn what ManyChat posts into one clean, typed message.

Recommended request body (ManyChat > Dynamic Block or External Request > Body):

    {"channel": "instagram", "contact": {{Full Contact Data}}}

ManyChat's own docs also show the body as only ``{{Full Contact Data}}`` (with
"Encode to JSON" ticked), so a bare contact object is accepted too. The
customer's message is the contact's ``last_input_text`` unless a ``text`` key
is sent.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import CHANNELS


class BadRequest(ValueError):
    pass


@dataclass
class Inbound:
    contact_id: str
    text: str
    channel: str
    first_name: str = ""
    last_interaction: datetime | None = None
    custom_fields: dict = field(default_factory=dict)
    tags: list = field(default_factory=list)

    @property
    def contact_ref(self) -> str:
        """Pseudonymous id for logs: never the raw id, name or text."""
        return hashlib.sha256(("mc:" + self.contact_id).encode()).hexdigest()[:12]

    def idempotency_key(self) -> str:
        stamp = self.last_interaction.isoformat() if self.last_interaction else ""
        raw = f"{self.contact_id}|{self.channel}|{stamp}|{self.text}"
        return hashlib.sha256(raw.encode()).hexdigest()


def parse_time(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _fields(raw) -> dict:
    """ManyChat sends custom fields either as {name: value} or as a list of
    {name, value} objects, depending on where they come from."""
    if isinstance(raw, dict):
        return {str(k): v for k, v in raw.items()}
    if isinstance(raw, list):
        return {str(f.get("name")): f.get("value") for f in raw if isinstance(f, dict) and f.get("name")}
    return {}


def guess_channel(contact: dict, default: str) -> str:
    if contact.get("ig_id") or contact.get("ig_username"):
        return "instagram"
    if contact.get("whatsapp_phone") or contact.get("whatsapp_username"):
        return "whatsapp"
    if contact.get("tg_id") or contact.get("telegram_id") or contact.get("tg_username"):
        return "telegram"
    return default


def parse(body, default_channel: str, max_chars: int, query_channel: str | None = None) -> Inbound:
    if not isinstance(body, dict):
        raise BadRequest("body must be a JSON object")
    contact = body.get("contact", body)
    if not isinstance(contact, dict):
        raise BadRequest("contact must be an object")
    cid = contact.get("id") or contact.get("subscriber_id")
    if cid in (None, "") or not str(cid).strip().isdigit():
        raise BadRequest("contact id missing or not numeric")
    text = body.get("text") if isinstance(body.get("text"), str) else contact.get("last_input_text")
    if not isinstance(text, str) or not text.strip():
        raise BadRequest("no message text (last_input_text is empty)")
    channel = (query_channel or body.get("channel") or "").strip().lower() or guess_channel(contact, default_channel)
    if channel == "facebook":
        channel = "messenger"
    if channel not in CHANNELS:
        raise BadRequest(f"channel must be one of {CHANNELS}")
    tags = contact.get("tags") or []
    tag_names = [t.get("name") if isinstance(t, dict) else str(t) for t in tags if t]
    return Inbound(
        contact_id=str(cid).strip(),
        text=text.strip()[:max_chars],
        channel=channel,
        first_name=str(contact.get("first_name") or "")[:60],
        last_interaction=parse_time(contact.get("last_interaction")),
        custom_fields=_fields(contact.get("custom_fields")),
        tags=[t for t in tag_names if t],
    )
