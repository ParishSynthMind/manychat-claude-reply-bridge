import pytest

from mc_bridge.inbound import BadRequest, guess_channel, parse, parse_time


def body(**contact):
    return {"channel": "instagram", "contact": {"id": "5001", "last_input_text": "hi", **contact}}


def test_wrapped_body_parsed():
    m = parse(body(first_name="Sara"), "instagram", 1000)
    assert (m.contact_id, m.text, m.channel, m.first_name) == ("5001", "hi", "instagram", "Sara")


def test_bare_full_contact_data_accepted():
    m = parse({"id": 77, "last_input_text": "price?", "ig_username": "x"}, "telegram", 1000)
    assert m.contact_id == "77" and m.channel == "instagram"


def test_explicit_text_overrides_last_input():
    assert parse({**body(), "text": "custom"}, "instagram", 1000).text == "custom"


def test_text_is_capped():
    assert len(parse(body(last_input_text="a" * 5000), "instagram", 1000).text) == 1000


@pytest.mark.parametrize("cid", [None, "", "abc", "12; DROP TABLE"])
def test_bad_contact_id_rejected(cid):
    with pytest.raises(BadRequest):
        parse({"contact": {"id": cid, "last_input_text": "hi"}}, "instagram", 1000)


def test_empty_text_rejected():
    with pytest.raises(BadRequest):
        parse(body(last_input_text="   "), "instagram", 1000)


def test_non_object_body_rejected():
    with pytest.raises(BadRequest):
        parse(["a"], "instagram", 1000)


def test_unknown_channel_rejected():
    with pytest.raises(BadRequest):
        parse({**body(), "channel": "myspace"}, "instagram", 1000)


def test_facebook_alias_is_messenger():
    assert parse({**body(), "channel": "facebook"}, "instagram", 1000).channel == "messenger"


def test_query_channel_wins():
    assert parse(body(), "instagram", 1000, query_channel="telegram").channel == "telegram"


def test_custom_fields_list_form():
    m = parse(body(custom_fields=[{"id": 1, "name": "lead_budget", "value": "200"}]), "instagram", 1000)
    assert m.custom_fields == {"lead_budget": "200"}


def test_custom_fields_dict_form():
    assert parse(body(custom_fields={"ai_paused": True}), "instagram", 1000).custom_fields == {"ai_paused": True}


def test_tags_as_objects_and_strings():
    assert parse(body(tags=[{"id": 1, "name": "vip"}, "opted-out"]), "instagram", 1000).tags == ["vip", "opted-out"]


def test_last_interaction_parsed_with_timezone():
    m = parse(body(last_interaction="2026-11-17T20:10:00+04:00"), "instagram", 1000)
    assert m.last_interaction.utcoffset().total_seconds() == 4 * 3600


@pytest.mark.parametrize("value", [None, "", "not a date", 123])
def test_bad_times_ignored(value):
    assert parse_time(value) is None


def test_naive_time_assumed_utc():
    assert parse_time("2026-11-17T10:00:00").utcoffset().total_seconds() == 0


def test_guess_channel_whatsapp_and_default():
    assert guess_channel({"whatsapp_phone": "+971"}, "instagram") == "whatsapp"
    assert guess_channel({"tg_id": 5}, "instagram") == "telegram"
    assert guess_channel({}, "messenger") == "messenger"


def test_contact_ref_hides_id_and_is_stable():
    m = parse(body(), "instagram", 1000)
    assert m.contact_ref == parse(body(), "instagram", 1000).contact_ref
    assert "5001" not in m.contact_ref and len(m.contact_ref) == 12


def test_idempotency_key_changes_with_text_and_time():
    a = parse(body(last_interaction="2026-11-17T10:00:00Z"), "instagram", 1000)
    b = parse(body(last_interaction="2026-11-17T10:00:05Z"), "instagram", 1000)
    c = parse(body(last_interaction="2026-11-17T10:00:00Z", last_input_text="other"), "instagram", 1000)
    assert len({a.idempotency_key(), b.idempotency_key(), c.idempotency_key()}) == 3
