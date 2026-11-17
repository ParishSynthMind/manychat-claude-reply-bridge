"""Structured JSON logs with no personal data.

Only whitelisted keys are written. Message text, names, emails, phone numbers
and raw contact ids are never logged: contacts appear as a short hash.
"""
from __future__ import annotations

import json
import logging
import time

ALLOWED = {"event", "contact", "channel", "mode", "action", "reason", "latency_ms", "input_tokens",
           "output_tokens", "citations", "status", "tags", "fields", "risk", "model", "deadline_ms", "path"}

log = logging.getLogger("mc_bridge")


def event(event_name: str, /, **kw):
    record = {"ts": round(time.time(), 3), "event": event_name}
    for k, v in kw.items():
        if k in ALLOWED and v is not None:
            record[k] = v
    log.info(json.dumps(record, ensure_ascii=False, sort_keys=True))
    return record


def setup(level: str = "INFO"):
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    log.handlers[:] = [handler]
    log.setLevel(level)
    log.propagate = False
