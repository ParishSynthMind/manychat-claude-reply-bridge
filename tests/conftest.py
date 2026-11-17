import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from mc_bridge.ai import ClaudeResponder
from mc_bridge.alerts import Alerter
from mc_bridge.bridge import Bridge
from mc_bridge.config import Settings
from mc_bridge.fake_manychat import API_KEY, FakeManyChat
from mc_bridge.inbound import Inbound
from mc_bridge.kb import KnowledgeBase
from mc_bridge.manychat_api import ManyChatClient
from mc_bridge.server import create_app
from mc_bridge.store import Store

ROOT = Path(__file__).resolve().parent.parent
SECRET = "test-secret-0123456789-abcdefghij"
SECRET2 = "rotated-secret-9876543210-zyxwvut"
NOLEAD = {"name": None, "need": None, "budget": None, "timeline": None}


class FakeAnthropic:
    """Stands in for anthropic.Anthropic(): scripted JSON answers, optional delay or error."""

    def __init__(self):
        self.replies = []
        self.calls = []
        self.messages = self
        self.stop_reason = "end_turn"
        self.delay = 0.0
        self.error = None
        self.done = threading.Event()

    def answer(self, reply, citations, confidence=0.92, lead=None, summary="asks a question"):
        self.replies.append({"action": "answer", "reply": reply, "citations": citations, "confidence": confidence,
                             "handover_reason": "", "summary": summary, "lead": {**NOLEAD, **(lead or {})}})

    def handover(self, reason="not in the knowledge base", lead=None):
        self.replies.append({"action": "handover", "reply": "", "citations": [], "confidence": 0.2,
                             "handover_reason": reason, "summary": "needs a person", "lead": {**NOLEAD, **(lead or {})}})

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            self.done.set()
            raise self.error
        raw = self.replies.pop(0) if self.replies else {"action": "handover", "reply": "", "citations": [],
                                                         "confidence": 0, "handover_reason": "no script",
                                                         "summary": "", "lead": NOLEAD}
        text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
        self.done.set()
        return SimpleNamespace(stop_reason=self.stop_reason, content=[SimpleNamespace(type="text", text=text)],
                               usage=SimpleNamespace(input_tokens=600, output_tokens=90))


def make_settings(**over):
    import yaml

    data = yaml.safe_load((ROOT / "config" / "bridge.yaml").read_text())
    data["knowledge_base_dir"] = str(ROOT / "knowledge_base")
    data["db_path"] = ":memory:"
    data.update(over)
    data.setdefault("secrets", [SECRET, SECRET2])
    return Settings(**data)


@pytest.fixture
def settings():
    return make_settings()


@pytest.fixture
def kb():
    return KnowledgeBase.load(ROOT / "knowledge_base")


@pytest.fixture
def claude():
    return FakeAnthropic()


@pytest.fixture
def alerter():
    return Alerter()


@pytest.fixture
def store():
    return Store(":memory:")


@pytest.fixture
def bridge(settings, kb, claude, store, alerter):
    responder = ClaudeResponder(settings.model, settings.business_name, client=claude)
    return Bridge(settings, kb, responder, store, alerter)


def make_bridge(settings, kb, claude, manychat=None, store=None, alerter=None):
    responder = ClaudeResponder(settings.model, settings.business_name, client=claude)
    return Bridge(settings, kb, responder, store or Store(":memory:"), alerter or Alerter(), manychat=manychat)


@pytest.fixture
def mc():
    s = make_settings()
    fake = FakeManyChat(tags=set(s.tags.values()), fields=set(s.fields.values()) | {s.paused_field, "ai_reply",
                                                                                    "ai_sources", "ai_handover"})
    fake.add_contact(5001, "Sara", "instagram")
    fake.add_contact(5002, "Omar", "telegram")
    fake.add_contact(5003, "Lina", "whatsapp")
    fake.add_contact(5004, "Ben", "messenger")
    return fake


@pytest.fixture
def mc_api(mc):
    return ManyChatClient(API_KEY, base_url="https://api.manychat.com", transport=mc.transport(), sleep=lambda s: None)


@pytest.fixture
def app_client(bridge):
    return TestClient(create_app(bridge), base_url="https://testserver")


def msg(text, cid="5001", channel="instagram", fields=None, tags=None, last=None):
    return Inbound(contact_id=cid, text=text, channel=channel, custom_fields=fields or {}, tags=tags or [],
                   last_interaction=last)


HEADERS = {"X-Bridge-Secret": SECRET}
DYN = "https://testserver/manychat/dynamic"
EXT = "https://testserver/manychat/external"
