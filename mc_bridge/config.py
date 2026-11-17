"""Settings: YAML for behaviour, environment variables for secrets.

Nothing secret is ever read from the YAML file or written to logs.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CHANNELS = ("instagram", "messenger", "whatsapp", "telegram")
DEFAULT_DISCLOSURE = "I'm an AI assistant for {business}. A person from the team can step in any time."

DEFAULT_TAGS = {"answered": "ai-answered", "handover": "human-handover", "opted_out": "opted-out",
                "lead": "lead-qualified", "timeout": "ai-timeout"}
DEFAULT_FIELDS = {"name": "lead_name", "need": "lead_need", "budget": "lead_budget", "timeline": "lead_timeline"}
DEFAULT_MESSAGES = {
    "handover": "I'm the AI assistant, so I've passed this to the team and a person will reply here shortly.",
    "checking": "Give me a few seconds, I'm checking that for you.",
    "fallback": "Thanks for your message! Our team will get back to you here shortly.",
    "opt_out": "You're unsubscribed and won't get automated replies. Send START any time to switch them back on.",
    "opt_in": "Welcome back! Automated replies are on again. How can I help?",
    "rate_limited": "You're sending messages quickly. Give me a moment and try again shortly.",
}


@dataclass
class Settings:
    business_name: str = "Demo Business"
    model: str = "claude-haiku-5-5"
    kill_switch: bool = False
    default_channel: str = "instagram"
    min_confidence: float = 0.7
    deadline_seconds: float = 8.0
    history_turns: int = 6
    history_ttl_hours: int = 24
    show_sources: bool = True
    max_input_chars: int = 1000
    max_ai_calls_per_day: int = 300
    per_contact_per_minute: int = 5
    per_contact_per_day: int = 40
    per_ip_per_minute: int = 120
    require_signature: bool = False
    signature_window_seconds: int = 300
    max_interaction_age_seconds: int = 0
    idempotency_ttl_seconds: int = 600
    tags: dict = field(default_factory=lambda: dict(DEFAULT_TAGS))
    fields: dict = field(default_factory=lambda: dict(DEFAULT_FIELDS))
    paused_field: str = "ai_paused"
    answer_buttons: list = field(default_factory=list)
    quick_replies: list = field(default_factory=list)
    messages: dict = field(default_factory=lambda: dict(DEFAULT_MESSAGES))
    risk_words: dict = field(default_factory=dict)
    human_words: list = field(default_factory=list)
    opt_out_words: list = field(default_factory=list)
    knowledge_base_dir: str = "knowledge_base"
    db_path: str = "data/bridge.db"
    # From the environment only:
    secrets: list = field(default_factory=list, repr=False)
    anthropic_api_key: str | None = field(default=None, repr=False)
    manychat_api_key: str | None = field(default=None, repr=False)
    alert_webhook_url: str | None = field(default=None, repr=False)
    heartbeat_url: str | None = field(default=None, repr=False)
    ai_disclosure: str = ""
    ai_disclosure_removed_by: str = ""

    def __post_init__(self):
        self.tags = {**DEFAULT_TAGS, **(self.tags or {})}
        self.fields = {**DEFAULT_FIELDS, **(self.fields or {})}
        self.messages = {**DEFAULT_MESSAGES, **(self.messages or {})}
        self.validate()

    def validate(self):
        if self.default_channel not in CHANNELS:
            raise ValueError(f"default_channel must be one of {CHANNELS}")
        if not 0 < self.deadline_seconds <= 9.0:
            raise ValueError("deadline_seconds must be above 0 and at most 9 (ManyChat stops waiting at 10 s)")
        if not 0 <= self.min_confidence <= 1:
            raise ValueError("min_confidence must be between 0 and 1")
        if len(self.answer_buttons) > 3:
            raise ValueError("at most 3 answer_buttons")
        for b in self.answer_buttons:
            if not str(b.get("url", "")).startswith("https://") or not b.get("caption"):
                raise ValueError("answer_buttons need a caption and an https:// url")
        if len(self.quick_replies) > 11:
            raise ValueError("ManyChat allows at most 11 quick replies")
        for q in self.quick_replies:
            if q.get("type") not in ("flow", "node") or not q.get("caption") or not q.get("target"):
                raise ValueError("quick_replies need type flow|node, a caption and a target")

    @property
    def disclosure(self) -> str:
        """B18: on by default; only removed by a recorded written request."""
        if self.ai_disclosure_removed_by.strip():
            return ""
        text = self.ai_disclosure.strip() or DEFAULT_DISCLOSURE
        return text.replace("{business}", self.business_name)

    @classmethod
    def load(cls, path: str | Path, env: dict | None = None) -> "Settings":
        env = os.environ if env is None else env
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown settings in {path}: {sorted(unknown)}")
        data["secrets"] = [s.strip() for s in env.get("BRIDGE_SECRETS", "").split(",") if s.strip()]
        data["anthropic_api_key"] = env.get("ANTHROPIC_API_KEY") or None
        data["manychat_api_key"] = env.get("MANYCHAT_API_KEY") or None
        data["alert_webhook_url"] = env.get("ALERT_WEBHOOK_URL") or None
        data["heartbeat_url"] = env.get("HEARTBEAT_URL") or None
        data["ai_disclosure"] = env.get("AI_DISCLOSURE", "")
        data["ai_disclosure_removed_by"] = env.get("AI_DISCLOSURE_REMOVED_BY_WRITTEN_REQUEST", "")
        if env.get("KILL_SWITCH", "").lower() in ("1", "true", "yes"):
            data["kill_switch"] = True
        if env.get("BRIDGE_DB_PATH"):
            data["db_path"] = env["BRIDGE_DB_PATH"]
        s = cls(**data)
        for secret in s.secrets:
            if len(secret) < 24:
                raise ValueError("every BRIDGE_SECRETS value must be at least 24 characters")
        return s
