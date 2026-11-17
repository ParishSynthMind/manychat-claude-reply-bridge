"""Small client for ManyChat's Public API (Page API, Bearer key).

Only used for the slow path (an AI answer that missed ManyChat's 10-second
window is pushed afterwards with sendContent) and for actions that did not fit
in the 5-action limit. Endpoints and limits from ManyChat's Swagger (Page API):
sendContent 25 req/s, subscriber methods 10 req/s. The key is never logged.
"""
from __future__ import annotations

import time

import httpx

BASE_URL = "https://api.manychat.com"


class ManyChatError(Exception):
    def __init__(self, status: int, message: str, code=None):
        super().__init__(f"ManyChat API {status}: {message}")
        self.status, self.message, self.code = status, message, code


class ManyChatClient:
    def __init__(self, api_key: str, base_url: str = BASE_URL, transport: httpx.BaseTransport | None = None,
                 sleep=time.sleep, retries: int = 3):
        self._http = httpx.Client(base_url=base_url, timeout=15, transport=transport,
                                  headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"})
        self.sleep = sleep
        self.retries = retries

    def _post(self, path: str, payload: dict) -> dict:
        delay = 1.0
        for attempt in range(self.retries + 1):
            try:
                r = self._http.post(path, json=payload)
            except httpx.TransportError as exc:
                if attempt == self.retries:
                    raise ManyChatError(0, f"network error: {type(exc).__name__}") from None
                self.sleep(delay)
                delay *= 2
                continue
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == self.retries:
                    raise ManyChatError(r.status_code, "gave up after retries")
                self.sleep(float(r.headers.get("retry-after") or delay))
                delay *= 2
                continue
            try:
                data = r.json()
            except ValueError:
                raise ManyChatError(r.status_code, "response was not JSON") from None
            if r.status_code != 200 or data.get("status") != "success":
                msgs = (data.get("details") or {}).get("messages") or []
                code = msgs[0].get("code") if msgs and isinstance(msgs[0], dict) else None
                raise ManyChatError(r.status_code, str(data.get("message") or "error")[:200], code)
            return data
        raise ManyChatError(0, "unreachable")

    def send_content(self, subscriber_id: str, content: dict) -> dict:
        return self._post("/fb/sending/sendContent", {"subscriber_id": int(subscriber_id), "data": content})

    def add_tag(self, subscriber_id: str, tag_name: str) -> dict:
        return self._post("/fb/subscriber/addTagByName", {"subscriber_id": int(subscriber_id), "tag_name": tag_name})

    def remove_tag(self, subscriber_id: str, tag_name: str) -> dict:
        return self._post("/fb/subscriber/removeTagByName", {"subscriber_id": int(subscriber_id), "tag_name": tag_name})

    def set_field(self, subscriber_id: str, field_name: str, value) -> dict:
        return self._post("/fb/subscriber/setCustomFieldByName",
                          {"subscriber_id": int(subscriber_id), "field_name": field_name, "field_value": value})

    def apply_actions(self, subscriber_id: str, actions: list[dict]):
        for a in actions:
            if a["action"] == "add_tag":
                self.add_tag(subscriber_id, a["tag_name"])
            elif a["action"] == "remove_tag":
                self.remove_tag(subscriber_id, a["tag_name"])
            elif a["action"] == "set_field_value":
                self.set_field(subscriber_id, a["field_name"], a["value"])
