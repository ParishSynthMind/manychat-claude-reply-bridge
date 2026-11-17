import time

import pytest

from mc_bridge import security
from tests.conftest import SECRET, SECRET2

BODY = b'{"contact":{"id":"1"}}'


def test_correct_secret_accepted():
    assert security.check({"X-Bridge-Secret": SECRET}, BODY, [SECRET], False, 300).ok


def test_header_name_is_case_insensitive():
    assert security.check({"x-bridge-secret": SECRET}, BODY, [SECRET], False, 300).ok


def test_rotated_second_secret_accepted():
    assert security.check({"X-Bridge-Secret": SECRET2}, BODY, [SECRET, SECRET2], False, 300).ok


def test_wrong_secret_rejected():
    r = security.check({"X-Bridge-Secret": "nope" * 8}, BODY, [SECRET], False, 300)
    assert not r.ok and r.reason == "bad secret"


def test_missing_secret_rejected():
    r = security.check({}, BODY, [SECRET], False, 300)
    assert not r.ok and r.reason == "missing secret"


def test_no_configured_secret_rejects_everything():
    r = security.check({"X-Bridge-Secret": SECRET}, BODY, [], False, 300)
    assert not r.ok


def test_prefix_of_secret_rejected():
    assert not security.check({"X-Bridge-Secret": SECRET[:-1]}, BODY, [SECRET], False, 300).ok


def _signed(secret=SECRET, ts=None, body=BODY):
    ts = str(int(time.time()) if ts is None else ts)
    return {"X-Bridge-Timestamp": ts, "X-Bridge-Signature": security.sign(secret, ts, body)}


def test_valid_signature_accepted_and_returns_nonce():
    r = security.check(_signed(), BODY, [SECRET], True, 300)
    assert r.ok and r.nonce.startswith("v1=")


def test_signature_with_rotated_secret_accepted():
    assert security.check(_signed(SECRET2), BODY, [SECRET, SECRET2], True, 300).ok


def test_signature_over_different_body_rejected():
    r = security.check(_signed(), BODY + b" ", [SECRET], True, 300)
    assert not r.ok and r.reason == "bad signature"


def test_old_timestamp_rejected():
    r = security.check(_signed(ts=int(time.time()) - 301), BODY, [SECRET], True, 300)
    assert not r.ok and r.reason == "timestamp outside window"


def test_future_timestamp_rejected():
    assert not security.check(_signed(ts=int(time.time()) + 400), BODY, [SECRET], True, 300).ok


def test_non_numeric_timestamp_rejected():
    h = _signed()
    h["X-Bridge-Timestamp"] = "yesterday"
    assert security.check(h, BODY, [SECRET], True, 300).reason == "bad timestamp"


def test_require_signature_refuses_plain_secret():
    r = security.check({"X-Bridge-Secret": SECRET}, BODY, [SECRET], True, 300)
    assert not r.ok and r.reason == "signature required"


def test_signature_used_even_when_not_required():
    h = {**_signed(), "X-Bridge-Secret": "wrong"}
    assert security.check(h, BODY, [SECRET], False, 300).ok


@pytest.mark.parametrize("bad", ["v1=", "v1=00", "sha256=abc", ""])
def test_malformed_signatures_rejected(bad):
    h = _signed()
    h["X-Bridge-Signature"] = bad
    assert not security.check(h, BODY, [SECRET], True, 300).ok
