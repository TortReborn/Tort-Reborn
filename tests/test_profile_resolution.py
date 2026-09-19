"""Profile resolution (TAQ-95): the config profile comes from the token's own
application id, BOT_PROFILE is a fallback for tokenless contexts only, and
every ambiguous state refuses to start rather than guessing."""

import base64

import pytest

from Helpers import variables


def _fake_token(app_id: str) -> str:
    seg = base64.urlsafe_b64encode(app_id.encode()).decode().rstrip("=")
    return f"{seg}.fake.signature"


TEST_APP_ID = next(k for k, v in variables._PROFILE_BY_APP_ID.items() if v == "test")
PROD_APP_ID = next(k for k, v in variables._PROFILE_BY_APP_ID.items() if v == "prod")


def test_app_id_decodes_from_token_prefix():
    assert variables._app_id_from_token(_fake_token("1234567890")) == "1234567890"


def test_app_id_rejects_garbage():
    assert variables._app_id_from_token("!!!not-base64!!!.x.y") is None


def test_app_id_rejects_non_numeric_payload():
    seg = base64.urlsafe_b64encode(b"not-a-snowflake").decode().rstrip("=")
    assert variables._app_id_from_token(f"{seg}.x.y") is None


def test_known_test_token_selects_test_profile(monkeypatch):
    monkeypatch.setenv("TOKEN", _fake_token(TEST_APP_ID))
    monkeypatch.delenv("BOT_PROFILE", raising=False)
    assert variables._resolve_profile() == "test"


def test_known_prod_token_selects_prod_profile(monkeypatch):
    monkeypatch.setenv("TOKEN", _fake_token(PROD_APP_ID))
    monkeypatch.delenv("BOT_PROFILE", raising=False)
    assert variables._resolve_profile() == "prod"


def test_unknown_app_id_fails_closed(monkeypatch):
    monkeypatch.setenv("TOKEN", _fake_token("999999999999999999"))
    monkeypatch.delenv("BOT_PROFILE", raising=False)
    with pytest.raises(RuntimeError, match="known bot application"):
        variables._resolve_profile()


def test_undecodable_token_fails_closed(monkeypatch):
    monkeypatch.setenv("TOKEN", "!!!not-base64!!!.x.y")
    monkeypatch.delenv("BOT_PROFILE", raising=False)
    with pytest.raises(RuntimeError, match="known bot application"):
        variables._resolve_profile()


def test_override_agreeing_with_token_is_allowed(monkeypatch):
    monkeypatch.setenv("TOKEN", _fake_token(TEST_APP_ID))
    monkeypatch.setenv("BOT_PROFILE", "test")
    assert variables._resolve_profile() == "test"


def test_override_disagreeing_with_token_fails_closed(monkeypatch):
    monkeypatch.setenv("TOKEN", _fake_token(PROD_APP_ID))
    monkeypatch.setenv("BOT_PROFILE", "test")
    with pytest.raises(RuntimeError, match="disagrees"):
        variables._resolve_profile()


def test_no_token_honors_override(monkeypatch):
    monkeypatch.setenv("TOKEN", "")
    monkeypatch.setenv("BOT_PROFILE", "test")
    assert variables._resolve_profile() == "test"


def test_no_token_no_override_fails_closed(monkeypatch):
    monkeypatch.setenv("TOKEN", "")
    monkeypatch.delenv("BOT_PROFILE", raising=False)
    with pytest.raises(RuntimeError, match="cannot select"):
        variables._resolve_profile()
