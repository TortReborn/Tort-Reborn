"""User text can't ping roles or @everyone, and intended pings still work."""

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from Helpers import mentions

ROOT = Path(__file__).resolve().parent.parent
PING = re.compile(r"<@&|@everyone |_ROLE_MENTION\}")
ALLOWS = re.compile(r"role_ping\(|EVERYONE_PING|allowed_mentions")


def test_default_only_allows_users():
    assert mentions.DEFAULT_MENTIONS.to_dict()["parse"] == ["users"]
    assert "roles" not in mentions.DEFAULT_MENTIONS.to_dict()


def test_role_ping_allows_one_role_only():
    allowed = mentions.role_ping(123).to_dict()
    assert allowed["parse"] == [] and allowed["roles"] == [123] and "users" not in allowed


def test_user_ping_allows_one_user_only():
    allowed = mentions.user_ping("456").to_dict()
    assert allowed["parse"] == [] and allowed["users"] == [456] and "roles" not in allowed


def test_error_report_pings_only_the_owner():
    allowed = mentions.error_report_mentions("456").to_dict()
    assert allowed["parse"] == [] and allowed["users"] == [456]


@pytest.mark.parametrize("bad", ["", None, "abc", "12a"])
def test_a_bad_owner_id_does_not_stop_the_error_report(bad):
    assert mentions.error_report_mentions(bad).to_dict() == {"parse": []}


def test_text_cannot_close_a_code_block():
    assert "`" not in mentions.fence_safe("x```@everyone <@&1>```")


def test_every_file_that_pings_a_role_says_so():
    """The client default silently drops role and @everyone pings, so a file
    that writes one has to allow it."""
    missing = []
    for folder in ("Commands", "Events", "Helpers", "Tasks", "UserCommands"):
        for path in (ROOT / folder).rglob("*.py"):
            if path.name in {"variables.py", "mentions.py", "role_buttons.py"}:
                continue
            source = path.read_text(encoding="utf-8")
            if PING.search(source) and not ALLOWS.search(source):
                missing.append(str(path.relative_to(ROOT)))
    assert missing == []


def test_client_uses_the_default():
    assert "allowed_mentions=DEFAULT_MENTIONS" in (ROOT / "main.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("length", [10, 3000])
def test_chunked_application_text_keeps_its_mention_rule(length):
    from Tasks.check_website_apps import _send_chunked

    channel = SimpleNamespace(send=AsyncMock())
    asyncio.run(_send_chunked(channel, "a" * length, allowed_mentions=mentions.NO_MENTIONS))
    assert channel.send.await_count >= 1
    for call in channel.send.await_args_list:
        assert call.kwargs["allowed_mentions"] is mentions.NO_MENTIONS


def test_progress_reports_an_unknown_player():
    import Commands.progress as progress

    message = MagicMock(defer=AsyncMock(), respond=AsyncMock())
    missing = SimpleNamespace(error=True)
    original = progress.PlayerStats
    progress.PlayerStats = lambda *_args: missing
    try:
        cog = progress.Progress(MagicMock())
        asyncio.run(cog.progress.callback(cog, message, "Nobody"))
    finally:
        progress.PlayerStats = original
    assert "Could not find" in message.respond.await_args.kwargs["embed"].description


def test_log_batches_send_with_no_mentions(monkeypatch):
    """The send sits in a try/except, so a mistake here would lose every log line quietly."""
    from Helpers import logger

    channel = SimpleNamespace(send=AsyncMock())
    client = SimpleNamespace(get_channel=lambda _id: channel, is_closed=lambda: False)
    monkeypatch.setattr(logger, "_client", client, raising=False)
    logger._queue.clear()
    logger._queue.append("a log line")
    asyncio.run(logger._flush())
    channel.send.assert_awaited_once()
    assert channel.send.await_args.kwargs["allowed_mentions"] is mentions.NO_MENTIONS
