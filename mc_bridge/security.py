"""Who may call the bridge, and replay protection.

ManyChat does not sign its External Request / Dynamic Block calls; you add your
own headers in the request setup. So the default check is a long random shared
secret in ``X-Bridge-Secret``, compared in constant time against every active
secret (two can be active while you rotate).

For callers that CAN sign (n8n, Make, a Cloudflare Worker in front of the
bridge), ``require_signature`` switches to HMAC-SHA256 over
``<timestamp>.<raw body>`` with a time window and a one-use signature cache,
which is full replay protection. In secret mode, replays are handled by the
idempotency cache (the same message returns the stored answer and never calls
the AI twice) and, optionally, by rejecting stale ManyChat ``last_interaction``
times.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass

SECRET_HEADER = "x-bridge-secret"
TS_HEADER = "x-bridge-timestamp"
SIG_HEADER = "x-bridge-signature"


@dataclass
class AuthResult:
    ok: bool
    reason: str = ""
    nonce: str | None = None


def sign(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return "v1=" + mac


def _any_match(candidate: str, secrets: list[str]) -> bool:
    found = False
    for s in secrets:  # check them all, so timing does not reveal which one matched
        found |= hmac.compare_digest(candidate.encode(), s.encode())
    return found


def check(headers: dict, body: bytes, secrets: list[str], require_signature: bool,
          window_seconds: int, now: float | None = None) -> AuthResult:
    h = {k.lower(): v for k, v in headers.items()}
    if not secrets:
        return AuthResult(False, "bridge has no secret configured")
    sig, ts = h.get(SIG_HEADER), h.get(TS_HEADER)
    if sig or require_signature:
        if not (sig and ts):
            return AuthResult(False, "signature required")
        try:
            ts_value = int(ts)
        except ValueError:
            return AuthResult(False, "bad timestamp")
        now = time.time() if now is None else now
        if abs(now - ts_value) > window_seconds:
            return AuthResult(False, "timestamp outside window")
        expected = [sign(s, ts, body) for s in secrets]
        if not _any_match(sig, expected):
            return AuthResult(False, "bad signature")
        return AuthResult(True, nonce=sig)
    given = h.get(SECRET_HEADER, "")
    if not given:
        return AuthResult(False, "missing secret")
    if not _any_match(given, secrets):
        return AuthResult(False, "bad secret")
    return AuthResult(True)
