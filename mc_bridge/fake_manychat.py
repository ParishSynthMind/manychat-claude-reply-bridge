"""A faithful stand-in for ManyChat, so everything is tested with no account.

It does what ManyChat's documentation says ManyChat does (help.manychat.com,
Dev Tools articles, read 17 Nov 2026):

- calls the bridge like a Dynamic Block / External Request: HTTPS URL only,
  your own headers, a JSON body built from "Full Contact Data", and a hard
  10-second timeout that cannot be changed;
- on a timeout, a non-200 status or a body that is not valid JSON, runs the
  flow's Fallback step instead (or stops, if there is none);
- validates a Dynamic Block reply against the v2 format: version "v2",
  content.type for Instagram/WhatsApp/Telegram (none for Messenger), at most
  10 messages, 5 actions and 11 quick replies, no quick replies on WhatsApp or
  Telegram, url/flow/node buttons, and tags/fields that must already exist;
- applies actions to the contact (tags, custom fields up to 4,000 characters);
- for an External Request, maps the reply into custom fields by JSON path
  ("$.reply"), and maps nothing when the status is not 200.

It also fakes the Public API (Page API, Bearer key) used by the slow path:
sendContent, addTagByName, removeTagByName, setCustomFieldByName, including
error 3011 when the contact's last interaction is over 24 hours old.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx

TIMEOUT_SECONDS = 10.0
API_KEY = "fake-manychat-api-key-123456"
FIELD_LIMIT = 4000
CHANNEL_TYPES = {"instagram", "whatsapp", "telegram"}
BUTTON_TYPES = {"url", "flow", "node", "buy"}


class ValidationError(ValueError):
    pass


@dataclass
class Contact:
    id: int
    first_name: str
    channel: str = "instagram"
    tags: list = field(default_factory=list)
    custom_fields: dict = field(default_factory=dict)
    last_input_text: str = ""
    last_interaction: datetime | None = None
    received: list = field(default_factory=list)  # messages delivered to the customer

    def full_contact_data(self) -> dict:
        """The shape of ManyChat's {{Full Contact Data}} / Public API Subscriber."""
        d = {
            "id": str(self.id), "page_id": "1234567890", "first_name": self.first_name, "last_name": "",
            "name": self.first_name, "gender": None, "language": "English", "timezone": "UTC+04",
            "live_chat_url": f"https://app.manychat.com/fb1234567890/chat/{self.id}",
            "last_input_text": self.last_input_text,
            "last_interaction": self.last_interaction.isoformat() if self.last_interaction else None,
            "custom_fields": [{"id": i, "name": k, "type": "text", "value": v}
                              for i, (k, v) in enumerate(self.custom_fields.items(), 1)],
            "tags": [{"id": i, "name": t} for i, t in enumerate(self.tags, 1)],
        }
        if self.channel == "instagram":
            d.update(ig_username=f"user{self.id}", ig_id=self.id)
        if self.channel == "whatsapp":
            d.update(whatsapp_phone="+971500000000")
        return d


@dataclass
class Outcome:
    status: str                 # "ok", "fallback", "stopped"
    http_status: int | None = None
    response: dict | None = None
    delivered: list = field(default_factory=list)
    error: str = ""
    seconds: float = 0.0


def validate_v2(resp: dict, channel: str, known_tags: set, known_fields: set) -> None:
    if not isinstance(resp, dict) or resp.get("version") != "v2":
        raise ValidationError('version must be "v2"')
    content = resp.get("content")
    if not isinstance(content, dict):
        raise ValidationError("content missing")
    if channel in CHANNEL_TYPES and content.get("type") != channel:
        raise ValidationError(f'content.type must be "{channel}"')
    if channel == "messenger" and "type" in content:
        raise ValidationError("Messenger format has no content.type")
    messages = content.get("messages", [])
    actions = content.get("actions", [])
    qrs = content.get("quick_replies", [])
    if len(messages) > 10:
        raise ValidationError("more than 10 messages")
    if len(actions) > 5:
        raise ValidationError("more than 5 actions")
    if len(qrs) > 11:
        raise ValidationError("more than 11 quick replies")
    if qrs and channel in ("whatsapp", "telegram"):
        raise ValidationError(f"quick replies are not supported on {channel}")
    for m in messages:
        if m.get("type") != "text" or not isinstance(m.get("text"), str) or not m["text"].strip():
            raise ValidationError("every message must be non-empty text")
        for b in m.get("buttons", []):
            if b.get("type") not in BUTTON_TYPES or not b.get("caption"):
                raise ValidationError("bad button")
            if b["type"] == "url" and not str(b.get("url", "")).startswith("https://"):
                raise ValidationError("url buttons should use https")
    for a in actions:
        kind = a.get("action")
        if kind in ("add_tag", "remove_tag"):
            if a.get("tag_name") not in known_tags:
                raise ValidationError(f"tag {a.get('tag_name')!r} does not exist in ManyChat")
        elif kind in ("set_field_value", "unset_field_value"):
            if a.get("field_name") not in known_fields:
                raise ValidationError(f"custom field {a.get('field_name')!r} does not exist")
            if kind == "set_field_value" and isinstance(a.get("value"), str) and len(a["value"]) > FIELD_LIMIT:
                raise ValidationError("custom field value over 4,000 characters")
        else:
            raise ValidationError(f"unknown action {kind!r}")


def json_path(data, path: str):
    """The small JSONPath subset ManyChat's Response mapping uses: $.a.b[0].c"""
    if not path.startswith("$"):
        raise ValueError("JSON path must start with $")
    cur = data
    for name, index in re.findall(r"\.([A-Za-z0-9_]+)|\[(\d+)\]", path[1:]):
        if name:
            if not isinstance(cur, dict) or name not in cur:
                return None
            cur = cur[name]
        else:
            if not isinstance(cur, list) or int(index) >= len(cur):
                return None
            cur = cur[int(index)]
    return cur


class FakeManyChat:
    def __init__(self, tags=(), fields=(), clock=lambda: datetime.now(timezone.utc)):
        self.contacts: dict[int, Contact] = {}
        self.tags = set(tags)
        self.fields = set(fields)
        self.clock = clock
        self.api_calls: list[tuple[str, dict]] = []
        self.requests: list[dict] = []

    def add_contact(self, cid: int, first_name: str, channel: str = "instagram", **custom) -> Contact:
        c = Contact(cid, first_name, channel, custom_fields=dict(custom))
        self.contacts[cid] = c
        return c

    # ------------------------------------------------- calling the bridge
    def build_body(self, contact: Contact, body_style: str = "wrapped") -> dict:
        data = contact.full_contact_data()
        return {"channel": contact.channel, "contact": data} if body_style == "wrapped" else data

    def send(self, client: httpx.Client, url: str, contact_id: int, text: str, headers: dict | None = None,
             mode: str = "dynamic", mapping: dict | None = None, fallback: str | None = "Sorry, try again later.",
             body_style: str = "wrapped", raw_body: bytes | None = None) -> Outcome:
        """A contact sends `text`; ManyChat runs the Dynamic Block / External Request."""
        if not url.startswith("https://"):
            raise ValueError("ManyChat only allows https:// request URLs")
        if len(url) > 2000:
            raise ValueError("ManyChat request URLs are limited to 2,000 characters")
        c = self.contacts[contact_id]
        c.last_input_text = text
        c.last_interaction = self.clock()
        body = raw_body if raw_body is not None else json.dumps(self.build_body(c, body_style)).encode()
        hdrs = {"Content-Type": "application/json", **(headers or {})}
        self.requests.append({"url": url, "headers": dict(hdrs), "body": body})
        start = datetime.now(timezone.utc)
        try:
            r = client.post(url, content=body, headers=hdrs, timeout=TIMEOUT_SECONDS)
        except httpx.TimeoutException:
            return self._fallback(c, fallback, None, "timed out after 10 s", start)
        except httpx.HTTPError as exc:
            return self._fallback(c, fallback, None, f"request failed: {type(exc).__name__}", start)
        secs = (datetime.now(timezone.utc) - start).total_seconds()
        if r.status_code != 200:
            return self._fallback(c, fallback, r.status_code, f"HTTP {r.status_code}", start)
        try:
            data = r.json()
        except ValueError:
            return self._fallback(c, fallback, 200, "response was not JSON", start)
        if mode == "external":
            for field_name, path in (mapping or {}).items():
                if field_name not in self.fields:
                    raise ValidationError(f"mapping target {field_name!r} does not exist")
                value = json_path(data, path)
                if value is not None:
                    c.custom_fields[field_name] = str(value)[:FIELD_LIMIT] if not isinstance(value, bool) else value
            return Outcome("ok", 200, data, seconds=secs)
        try:
            validate_v2(data, c.channel, self.tags, self.fields)
        except ValidationError as exc:
            return self._fallback(c, fallback, 200, f"invalid dynamic content: {exc}", start)
        content = data["content"]
        self._apply(c, content.get("actions", []))
        delivered = [m["text"] for m in content.get("messages", [])]
        c.received.extend(delivered)
        return Outcome("ok", 200, data, delivered, seconds=secs)

    def _fallback(self, c: Contact, fallback, status, error, start) -> Outcome:
        secs = (datetime.now(timezone.utc) - start).total_seconds()
        if fallback is None:
            return Outcome("stopped", status, error=error, seconds=secs)
        c.received.append(fallback)
        return Outcome("fallback", status, delivered=[fallback], error=error, seconds=secs)

    def _apply(self, c: Contact, actions: list[dict]):
        for a in actions:
            if a["action"] == "add_tag" and a["tag_name"] not in c.tags:
                c.tags.append(a["tag_name"])
            elif a["action"] == "remove_tag" and a["tag_name"] in c.tags:
                c.tags.remove(a["tag_name"])
            elif a["action"] == "set_field_value":
                c.custom_fields[a["field_name"]] = a["value"]
            elif a["action"] == "unset_field_value":
                c.custom_fields.pop(a["field_name"], None)

    # ------------------------------------------------- the Public API
    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.headers.get("authorization") != f"Bearer {API_KEY}":
                return httpx.Response(401, json={"status": "error", "message": "Wrong token", "details": {"messages": []}})
            body = json.loads(request.content or b"{}")
            self.api_calls.append((request.url.path, body))
            c = self.contacts.get(int(body.get("subscriber_id", 0)))
            if c is None:
                return _err("Subscriber does not exist")
            path = request.url.path
            if path == "/fb/sending/sendContent":
                if c.last_interaction and self.clock() - c.last_interaction > timedelta(hours=24) \
                        and not body.get("message_tag"):
                    return _err("Content can't be sent to subscriber without message tag.", 3011)
                try:
                    validate_v2(body.get("data") or {}, c.channel, self.tags, self.fields)
                except ValidationError as exc:
                    return _err(f"Validation error: {exc}")
                c.received.extend(m["text"] for m in body["data"]["content"].get("messages", []))
                self._apply(c, body["data"]["content"].get("actions", []))
            elif path in ("/fb/subscriber/addTagByName", "/fb/subscriber/removeTagByName"):
                if body.get("tag_name") not in self.tags:
                    return _err("Tag not found")
                self._apply(c, [{"action": "add_tag" if "add" in path else "remove_tag", "tag_name": body["tag_name"]}])
            elif path == "/fb/subscriber/setCustomFieldByName":
                if body.get("field_name") not in self.fields:
                    return _err("Field not found")
                c.custom_fields[body["field_name"]] = body.get("field_value")
            else:
                return httpx.Response(404, json={"status": "error", "message": "Not found", "details": {"messages": []}})
            return httpx.Response(200, json={"status": "success"})

        return httpx.MockTransport(handler)


def _err(message: str, code: int | None = None) -> httpx.Response:
    return httpx.Response(400, json={"status": "error", "message": message,
                                     "details": {"messages": [{"message": message, "code": code}]}})
