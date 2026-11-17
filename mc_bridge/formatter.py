"""Build ManyChat's response formats from one channel-neutral Reply.

- Dynamic Block: ManyChat's "v2" dynamic content (messages, actions,
  quick_replies), with ManyChat's documented limits enforced here so a reply
  can never be rejected for size: at most 10 messages, 5 actions and 11 quick
  replies; quick replies only on Instagram and Messenger (not supported on
  WhatsApp and Telegram); Messenger's format has no ``content.type``.
- External Request: a flat JSON object for ManyChat's Response mapping
  (``$.reply``, ``$.lead_budget`` ...) into custom fields.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_MESSAGES, MAX_ACTIONS, MAX_QUICK_REPLIES = 10, 5, 11
TEXT_LIMIT = {"instagram": 1000, "messenger": 2000, "whatsapp": 4096, "telegram": 4096}
QR_CHANNELS = {"instagram", "messenger"}


@dataclass
class Reply:
    action: str                       # answered, handover, opted_out, opted_in, paused, rate_limited, fallback, ...
    texts: list[str] = field(default_factory=list)
    reason: str = ""
    add_tags: list[str] = field(default_factory=list)
    remove_tags: list[str] = field(default_factory=list)
    set_fields: dict = field(default_factory=dict)
    buttons: list[dict] = field(default_factory=list)
    quick_replies: list[dict] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    lead: dict = field(default_factory=dict)
    handover: bool = False
    disclosed: bool = False

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, d: dict) -> "Reply":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def split_text(text: str, limit: int) -> list[str]:
    """Split on sentence ends (then spaces) so no message passes the channel limit."""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    parts, current = [], ""
    for sentence in re.split(r"(?<=[.!?؟])\s+", text):
        while len(sentence) > limit:
            cut = sentence.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            if current:
                parts.append(current)
                current = ""
            parts.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if current and len(current) + 1 + len(sentence) > limit:
            parts.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        parts.append(current)
    return [p for p in parts if p]


def ordered_actions(reply: Reply) -> list[dict]:
    """All actions, most important first (handover and opt-out before lead details)."""
    out = [{"action": "add_tag", "tag_name": t} for t in reply.add_tags]
    out += [{"action": "set_field_value", "field_name": k, "value": v} for k, v in reply.set_fields.items()]
    out += [{"action": "remove_tag", "tag_name": t} for t in reply.remove_tags]
    return out


def dynamic_block(reply: Reply, channel: str) -> tuple[dict, list[dict]]:
    """Returns (ManyChat v2 response, actions that did not fit in the 5-action limit)."""
    limit = TEXT_LIMIT[channel]
    texts = [t for chunk in reply.texts for t in split_text(chunk, limit)][:MAX_MESSAGES]
    messages = [{"type": "text", "text": t} for t in texts]
    if messages and reply.buttons:
        messages[-1]["buttons"] = [{"type": "url", "caption": b["caption"][:20], "url": b["url"]}
                                   for b in reply.buttons[:3]]
    actions = ordered_actions(reply)
    content: dict = {"messages": messages, "actions": actions[:MAX_ACTIONS], "quick_replies": []}
    if channel in QR_CHANNELS and messages and reply.quick_replies:
        content["quick_replies"] = [{"type": q["type"], "caption": q["caption"][:20], "target": q["target"]}
                                    for q in reply.quick_replies[:MAX_QUICK_REPLIES]]
    if channel != "messenger":
        content = {"type": channel, **content}
    return {"version": "v2", "content": content}, actions[MAX_ACTIONS:]


def external_json(reply: Reply) -> dict:
    """Flat values for ManyChat's Response mapping (JSON path -> custom field)."""
    out = {
        "ok": True,
        "action": reply.action,
        "reply": "\n\n".join(t for t in reply.texts if t),
        "handover": reply.handover,
        "opted_out": reply.action == "opted_out",
        "sources": "; ".join(reply.sources),
        "tags_added": ",".join(reply.add_tags),
        "lead_qualified": all(reply.lead.get(k) for k in ("need", "budget", "timeline")),
    }
    for k in ("name", "need", "budget", "timeline"):
        out[f"lead_{k}"] = reply.lead.get(k) or ""
    return out
