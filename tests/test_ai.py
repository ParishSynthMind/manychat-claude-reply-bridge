import json
from types import SimpleNamespace

from mc_bridge.ai import SCHEMA, ClaudeResponder, build_user_prompt, check_decision
from tests.conftest import NOLEAD, FakeAnthropic


def raw(**kw):
    base = {"action": "answer", "reply": "Transform costs 179 USD per month.", "citations": ["pricing"],
            "confidence": 0.9, "handover_reason": "", "summary": "price", "lead": dict(NOLEAD)}
    base.update(kw)
    return base


def arts(kb, *ids):
    return [kb.articles[i] for i in ids]


def test_good_answer_kept(kb):
    d = check_decision(raw(), arts(kb, "pricing"), 0.7)
    assert d.action == "answer" and d.citations == ["pricing"]


def test_no_citation_forces_handover(kb):
    d = check_decision(raw(citations=[]), arts(kb, "pricing"), 0.7)
    assert d.action == "handover" and "no valid citation" in d.reason


def test_citing_article_not_given_forces_handover(kb):
    d = check_decision(raw(citations=["pricing", "refunds"]), arts(kb, "pricing"), 0.7)
    assert d.action == "handover" and "not given" in d.reason


def test_low_confidence_forces_handover(kb):
    assert check_decision(raw(confidence=0.4), arts(kb, "pricing"), 0.7).action == "handover"


def test_link_in_reply_forces_handover(kb):
    d = check_decision(raw(reply="Pay at https://evil.example now"), arts(kb, "pricing"), 0.7)
    assert d.action == "handover" and "link" in d.reason


def test_secret_in_reply_forces_handover(kb):
    d = check_decision(raw(reply="key sk-ant-abcdefghijklmnop123"), arts(kb, "pricing"), 0.7)
    assert d.action == "handover"


def test_overlong_reply_forces_handover(kb):
    assert check_decision(raw(reply="a" * 901), arts(kb, "pricing"), 0.7).action == "handover"


def test_empty_reply_forces_handover(kb):
    assert check_decision(raw(reply=""), arts(kb, "pricing"), 0.7).action == "handover"


def test_model_handover_keeps_reason(kb):
    d = check_decision(raw(action="handover", handover_reason="refund request"), arts(kb, "pricing"), 0.7)
    assert d.action == "handover" and d.reason == "refund request"


def test_unknown_action_is_handover(kb):
    assert check_decision(raw(action="send_money"), arts(kb, "pricing"), 0.7).action == "handover"


def test_lead_only_non_empty_values(kb):
    d = check_decision(raw(lead={"name": "Sara", "need": None, "budget": "", "timeline": "null"}), arts(kb, "pricing"), 0.7)
    assert d.lead == {"name": "Sara"}


def test_lead_values_capped(kb):
    d = check_decision(raw(lead={**NOLEAD, "need": "x" * 500}), arts(kb, "pricing"), 0.7)
    assert len(d.lead["need"]) == 200


def test_bad_confidence_type_is_zero(kb):
    assert check_decision(raw(confidence="high"), arts(kb, "pricing"), 0.7).action == "handover"


def test_duplicate_citations_collapsed(kb):
    assert check_decision(raw(citations=["pricing", "pricing"]), arts(kb, "pricing"), 0.7).citations == ["pricing"]


def test_prompt_wraps_customer_text_and_articles(kb):
    p = build_user_prompt([{"role": "customer", "text": "hi"}], "how much?", arts(kb, "pricing"))
    assert '<article id="pricing">' in p and "<customer_message>\nhow much?\n</customer_message>" in p
    assert "customer: hi" in p


def test_schema_requires_all_lead_keys():
    assert SCHEMA["properties"]["lead"]["required"] == ["name", "need", "budget", "timeline"]


def test_responder_sends_model_schema_and_timeout(kb):
    fake = FakeAnthropic()
    fake.answer("Starter is 99 USD a month.", ["pricing"])
    r = ClaudeResponder("claude-haiku-5-5", "Peak Form", client=fake)
    d = r.decide([], "price?", arts(kb, "pricing"), "instagram", 0.7, timeout=7.5)
    call = fake.calls[0]
    assert call["model"] == "claude-haiku-5-5" and call["timeout"] == 7.5
    assert call["output_config"]["format"]["schema"] is SCHEMA
    assert "instagram" in call["system"] and "temperature" not in call
    assert d.action == "answer" and d.input_tokens == 600


def test_responder_refusal_and_cutoff(kb):
    for stop, reason in (("refusal", "model declined"), ("max_tokens", "model reply cut off")):
        fake = FakeAnthropic()
        fake.answer("x", ["pricing"])
        fake.stop_reason = stop
        d = ClaudeResponder("m", "b", client=fake).decide([], "q", arts(kb, "pricing"), "instagram", 0.7)
        assert d.action == "handover" and d.reason == reason


def test_responder_bad_json(kb):
    fake = FakeAnthropic()
    fake.replies.append("not json")
    assert ClaudeResponder("m", "b", client=fake).decide([], "q", arts(kb, "pricing"), "telegram", 0.7).action == "handover"


def test_responder_non_object_json(kb):
    fake = FakeAnthropic()
    fake.replies.append(json.dumps([1, 2]))
    d = ClaudeResponder("m", "b", client=fake).decide([], "q", arts(kb, "pricing"), "telegram", 0.7)
    assert d.reason == "model reply was not a JSON object"


def test_responder_ignores_non_text_blocks(kb):
    class C(FakeAnthropic):
        def create(self, **kw):
            r = super().create(**kw)
            r.content.insert(0, SimpleNamespace(type="thinking", thinking="..."))
            return r
    fake = C()
    fake.answer("Starter is 99 USD.", ["pricing"])
    assert ClaudeResponder("m", "b", client=fake).decide([], "q", arts(kb, "pricing"), "instagram", 0.7).action == "answer"
