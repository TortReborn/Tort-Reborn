"""TAQ-217: applications carry the questions their applicant saw, and the bot
labels answers from that copy rather than its own fixed list."""

import json

from Tasks.check_website_apps import _chunks, _format_answers, _format_hammerhead_sections

GUILD_SNAPSHOT = {"sections": [{"title": None, "task": False, "questions": [
    {"id": "ign", "label": "IGN"},
    {"id": "q_new", "label": "A question added on the website"},
    {"id": "age", "label": "Age (optional)"},
]}]}

HH_SNAPSHOT = {"sections": [
    {"title": None, "task": False, "questions": [
        {"id": "hh_ign_rank", "label": "IGN and rank?"},
        {"id": "hh_tasks", "label": "Which areas?"},
    ]},
    {"title": "Raid", "task": True, "questions": [
        {"id": "hh_raid_experience", "label": "What experience do you have with Wynncraft's raids?"},
    ]},
    {"title": "Wars", "task": True, "questions": [{"id": "hh_war_importance", "label": "Why war?"}]},
    {"title": None, "task": False, "questions": [{"id": "hh_dedication", "label": "Time?"}]},
]}


def test_guild_answers_use_the_submitted_wording_and_skip_blanks():
    answers = {"ign": "Tort", "q_new": "  yes  ", "age": ""}
    text = _format_answers(answers, "guild", GUILD_SNAPSHOT)
    assert text == "**IGN**\nTort\n\n**A question added on the website**\nyes"


def test_snapshot_arriving_as_json_text_is_read_too():
    assert _format_answers({"ign": "Tort"}, "guild", json.dumps(GUILD_SNAPSHOT)) == "**IGN**\nTort"


def test_hammerhead_shows_picked_areas_only_with_headings():
    answers = {"hh_ign_rank": "Tort, Angler", "hh_tasks": ["Raid"], "hh_raid_experience": "All of them"}
    sections = _format_hammerhead_sections(answers, HH_SNAPSHOT)
    assert sections == [
        "**IGN and rank?**\nTort, Angler\n\n**Which areas?**\nRaid",
        "__**Raid**__\n\n**What experience do you have with Wynncraft's raids?**\nAll of them",
        "**Time?**\nNo response",
    ]


def test_rows_from_before_the_snapshot_keep_the_old_labels():
    assert _format_answers({"ign": "Tort"}, "guild", None) == "**What is your IGN?**\nTort"
    legacy = _format_hammerhead_sections({"hh_ign_rank": "Tort, Angler", "hh_tasks": ["Raid"]}, None)
    assert "4 different raids" in legacy[1]
    assert _format_answers({"ign": "Tort"}, "guild", {"sections": []}) == "**What is your IGN?**\nTort"


def test_chunks_stay_under_the_limit_and_lose_nothing():
    text = "\n\n".join(f"**Q{i}**\n" + "x" * 900 for i in range(10))
    pieces = _chunks(text, 4000)
    assert len(pieces) > 1
    assert all(len(p) <= 4000 for p in pieces)
    assert "".join(pieces).replace("\n", "") == text.replace("\n", "")
