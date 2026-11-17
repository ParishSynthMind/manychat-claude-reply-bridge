"""HTTPS endpoint for ManyChat.

POST /manychat/dynamic   -> ManyChat v2 dynamic content (use in a Dynamic Block)
POST /manychat/external  -> flat JSON for Response mapping (use in an External Request)
GET  /health             -> liveness, model and knowledge base size (no secrets)

Every POST needs the shared secret header (or an HMAC signature). Bodies are
capped at 32 KB, callers are rate limited per IP, and errors never echo the
request back.
"""
from __future__ import annotations

import json
import os
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import logs, security
from .alerts import Alerter
from .bridge import Bridge
from .config import Settings
from .inbound import BadRequest, parse
from .kb import KnowledgeBase
from .ratelimit import RateLimiter
from .store import Store

MAX_BODY = 32 * 1024


def build_bridge(settings: Settings, responder=None, manychat=None, store=None, alerter=None) -> Bridge:
    kb = KnowledgeBase.load(settings.knowledge_base_dir)
    if responder is None:
        from .ai import ClaudeResponder

        responder = ClaudeResponder(settings.model, settings.business_name, api_key=settings.anthropic_api_key)
    if manychat is None and settings.manychat_api_key:
        from .manychat_api import ManyChatClient

        manychat = ManyChatClient(settings.manychat_api_key)
    store = store or Store(settings.db_path)
    alerter = alerter or Alerter(settings.alert_webhook_url, settings.heartbeat_url)
    return Bridge(settings, kb, responder, store, alerter, manychat=manychat)


def create_app(bridge: Bridge, trust_proxy: bool = False) -> FastAPI:
    app = FastAPI(title="ManyChat AI Reply Bridge", docs_url=None, redoc_url=None, openapi_url=None)
    per_ip = RateLimiter(bridge.s.per_ip_per_minute, 60)

    def client_ip(request: Request) -> str:
        if trust_proxy:
            fwd = request.headers.get("x-forwarded-for", "")
            if fwd:
                return fwd.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    @app.get("/health")
    def health():
        return {"ok": True, "model": bridge.s.model, "articles": len(bridge.kb.articles),
                "kill_switch": bridge.s.kill_switch, "slow_path": bridge.manychat is not None}

    async def handle(request: Request, mode: str):
        started = time.monotonic()
        if not per_ip.allow(client_ip(request)):
            return JSONResponse({"ok": False, "error": "rate limited"}, status_code=429)
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_BODY:
            return JSONResponse({"ok": False, "error": "body too large"}, status_code=413)
        body = await request.body()
        if len(body) > MAX_BODY:
            return JSONResponse({"ok": False, "error": "body too large"}, status_code=413)
        auth = security.check(dict(request.headers), body, bridge.s.secrets, bridge.s.require_signature,
                              bridge.s.signature_window_seconds)
        if not auth.ok:
            logs.event("auth_failed", mode=mode, reason=auth.reason, path=request.url.path)
            return JSONResponse({"ok": False, "error": "unauthorised"}, status_code=401)
        if auth.nonce and not bridge.store.use_nonce(auth.nonce):
            logs.event("replay_blocked", mode=mode)
            return JSONResponse({"ok": False, "error": "replayed request"}, status_code=409)
        try:
            data = json.loads(body or b"{}")
            inbound = parse(data, bridge.s.default_channel, bridge.s.max_input_chars,
                            request.query_params.get("channel"))
        except (ValueError, BadRequest) as exc:
            reason = str(exc) if isinstance(exc, BadRequest) else "body is not valid JSON"
            logs.event("bad_request", mode=mode, reason=reason[:80])
            return JSONResponse({"ok": False, "error": reason[:120]}, status_code=400)
        try:
            out = await _run(bridge, inbound, mode, started)
        except Exception as exc:  # last line of defence: alert, and let ManyChat's fallback step run
            bridge.alerter.failure(f"unhandled error: {type(exc).__name__}")
            logs.event("error", mode=mode, reason=type(exc).__name__)
            return JSONResponse({"ok": False, "error": "internal error"}, status_code=500)
        return JSONResponse(out)

    @app.post("/manychat/dynamic")
    async def dynamic(request: Request):
        return await handle(request, "dynamic")

    @app.post("/manychat/external")
    async def external(request: Request):
        return await handle(request, "external")

    return app


async def _run(bridge: Bridge, inbound, mode: str, started: float) -> dict:
    import anyio

    return await anyio.to_thread.run_sync(lambda: bridge.respond(inbound, mode, started))


def app_from_env() -> FastAPI:
    logs.setup(os.environ.get("LOG_LEVEL", "INFO"))
    settings = Settings.load(os.environ.get("BRIDGE_CONFIG", "config/bridge.yaml"))
    if not settings.secrets:
        raise SystemExit("BRIDGE_SECRETS is not set: refusing to start an open endpoint")
    return create_app(build_bridge(settings), trust_proxy=os.environ.get("TRUST_PROXY_HEADERS") == "true")
