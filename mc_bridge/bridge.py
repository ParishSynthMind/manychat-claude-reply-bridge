"""The bridge: one ManyChat message in, one ManyChat reply out, inside the deadline.

Order of checks (code decides; the AI is only asked when nothing else applies):
replay/idempotency -> kill switch -> opt-in/opt-out -> paused (a person has the
chat) -> rate limits -> risk words / "human" / prompt-injection -> daily AI cap
-> Claude with a hard deadline -> checks on Claude's answer.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timezone

from . import logs
from .ai import Decision
from .config import Settings
from .formatter import Reply, dynamic_block, external_json
from .guard import clean, injection_flags, is_opt_in, is_opt_out, risk_categories, wants_human
from .inbound import Inbound
from .kb import KnowledgeBase
from .ratelimit import RateLimiter

TRUE = {"true", "1", "yes", "y"}
FALSE = {"false", "0", "no", "n"}
SAFETY_MARGIN = 0.4  # seconds kept back for formatting and the network


def reason_label(reason: str) -> str:
    """A reason safe for logs and alerts: the model's own words are never passed on."""
    return "model" if reason.startswith("model:") else reason[:60]


def _flag(value) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in TRUE | FALSE:
        return value.strip().lower() in TRUE
    return None


class Bridge:
    def __init__(self, settings: Settings, kb: KnowledgeBase, responder, store, alerter, manychat=None,
                 clock=time.time, monotonic=time.monotonic, executor: ThreadPoolExecutor | None = None):
        self.s = settings
        self.kb = kb
        self.responder = responder
        self.store = store
        self.alerter = alerter
        self.manychat = manychat
        self.clock = clock
        self.monotonic = monotonic
        self.pool = executor or ThreadPoolExecutor(max_workers=16, thread_name_prefix="mc-ai")
        self.per_minute = RateLimiter(settings.per_contact_per_minute, 60, clock=monotonic)
        self._inflight: dict[str, threading.Event] = {}
        self._inflight_lock = threading.Lock()
        self.slow_path_results: list[dict] = []  # for tests and the live-run record

    # ------------------------------------------------------------------ public
    def respond(self, inbound: Inbound, mode: str = "dynamic", started: float | None = None) -> dict:
        """Formatted ManyChat response. The same message twice (a retry or a
        replayed request) returns the stored answer and never calls the AI again."""
        started = self.monotonic() if started is None else started
        key = f"{mode}:{inbound.idempotency_key()}"
        cached = self.store.cached(key, self.s.idempotency_ttl_seconds)
        if cached is not None:
            logs.event("duplicate", contact=inbound.contact_ref, channel=inbound.channel, mode=mode)
            return cached
        with self._inflight_lock:
            waiter = self._inflight.get(key)
            if waiter is None:
                self._inflight[key] = threading.Event()
        if waiter is not None:  # the same message is being answered right now: wait for that answer
            waiter.wait(max(0.1, self.s.deadline_seconds - (self.monotonic() - started)))
            cached = self.store.cached(key, self.s.idempotency_ttl_seconds)
            if cached is not None:
                logs.event("duplicate", contact=inbound.contact_ref, channel=inbound.channel, mode=mode)
                return cached
            return self._format(Reply("duplicate", reason="duplicate in flight"), inbound, mode)[0]
        try:
            reply = self.handle(inbound, started)
            out, overflow = self._format(reply, inbound, mode)
            if overflow and self.manychat:
                self.pool.submit(self._apply_overflow, inbound, overflow)
            self.store.remember(key, out)
            logs.event("reply", contact=inbound.contact_ref, channel=inbound.channel, mode=mode,
                       action=reply.action, reason=reason_label(reply.reason) or None,
                       latency_ms=int((self.monotonic() - started) * 1000), citations=reply.citations or None,
                       tags=reply.add_tags or None, fields=sorted(reply.set_fields) or None)
            return out
        finally:
            with self._inflight_lock:
                ev = self._inflight.pop(key, None)
            if ev:
                ev.set()

    def handle(self, m: Inbound, started: float) -> Reply:
        s, tags = self.s, self.s.tags
        contact = self.store.contact(m.contact_id)

        if s.max_interaction_age_seconds and m.last_interaction:
            age = (datetime.fromtimestamp(self.clock(), tz=timezone.utc) - m.last_interaction).total_seconds()
            if age > s.max_interaction_age_seconds:
                return Reply("stale", reason="stale request (possible replay)")

        if s.kill_switch:
            return Reply("kill_switch", reason="kill switch on", add_tags=[tags["handover"]], handover=True)

        opted_out = contact["opted_out"] or tags["opted_out"] in m.tags
        if opted_out:
            if is_opt_in(m.text):
                self.store.set_flag(m.contact_id, "opted_out", False)
                return Reply("opted_in", texts=[s.messages["opt_in"]], remove_tags=[tags["opted_out"]],
                             reason="opt-in")
            return Reply("opted_out_silent", reason="contact opted out")
        if is_opt_out(m.text, s.opt_out_words):
            self.store.set_flag(m.contact_id, "opted_out", True)
            return Reply("opted_out", texts=[s.messages["opt_out"]], add_tags=[tags["opted_out"]], reason="opt-out")

        paused = _flag(m.custom_fields.get(s.paused_field))
        if paused is True and not contact["paused"]:
            self.store.set_flag(m.contact_id, "paused", True)
        elif paused is False and contact["paused"]:
            self.store.set_flag(m.contact_id, "paused", False)  # a person switched the bot back on
        if paused is True or (paused is None and contact["paused"]):
            return Reply("paused", reason="a person has this chat")

        day = datetime.fromtimestamp(self.clock(), tz=timezone.utc).strftime("%Y-%m-%d")
        if not self.per_minute.allow(m.contact_id) or \
                self.store.bump(f"contact:{m.contact_id}", day) > s.per_contact_per_day:
            return Reply("rate_limited", texts=[s.messages["rate_limited"]], reason="contact rate limit")

        text = clean(m.text, s.max_input_chars)
        risks = risk_categories(text, s.risk_words)
        if risks:
            return self._handover(m, contact, f"risk:{','.join(risks)}")
        if wants_human(text, s.human_words):
            return self._handover(m, contact, "asked for a person")
        if injection_flags(text):
            return self._handover(m, contact, "prompt injection attempt")

        if self.store.count("ai_calls", day) >= s.max_ai_calls_per_day:
            self.alerter.failure("daily AI call cap reached; replies fall back to the team")
            return self._fallback(m, contact, "daily AI cap reached", handover=True)
        self.store.bump("ai_calls", day)

        articles = self.kb.search(text, k=4)
        if not articles and self.kb.has("about"):
            articles = [self.kb.articles["about"]]
        history = self.store.history(m.contact_id, s.history_ttl_hours)
        remaining = s.deadline_seconds - (self.monotonic() - started) - SAFETY_MARGIN
        if remaining <= 0.5:
            return self._fallback(m, contact, "no time left before ManyChat's limit")
        sdk_timeout = 30.0 if self.manychat else remaining
        future = self.pool.submit(self.responder.decide, history, text, articles, m.channel,
                                  s.min_confidence, sdk_timeout)
        try:
            decision: Decision = future.result(timeout=remaining)
        except FutureTimeout:
            if self.manychat:
                future.add_done_callback(lambda f: self._slow_path(f, m, text, contact))
                reply = self._fallback(m, contact, "ai slower than the deadline: slow path", message="checking")
            else:
                reply = self._fallback(m, contact, "ai slower than the deadline")
            reply.add_tags.append(tags["timeout"])
            return reply
        except Exception as exc:  # the API is down, the key is wrong...: never leave the customer hanging
            name = type(exc).__name__
            self.alerter.failure(f"AI call failed: {name}")
            return self._fallback(m, contact, f"ai error: {name}", handover=True)
        return self._from_decision(m, contact, text, decision)

    # ----------------------------------------------------------------- helpers
    def _disclose(self, m: Inbound, contact: dict, texts: list[str]) -> bool:
        line = self.s.disclosure
        if line and not contact["disclosed"]:
            texts.insert(0, line)
            self.store.set_flag(m.contact_id, "disclosed", True)
            contact["disclosed"] = True
            return True
        return False

    def _handover(self, m: Inbound, contact: dict, reason: str) -> Reply:
        texts = [self.s.messages["handover"]]
        disclosed = self._disclose(m, contact, texts)
        self.store.set_flag(m.contact_id, "paused", True)
        self.alerter.handover(f"contact {m.contact_ref} on {m.channel} needs a person ({reason_label(reason)})")
        return Reply("handover", texts=texts, reason=reason, add_tags=[self.s.tags["handover"]],
                     set_fields={self.s.paused_field: True}, handover=True, disclosed=disclosed)

    def _fallback(self, m: Inbound, contact: dict, reason: str, handover: bool = False,
                  message: str = "fallback") -> Reply:
        texts = [self.s.messages[message]]
        disclosed = self._disclose(m, contact, texts)
        r = Reply("fallback", texts=texts, reason=reason, handover=handover, disclosed=disclosed)
        if handover:
            r.add_tags.append(self.s.tags["handover"])
        return r

    def _from_decision(self, m: Inbound, contact: dict, text: str, d: Decision, record: bool = True) -> Reply:
        if d.action != "answer":
            reply = self._handover(m, contact, "model: " + (d.reason or "asked for a person"))
            reply.lead = d.lead
            return reply
        s = self.s
        body = d.reply
        titles = [self.kb.articles[c].title for c in d.citations if self.kb.has(c)]
        if s.show_sources and titles:
            body += "\n\nSource: " + "; ".join(titles[:2])
        texts = [body]
        disclosed = self._disclose(m, contact, texts)
        known = {k: m.custom_fields.get(f) for k, f in s.fields.items() if m.custom_fields.get(f)}
        new = {k: v for k, v in d.lead.items() if v and known.get(k) != v}
        lead = {**known, **new}
        add_tags = [s.tags["answered"]]
        if all(lead.get(k) for k in ("need", "budget", "timeline")) and s.tags["lead"] not in m.tags:
            add_tags.insert(0, s.tags["lead"])
        reply = Reply("answered", texts=texts, reason="", add_tags=add_tags,
                      set_fields={s.fields[k]: v for k, v in new.items() if k in s.fields},
                      buttons=list(s.answer_buttons), quick_replies=list(s.quick_replies),
                      citations=d.citations, sources=titles, lead=lead, disclosed=disclosed)
        if record:
            self.store.add_turns(m.contact_id, [{"role": "customer", "text": text},
                                                {"role": "assistant", "text": d.reply}], s.history_turns)
        self.alerter.heartbeat()
        return reply

    def _format(self, reply: Reply, m: Inbound, mode: str) -> tuple[dict, list[dict]]:
        if mode == "external":
            return external_json(reply), []
        return dynamic_block(reply, m.channel)

    def _slow_path(self, future, m: Inbound, text: str, contact: dict):
        """The AI answered after ManyChat stopped waiting: push it with the Public API."""
        try:
            decision = future.result()
            reply = self._from_decision(m, contact, text, decision)
            content, overflow = dynamic_block(reply, m.channel)
            actions = content["content"].pop("actions", [])
            content["content"]["actions"] = []
            self.manychat.send_content(m.contact_id, content)
            self.manychat.apply_actions(m.contact_id, actions + overflow)
            self.slow_path_results.append({"action": reply.action, "sent": True})
            logs.event("slow_path", contact=m.contact_ref, channel=m.channel, action=reply.action, status="sent")
        except Exception as exc:
            self.slow_path_results.append({"action": "error", "sent": False, "error": type(exc).__name__})
            self.alerter.failure(f"slow path failed: {type(exc).__name__}")
            logs.event("slow_path", contact=m.contact_ref, channel=m.channel, status="failed",
                       reason=type(exc).__name__)

    def _apply_overflow(self, m: Inbound, actions: list[dict]):
        try:
            self.manychat.apply_actions(m.contact_id, actions)
        except Exception as exc:
            self.alerter.failure(f"could not apply extra actions: {type(exc).__name__}")

    def daily_check(self) -> int:
        """Empty-run alert (rule H13): no AI calls at all today means something upstream is broken."""
        day = datetime.fromtimestamp(self.clock(), tz=timezone.utc).strftime("%Y-%m-%d")
        n = self.store.count("ai_calls", day)
        if n == 0:
            self.alerter.empty_run(f"no ManyChat messages reached the bridge on {day}")
        return n
