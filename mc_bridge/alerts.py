"""Failure, empty-run and heartbeat signals (playbook rule H13).

- failure: anything that raised while handling a message or a job.
- empty_run: the daily summary saw no incoming messages at all.
- heartbeat: a ping after every message handled successfully; point
  HEARTBEAT_URL at a dead-man's switch (Healthchecks.io, UptimeRobot) and it
  raises the alarm when the pings STOP, which catches a server that died.

ALERT_WEBHOOK_URL gets a JSON POST {"text": ...} (Slack, Teams, n8n...). With
no URLs set, alerts go to the log only.
"""
from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger("mc_bridge.alerts")


class Alerter:
    def __init__(self, alert_url: str | None = None, heartbeat_url: str | None = None,
                 transport: httpx.BaseTransport | None = None, min_heartbeat_gap: float = 60.0, clock=time.monotonic):
        self.alert_url = alert_url
        self.heartbeat_url = heartbeat_url
        self._http = httpx.Client(timeout=10, transport=transport)
        self.min_gap = min_heartbeat_gap
        self._last_beat = None
        self.clock = clock
        self.sent: list[tuple[str, str]] = []

    def _post(self, kind: str, text: str):
        self.sent.append((kind, text))
        if kind == "heartbeat":
            log.debug("heartbeat")
        else:
            log.error("%s: %s", kind.upper(), text)
        url = self.heartbeat_url if kind == "heartbeat" else self.alert_url
        if not url:
            return
        try:
            if kind == "heartbeat":
                self._http.get(url)
            else:
                self._http.post(url, json={"text": f"[{kind}] {text}"})
        except httpx.HTTPError as exc:  # an alert that fails must never crash the assistant
            log.error("could not deliver %s alert: %s", kind, exc)

    def failure(self, text: str):
        self._post("failure", text[:1000])

    def empty_run(self, text: str):
        self._post("empty-run", text[:1000])

    def handover(self, text: str):
        """Tells the team a person is needed (no message text, only the reason)."""
        self._post("handover", text[:1000])

    def heartbeat(self):
        now = self.clock()
        if self._last_beat is not None and now - self._last_beat < self.min_gap:
            return
        self._last_beat = now
        self._post("heartbeat", "ok")
