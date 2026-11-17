import pytest

from mc_bridge.fake_manychat import ValidationError, validate_v2
from mc_bridge.formatter import Reply, dynamic_block, external_json, split_text

TAGS = {"ai-answered", "human-handover", "lead-qualified", "opted-out", "ai-timeout"}
FIELDS = {"lead_name", "lead_need", "lead_budget", "lead_timeline", "ai_paused"}


def answered(**kw):
    base = dict(texts=["Transform costs 179 USD per month."], add_tags=["ai-answered"],
                buttons=[{"caption": "Book a free call", "url": "https://example.com/book"}],
                quick_replies=[{"type": "flow", "caption": "See plans", "target": "content20261117"}])
    base.update(kw)
    return Reply("answered", **base)


@pytest.mark.parametrize("channel", ["instagram", "whatsapp", "telegram"])
def test_channel_type_set_for_non_messenger(channel):
    out, _ = dynamic_block(answered(), channel)
    assert out["version"] == "v2" and out["content"]["type"] == channel
    validate_v2(out, channel, TAGS, FIELDS)


def test_messenger_has_no_content_type():
    out, _ = dynamic_block(answered(), "messenger")
    assert "type" not in out["content"]
    validate_v2(out, "messenger", TAGS, FIELDS)


@pytest.mark.parametrize("channel", ["whatsapp", "telegram"])
def test_no_quick_replies_on_whatsapp_or_telegram(channel):
    out, _ = dynamic_block(answered(), channel)
    assert out["content"]["quick_replies"] == []


@pytest.mark.parametrize("channel", ["instagram", "messenger"])
def test_quick_replies_on_instagram_and_messenger(channel):
    out, _ = dynamic_block(answered(), channel)
    assert out["content"]["quick_replies"][0]["target"] == "content20261117"


def test_buttons_attached_to_last_message_only():
    out, _ = dynamic_block(answered(texts=["I'm an AI assistant.", "Answer."]), "instagram")
    msgs = out["content"]["messages"]
    assert "buttons" not in msgs[0] and msgs[1]["buttons"][0]["type"] == "url"


def test_button_caption_trimmed_to_20():
    out, _ = dynamic_block(answered(buttons=[{"caption": "x" * 40, "url": "https://e.com"}]), "instagram")
    assert len(out["content"]["messages"][0]["buttons"][0]["caption"]) == 20


def test_actions_capped_at_five_and_overflow_returned():
    r = answered(add_tags=["lead-qualified", "ai-answered"],
                 set_fields={"lead_name": "Sara", "lead_need": "fat loss", "lead_budget": "200", "lead_timeline": "June"})
    out, overflow = dynamic_block(r, "instagram")
    assert len(out["content"]["actions"]) == 5 and len(overflow) == 1
    assert out["content"]["actions"][0] == {"action": "add_tag", "tag_name": "lead-qualified"}
    validate_v2(out, "instagram", TAGS, FIELDS)


def test_handover_tag_comes_before_fields():
    r = Reply("handover", texts=["x"], add_tags=["human-handover"], set_fields={"ai_paused": True})
    acts = dynamic_block(r, "telegram")[0]["content"]["actions"]
    assert acts[0]["action"] == "add_tag" and acts[1] == {"action": "set_field_value", "field_name": "ai_paused", "value": True}


def test_remove_tag_action():
    acts = dynamic_block(Reply("opted_in", texts=["hi"], remove_tags=["opted-out"]), "instagram")[0]["content"]["actions"]
    assert acts == [{"action": "remove_tag", "tag_name": "opted-out"}]


def test_long_text_split_for_instagram_limit():
    long = ("This is a sentence about training. " * 60).strip()
    out, _ = dynamic_block(answered(texts=[long]), "instagram")
    assert all(len(m["text"]) <= 1000 for m in out["content"]["messages"])
    assert len(out["content"]["messages"]) >= 2


def test_never_more_than_ten_messages():
    out, _ = dynamic_block(answered(texts=["word " * 3000]), "instagram")
    assert len(out["content"]["messages"]) == 10


def test_split_text_keeps_short_text():
    assert split_text("Hello there.", 1000) == ["Hello there."]


def test_split_text_empty():
    assert split_text("   ", 1000) == []


def test_split_text_hard_cuts_a_huge_word():
    parts = split_text("x" * 2500, 1000)
    assert [len(p) for p in parts] == [1000, 1000, 500]


def test_empty_reply_is_valid_silence():
    out, _ = dynamic_block(Reply("paused"), "instagram")
    assert out["content"]["messages"] == [] and out["content"]["quick_replies"] == []
    validate_v2(out, "instagram", TAGS, FIELDS)


def test_external_json_flat_values():
    r = answered(sources=["Prices", "Coaching programs"], lead={"need": "fat loss", "budget": "200", "timeline": "June"})
    out = external_json(r)
    assert out["reply"] == "Transform costs 179 USD per month."
    assert out["sources"] == "Prices; Coaching programs"
    assert out["lead_qualified"] is True and out["lead_name"] == ""
    assert out["handover"] is False and out["opted_out"] is False


def test_external_json_joins_disclosure_and_answer():
    out = external_json(answered(texts=["I'm an AI assistant.", "Answer."]))
    assert out["reply"] == "I'm an AI assistant.\n\nAnswer."


def test_reply_round_trip():
    r = answered(lead={"name": "Sara"})
    assert Reply.from_dict(r.to_dict()) == r


def test_validator_catches_wrong_type_for_channel():
    out, _ = dynamic_block(answered(), "instagram")
    with pytest.raises(ValidationError):
        validate_v2(out, "telegram", TAGS, FIELDS)
