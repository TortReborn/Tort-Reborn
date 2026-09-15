"""The Wynncraft player API sends explicit nulls (``"level": null``,
``"globalData": null``) for some accounts; those must not abort the scan."""
from Tasks.recruitment_checker import RecruitmentChecker


# Instantiating the cog builds a tasks.loop, which wants a running event loop;
# the helper is pure so call it unbound.
max_level = RecruitmentChecker.get_max_character_level


def test_max_level_ignores_null_levels():
    characters = {
        "a": {"level": None},
        "b": {"level": 87},
        "c": None,
        "d": {},
    }
    assert max_level(None, characters) == 87


def test_max_level_all_null_is_zero():
    assert max_level(None, {"a": {"level": None}}) == 0
    assert max_level(None, {}) == 0
