"""A queued raid credits each player once, however their name was spelled."""

from unittest.mock import MagicMock

import Tasks.update_member_data as umd

UUID = "065fc385-f9c1-4e7f-96b3-8674a65c509f"
OTHER = "11111111-2222-3333-4444-555555555555"


def test_case_variants_of_one_player_collapse():
    rows = [{"uuid": UUID, "ign": "Alex"}, {"uuid": UUID.upper(), "ign": "ALEX"}, {"uuid": UUID, "ign": "alex"}]
    assert umd._unique_participants(rows) == [rows[0]]


def test_players_without_a_uuid_collapse_by_name():
    rows = [{"uuid": None, "ign": "Alex"}, {"uuid": None, "ign": "ALEX"}]
    assert umd._unique_participants(rows) == [rows[0]]


def test_different_players_are_kept_in_order():
    rows = [{"uuid": UUID, "ign": "Alex"}, {"uuid": OTHER, "ign": "Sam"}, {"uuid": None, "ign": "Kai"}]
    assert umd._unique_participants(rows) == rows


def test_a_raid_credits_a_repeated_player_once(monkeypatch):
    db = MagicMock()
    db.cursor.fetchone.side_effect = [(7,), (42,)]
    monkeypatch.setattr(umd, "_db_connect_with_retry", lambda: db)
    umd._graid_log_manual_sync(
        [{"uuid": UUID, "ign": "Alex"}, {"uuid": UUID, "ign": "ALEX"}], "The Canyon Colossus",
    )
    credits = [c for c in db.cursor.execute.call_args_list if "uncollected_raids AS ur" in c.args[0]]
    assert len(credits) == 1
