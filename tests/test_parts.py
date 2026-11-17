"""Config, store, rate limiter, logs, the ManyChat API client and the fake's own rules."""
import json
import logging

import httpx
import pytest

from mc_bridge import logs
from mc_bridge.config import Settings
from mc_bridge.fake_manychat import API_KEY, FakeManyChat, ValidationError, json_path, validate_v2
from mc_bridge.guard import is_opt_out, risk_categories, wants_human
from mc_bridge.manychat_api import ManyChatClient, ManyChatError
from mc_bridge.ratelimit import RateLimiter
from mc_bridge.store import Store
from tests.conftest import ROOT, SECRET, make_settings

ENV = {"BRIDGE_SECRETS": f"{SECRET}, second-secret-abcdefghijklmnop", "ANTHROPIC_API_KEY": "sk-test"}


# config ---------------------------------------------------------------------
def test_load_shipped_config():
    s = Settings.load(ROOT / "config" / "bridge.yaml", env=ENV)
    assert s.model == "claude-haiku-5-5" and len(s.secrets) == 2 and s.deadline_seconds == 8.0


def test_secrets_never_in_repr():
    s = Settings.load(ROOT / "config" / "bridge.yaml", env=ENV)
    assert SECRET not in repr(s) and "sk-test" not in repr(s)


def test_short_secret_refused():
    with pytest.raises(ValueError):
        Settings.load(ROOT / "config" / "bridge.yaml", env={"BRIDGE_SECRETS": "short"})


def test_unknown_setting_refused(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("modle: typo\n")
    with pytest.raises(ValueError, match="unknown settings"):
        Settings.load(p, env={})


def test_kill_switch_from_env():
    assert Settings.load(ROOT / "config" / "bridge.yaml", env={**ENV, "KILL_SWITCH": "true"}).kill_switch


@pytest.mark.parametrize("bad", [{"deadline_seconds": 10}, {"deadline_seconds": 0}, {"default_channel": "sms"},
                                 {"min_confidence": 2}, {"answer_buttons": [{"caption": "x", "url": "http://a"}]},
                                 {"quick_replies": [{"type": "url", "caption": "x", "target": "y"}]},
                                 {"answer_buttons": [{"caption": "a", "url": "https://a"}] * 4}])
def test_invalid_settings_refused(bad):
    with pytest.raises(ValueError):
        Settings(**bad)


def test_partial_tags_keep_defaults():
    s = Settings(tags={"handover": "needs-human"})
    assert s.tags["handover"] == "needs-human" and s.tags["answered"] == "ai-answered"


def test_disclosure_default_blank_falls_back():
    assert Settings(business_name="X", ai_disclosure="  ").disclosure.startswith("I'm an AI assistant for X")


# store ----------------------------------------------------------------------
def test_store_flags_and_history_cap():
    st = Store()
    st.set_flag("1", "paused", True)
    for i in range(10):
        st.add_turns("1", [{"role": "customer", "text": str(i)}], keep=2)
    c = st.contact("1")
    assert c["paused"] and len(c["history"]) == 4


def test_store_rejects_unknown_column():
    with pytest.raises(ValueError):
        Store()._upsert("1", evil=1)


def test_idempotency_cache_expires():
    now = [100.0]
    st = Store(clock=lambda: now[0])
    st.remember("k", {"a": 1})
    assert st.cached("k", 10) == {"a": 1}
    now[0] += 11
    assert st.cached("k", 10) is None


def test_nonce_single_use():
    st = Store()
    assert st.use_nonce("v1=abc") and not st.use_nonce("v1=abc")


def test_counters_reset_each_day():
    st = Store()
    st.bump("ai_calls", "2026-11-17")
    st.bump("ai_calls", "2026-11-17")
    assert st.count("ai_calls", "2026-11-17") == 2 and st.bump("ai_calls", "2026-11-18") == 1


def test_purge_clears_old_history():
    now = [0.0]
    st = Store(clock=lambda: now[0])
    st.add_turns("1", [{"role": "customer", "text": "hi"}], keep=3)
    now[0] = 30 * 3600
    assert st.purge(24, 600) == 1 and st.contact("1")["history"] == []


def test_store_on_disk(tmp_path):
    st = Store(str(tmp_path / "sub" / "b.db"))
    st.set_flag("9", "opted_out", True)
    assert Store(str(tmp_path / "sub" / "b.db")).contact("9")["opted_out"]


# rate limiter ---------------------------------------------------------------
def test_rate_limiter_window():
    now = [0.0]
    rl = RateLimiter(2, 60, clock=lambda: now[0])
    assert rl.allow("a") and rl.allow("a") and not rl.allow("a") and rl.allow("b")
    now[0] = 61
    assert rl.allow("a")


def test_rate_limiter_zero_means_off():
    rl = RateLimiter(0)
    assert all(rl.allow("a") for _ in range(100))


# logs -----------------------------------------------------------------------
def test_log_event_drops_unknown_keys(caplog):
    caplog.set_level(logging.INFO, logger="mc_bridge")
    rec = logs.event("reply", contact="abc", text="secret message", name="Sara", action="answered")
    assert rec == {"ts": rec["ts"], "event": "reply", "contact": "abc", "action": "answered"}
    assert "Sara" not in caplog.text and "secret message" not in caplog.text


def test_log_lines_are_json(caplog):
    caplog.set_level(logging.INFO, logger="mc_bridge")
    logs.event("duplicate", mode="dynamic")
    assert json.loads(caplog.records[-1].getMessage())["mode"] == "dynamic"


# guard ----------------------------------------------------------------------
@pytest.mark.parametrize("text,cats", [("can I get my money back", ["refund"]), ("I'll call my lawyer", ["legal"]),
                                       ("I was charged twice", ["payment"]), ("delete my data please", ["personal_data"]),
                                       ("what's the price", [])])
def test_risk_categories(text, cats):
    assert risk_categories(text, make_settings().risk_words) == cats


def test_stop_inside_sentence_is_not_opt_out():
    words = make_settings().opt_out_words
    assert is_opt_out("STOP", words) and is_opt_out("stop please", words)
    assert not is_opt_out("how do I stop the plan renewing next month", words)


def test_wants_human_word_boundaries():
    words = make_settings().human_words
    assert wants_human("I want a human", words) and not wants_human("humane treatment", words)


# ManyChat Public API client -------------------------------------------------
@pytest.fixture
def api(mc):
    return ManyChatClient(API_KEY, transport=mc.transport(), sleep=lambda s: None)


def test_api_add_tag_and_set_field(api, mc):
    api.add_tag("5001", "ai-answered")
    api.set_field("5001", "lead_budget", "250")
    assert "ai-answered" in mc.contacts[5001].tags and mc.contacts[5001].custom_fields["lead_budget"] == "250"


def test_api_unknown_tag_raises(api):
    with pytest.raises(ManyChatError, match="Tag not found"):
        api.add_tag("5001", "nope")


def test_api_wrong_key_raises(mc):
    bad = ManyChatClient("wrong", transport=mc.transport(), sleep=lambda s: None)
    with pytest.raises(ManyChatError) as e:
        bad.add_tag("5001", "ai-answered")
    assert e.value.status == 401 and "wrong" not in str(e.value)


def test_api_24h_window_error_code(api, mc):
    from datetime import datetime, timedelta, timezone
    mc.contacts[5001].last_interaction = datetime.now(timezone.utc) - timedelta(hours=25)
    with pytest.raises(ManyChatError) as e:
        api.send_content("5001", {"version": "v2", "content": {"type": "instagram", "messages": [{"type": "text", "text": "hi"}]}})
    assert e.value.code == 3011


def test_api_retries_429_then_succeeds():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(200, json={"status": "success"})
    c = ManyChatClient("k", transport=httpx.MockTransport(handler), sleep=lambda s: None)
    assert c.add_tag("1", "t")["status"] == "success" and len(calls) == 3


def test_api_gives_up_after_retries():
    c = ManyChatClient("k", transport=httpx.MockTransport(lambda r: httpx.Response(503)), sleep=lambda s: None, retries=2)
    with pytest.raises(ManyChatError, match="gave up"):
        c.add_tag("1", "t")


def test_api_network_error_wrapped():
    def boom(request):
        raise httpx.ConnectError("down", request=request)
    c = ManyChatClient("k", transport=httpx.MockTransport(boom), sleep=lambda s: None, retries=1)
    with pytest.raises(ManyChatError, match="network error"):
        c.add_tag("1", "t")


def test_api_non_json_response():
    c = ManyChatClient("k", transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>")),
                       sleep=lambda s: None)
    with pytest.raises(ManyChatError, match="not JSON"):
        c.add_tag("1", "t")


def test_api_sends_bearer_header():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json={"status": "success"})
    ManyChatClient("abc", transport=httpx.MockTransport(handler)).set_field("1", "f", True)
    assert seen["auth"] == "Bearer abc"


# the fake's own rules -------------------------------------------------------
def test_validator_rejects_unknown_tag():
    resp = {"version": "v2", "content": {"type": "instagram", "messages": [],
                                         "actions": [{"action": "add_tag", "tag_name": "ghost"}]}}
    with pytest.raises(ValidationError, match="does not exist"):
        validate_v2(resp, "instagram", set(), set())


def test_validator_rejects_long_field_value():
    resp = {"version": "v2", "content": {"type": "instagram", "messages": [],
                                         "actions": [{"action": "set_field_value", "field_name": "f", "value": "x" * 4001}]}}
    with pytest.raises(ValidationError, match="4,000"):
        validate_v2(resp, "instagram", set(), {"f"})


@pytest.mark.parametrize("resp", [{"version": "v1", "content": {}}, {"version": "v2"},
                                  {"version": "v2", "content": {"type": "instagram", "messages": [{"type": "text", "text": ""}]}},
                                  {"version": "v2", "content": {"type": "instagram", "messages": [{"type": "text", "text": "x"}] * 11}},
                                  {"version": "v2", "content": {"type": "telegram", "messages": [],
                                                                "quick_replies": [{"type": "node", "caption": "a", "target": "b"}]}}])
def test_validator_rejects_bad_shapes(resp):
    with pytest.raises(ValidationError):
        validate_v2(resp, resp.get("content", {}).get("type", "instagram"), set(), set())


def test_json_path_subset():
    data = {"a": {"b": [{"c": 5}]}, "reply": "hi"}
    assert json_path(data, "$.a.b[0].c") == 5 and json_path(data, "$.reply") == "hi"
    assert json_path(data, "$.missing") is None and json_path(data, "$.a.b[3]") is None


def test_full_contact_data_shape():
    fake = FakeManyChat()
    c = fake.add_contact(1, "Sara", "instagram", lead_budget="100")
    d = c.full_contact_data()
    assert d["id"] == "1" and d["ig_username"] and d["custom_fields"][0]["name"] == "lead_budget"
