"""The whole path: fake ManyChat -> HTTP -> bridge -> fake Claude -> ManyChat's validator."""
import json
import logging
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from mc_bridge import fake_manychat, security
from mc_bridge.server import create_app
from tests.conftest import DYN, EXT, HEADERS, SECRET, make_bridge, make_settings

MAPPING = {"ai_reply": "$.reply", "ai_sources": "$.sources", "lead_budget": "$.lead_budget", "ai_handover": "$.handover"}


def test_instagram_dynamic_block_round_trip(app_client, mc, claude):
    claude.answer("Transform costs 179 USD per month.", ["pricing"])
    o = mc.send(app_client, DYN, 5001, "How much is Transform?", HEADERS)
    assert o.status == "ok" and o.http_status == 200
    assert o.delivered[0].startswith("I'm an AI assistant") and "179 USD" in o.delivered[1]
    assert "ai-answered" in mc.contacts[5001].tags


@pytest.mark.parametrize("cid,channel", [(5002, "telegram"), (5003, "whatsapp"), (5004, "messenger")])
def test_every_channel_passes_manychat_validation(app_client, mc, claude, cid, channel):
    claude.answer("Starter costs 99 USD per month.", ["pricing"])
    o = mc.send(app_client, DYN, cid, "starter price?", HEADERS)
    assert o.status == "ok", o.error
    assert ("type" in o.response["content"]) == (channel != "messenger")


def test_bare_full_contact_data_body(app_client, mc, claude):
    claude.answer("Starter costs 99 USD per month.", ["pricing"])
    assert mc.send(app_client, DYN, 5001, "price?", HEADERS, body_style="bare").status == "ok"


def test_external_request_response_mapping(app_client, mc, claude):
    claude.answer("Transform costs 179 USD per month.", ["pricing"], lead={"budget": "200"})
    o = mc.send(app_client, EXT, 5001, "transform price? my budget is 200", HEADERS, mode="external", mapping=MAPPING)
    f = mc.contacts[5001].custom_fields
    assert o.status == "ok" and "179 USD" in f["ai_reply"] and f["ai_sources"] == "Prices"
    assert f["lead_budget"] == "200" and f["ai_handover"] is False


def test_lead_qualification_lands_in_contact_fields(app_client, mc, claude):
    claude.answer("Transform suits that goal.", ["programs"],
                  lead={"name": "Sara", "need": "lose 8 kg", "budget": "200 USD/month", "timeline": "June"})
    mc.send(app_client, DYN, 5001, "I'm Sara, lose 8kg by June, 200 USD a month", HEADERS)
    c = mc.contacts[5001]
    assert "lead-qualified" in c.tags
    assert c.custom_fields["lead_need"] == "lose 8 kg" and c.custom_fields["lead_budget"] == "200 USD/month"


def test_handover_sets_tag_and_pause_field(app_client, mc):
    o = mc.send(app_client, DYN, 5001, "I want a refund", HEADERS)
    c = mc.contacts[5001]
    assert "human-handover" in c.tags and c.custom_fields["ai_paused"] is True
    assert "a person will reply" in o.delivered[-1]


def test_paused_contact_gets_no_bot_reply(app_client, mc, claude):
    mc.send(app_client, DYN, 5001, "I want a refund", HEADERS)
    o = mc.send(app_client, DYN, 5001, "hello?", HEADERS)
    assert o.status == "ok" and o.delivered == [] and claude.calls == []


def test_opt_out_round_trip(app_client, mc):
    mc.send(app_client, DYN, 5001, "stop", HEADERS)
    assert "opted-out" in mc.contacts[5001].tags
    assert mc.send(app_client, DYN, 5001, "price?", HEADERS).delivered == []
    mc.send(app_client, DYN, 5001, "START", HEADERS)
    assert "opted-out" not in mc.contacts[5001].tags


def test_wrong_secret_gets_401_and_manychat_runs_fallback(app_client, mc, claude):
    o = mc.send(app_client, DYN, 5001, "price?", {"X-Bridge-Secret": "wrong-secret-wrong-secret-wrong"})
    assert o.status == "fallback" and o.http_status == 401 and claude.calls == []


def test_missing_secret_without_fallback_stops_flow(app_client, mc):
    o = mc.send(app_client, DYN, 5001, "price?", {}, fallback=None)
    assert o.status == "stopped" and o.http_status == 401


def test_error_body_does_not_echo_request(app_client):
    r = app_client.post("/manychat/dynamic", content=b'{"contact":{"id":"5001","last_input_text":"my card 4242"}}',
                        headers={"X-Bridge-Secret": "x" * 30})
    assert "4242" not in r.text and r.status_code == 401


def test_bad_json_is_400(app_client):
    r = app_client.post("/manychat/dynamic", content=b"{not json", headers=HEADERS)
    assert r.status_code == 400 and r.json()["error"] == "body is not valid JSON"


def test_missing_text_is_400(app_client):
    r = app_client.post("/manychat/dynamic", json={"contact": {"id": "1", "last_input_text": ""}}, headers=HEADERS)
    assert r.status_code == 400


def test_oversized_body_is_413(app_client):
    big = json.dumps({"contact": {"id": "1", "last_input_text": "a" * 40000}})
    r = app_client.post("/manychat/dynamic", content=big, headers=HEADERS)
    assert r.status_code == 413


def test_channel_from_query_string(app_client, mc, claude):
    claude.answer("Starter costs 99 USD per month.", ["pricing"])
    body = json.dumps({"contact": {"id": "5002", "last_input_text": "price?"}})
    r = app_client.post("/manychat/dynamic?channel=telegram", content=body, headers=HEADERS)
    assert r.json()["content"]["type"] == "telegram"


def test_signed_request_accepted_then_replay_blocked(kb, claude):
    b = make_bridge(make_settings(require_signature=True), kb, claude)
    client = TestClient(create_app(b), base_url="https://testserver")
    claude.answer("Starter costs 99 USD per month.", ["pricing"])
    body = json.dumps({"channel": "instagram", "contact": {"id": "5001", "last_input_text": "price?"}}).encode()
    ts = str(int(time.time()))
    h = {"X-Bridge-Timestamp": ts, "X-Bridge-Signature": security.sign(SECRET, ts, body)}
    assert client.post("/manychat/dynamic", content=body, headers=h).status_code == 200
    assert client.post("/manychat/dynamic", content=body, headers=h).status_code == 409


def test_signed_mode_rejects_plain_secret(kb, claude):
    b = make_bridge(make_settings(require_signature=True), kb, claude)
    client = TestClient(create_app(b), base_url="https://testserver")
    r = client.post("/manychat/dynamic", json={"contact": {"id": "1", "last_input_text": "hi"}}, headers=HEADERS)
    assert r.status_code == 401


def test_per_ip_rate_limit(kb, claude):
    b = make_bridge(make_settings(per_ip_per_minute=2), kb, claude)
    client = TestClient(create_app(b), base_url="https://testserver")
    codes = [client.post("/manychat/dynamic", content=b"{}", headers=HEADERS).status_code for _ in range(3)]
    assert codes == [400, 400, 429]


def test_forwarded_ip_only_trusted_when_enabled(kb, claude):
    b = make_bridge(make_settings(per_ip_per_minute=1), kb, claude)
    client = TestClient(create_app(b, trust_proxy=True), base_url="https://testserver")
    for ip in ("1.1.1.1", "2.2.2.2"):
        r = client.post("/manychat/dynamic", content=b"{}", headers={**HEADERS, "X-Forwarded-For": ip})
        assert r.status_code == 400  # different callers, both allowed


def test_health_has_no_secrets(app_client):
    r = app_client.get("/health").json()
    assert r["ok"] and r["model"] == "claude-haiku-5-5" and r["articles"] == 10
    assert SECRET not in json.dumps(r)


def test_no_docs_endpoints_exposed(app_client):
    assert app_client.get("/docs").status_code == 404 and app_client.get("/openapi.json").status_code == 404


def test_unhandled_error_is_500_and_alerts(kb, claude):
    b = make_bridge(make_settings(), kb, claude)
    b.handle = lambda *a, **k: 1 / 0
    client = TestClient(create_app(b), base_url="https://testserver")
    r = client.post("/manychat/dynamic", json={"contact": {"id": "1", "last_input_text": "hi"}}, headers=HEADERS)
    assert r.status_code == 500 and r.json() == {"ok": False, "error": "internal error"}
    assert b.alerter.sent[-1] == ("failure", "unhandled error: ZeroDivisionError")


def test_slow_endpoint_hits_manychats_10_second_limit(mc, monkeypatch):
    monkeypatch.setattr(fake_manychat, "TIMEOUT_SECONDS", 0.2)

    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)
    client = httpx.Client(transport=httpx.MockTransport(slow))
    o = mc.send(client, "https://bridge.example/manychat/dynamic", 5001, "hi", HEADERS)
    assert o.status == "fallback" and "timed out" in o.error


def test_fake_manychat_refuses_http_urls(mc, app_client):
    with pytest.raises(ValueError):
        mc.send(app_client, "http://testserver/manychat/dynamic", 5001, "hi", HEADERS)


def test_deadline_respected_end_to_end(kb, claude, mc):
    b = make_bridge(make_settings(deadline_seconds=1.0), kb, claude)
    client = TestClient(create_app(b), base_url="https://testserver")
    claude.delay = 2.0
    claude.answer("late", ["pricing"])
    o = mc.send(client, DYN, 5001, "price?", HEADERS)
    assert o.status == "ok" and o.seconds < 1.5
    assert "ai-timeout" in mc.contacts[5001].tags


def test_logs_contain_no_personal_data(app_client, mc, claude, caplog):
    caplog.set_level(logging.INFO, logger="mc_bridge")
    claude.answer("Transform suits that.", ["programs"], lead={"name": "Sara", "budget": "999 USD"})
    mc.send(app_client, DYN, 5001, "I'm Sara Example, email sara@example.com, budget 999 USD", HEADERS)
    mc.send(app_client, DYN, 5002, "I want a refund for my card ending 4242", HEADERS)
    text = caplog.text
    for secret_bit in ("Sara", "sara@example.com", "999", "5001", "5002", "4242", "I want a refund", SECRET):
        assert secret_bit not in text, secret_bit
    assert '"event": "reply"' in text and "risk:refund" in text  # the category is logged, never the words
