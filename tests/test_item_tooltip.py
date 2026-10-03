import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Helpers.item_tooltip import ItemTooltipBridge, find_item_codes
from Helpers.artemis_item import decode_crafted_gear, decode_gear
from Helpers.item_tooltip_render import (
    build_crafted_lines,
    build_lines,
    calculate_custom_scales,
    item_from_api,
    item_from_gear,
    render_crafted_tooltip,
    render_item_tooltip,
    stat_names,
)
from Helpers.minecraft_tooltip import _stats
from Helpers.wynn_items import WynnItemIndex


def _item_code(tag: int) -> str:
    return "\U000F0000\U000F0100" + chr(0xF0010 + tag)


ITEM_SHARE_WITH_NAME = f'{_item_code(1)} "Divzer qol"'
PROWESS_CODE = (
    "\U000F0002\U000F0100\U000F0250\U000F726F\U000F7765\U000F7373"
    "\U000F00FF"
)
VOLATILITY_CODE = (
    "\U000F0002\U000F0100\U000F0256\U000F6F6C\U000F6174\U000F696C"
    "\U000F6974\U000F7900\U000F0306\U000F0075\U000F1904\U000F141E"
    "\U000FC705\U000F0416\U000F1174\U000F0415\U000F6658\U000F0423"
    "\U000F0132\U000F041F\U000F0226\U000F0419\U000F0403\U000F0005"
    "\U000F02FF"
)
WILD_GROWTH_CODE = (
    "\U000F0002\U000F0100\U000F0257\U000F696C\U000F6420\U000F4772"
    "\U000F6F77\U000F7468\U000F0003\U000F0500\U000F220E\U000F040D"
    "\U000F1980\U000F0504\U000F1F30\U000FC401\U000F040D\U000F482A"
    "\U000F0419\U000F511B\U000F041D\U000F0403\U000F0005\U000F03FF"
)
TOME_CODE = (
    "\U000F0002\U000F0101\U000F0249\U000F6E66\U000F6572\U000F6E61"
    "\U000F6C20\U000F546F\U000F6D65\U000F206F\U000F6620\U000F436F"
    "\U000F6D62\U000F6174\U000F204D\U000F6173\U000F7465\U000F7279"
    "\U000F2049\U000F4949\U000F0003\U000F0200\U000F660A\U000F0423"
    "\U000F1210\U000F041C\U000F0502\U0010FFEE"
)
IONIC_SPARK_ENTRY = {
    "displayName": "Ionic Spark",
    "internalName": "Volatility",
    "tier": "unique",
    "identifications": {
        "exploding": {"min": 6, "raw": 20, "max": 26},
        "manaRegen": {"min": -8, "raw": -6, "max": -4},
        "walkSpeed": {"min": 6, "raw": 20, "max": 26},
        "healthRegen": {"min": -32, "raw": -25, "max": -17},
        "waterDamage": {"min": 8, "raw": 27, "max": 35},
        "rawDexterity": 8,
        "thunderDamage": {"min": 8, "raw": 27, "max": 35},
    },
}
VOLATILITY_ENTRY = {
    "displayName": "Volatility",
    "internalName": "Volatility2",
    "tier": "fabled",
    "identifications": {
        "damage": {"min": 10, "raw": 34, "max": 44},
        "exploding": {"min": 18, "raw": 60, "max": 78},
        "lifeSteal": {"min": -520, "raw": -400, "max": -280},
        "rawDefence": 10,
        "rawMaxMana": {"min": -18, "raw": -14, "max": -10},
        "2ndSpellCost": {"min": -6, "raw": -20, "max": -26},
        "3rdSpellCost": {"min": -5, "raw": -18, "max": -23},
    },
}
WILD_GROWTH_ENTRY = {
    "displayName": "Wild Growth",
    "internalName": "Wild Growth",
    "tier": "rare",
    "identifications": {
        "manaRegen": {"min": 3, "raw": 10, "max": 13},
        "walkSpeed": {"min": -23, "raw": -18, "max": -13},
        "spellDamage": {"min": 6, "raw": 19, "max": 25},
        "healthRegenRaw": {"min": 76, "raw": 252, "max": 328},
        "rawEarthDamage": {"min": 41, "raw": 138, "max": 179},
    },
}
PROWESS_ENTRY = {
    "displayName": "Prowess",
    "internalName": "Prowess",
    "type": "accessory",
    "subType": "bracelet",
    "tier": "legendary",
    "restriction": "untradable",
    "identified": True,
    "requirements": {"level": 100, "quest": "The Qira Hive"},
    "identifications": {
        "rawAgility": 4,
        "rawDefence": 4,
        "rawStrength": 4,
        "rawDexterity": 4,
        "rawIntelligence": 4,
    },
}
CRAFTED_RING = (
    "\U000F0002\U000F0103\U000F0705\U000F0886\U000F013A\U000F0967"
    "\U000F0005\U000F0028\U000F0148\U000F0264\U000F0300\U000F0400"
    "\U000F0C04\U000F3E08\U000F0723\U000F2F0C\U000F0611\U000F220C"
    "\U000F0523\U000F480A\U000F0408\U0010FFEE"
)


class TestFindItemCodes:
    def test_no_items_returns_empty(self):
        assert find_item_codes("just a normal guild chat message") == []

    def test_quoted_name_suffix_folded_into_consumed_span(self):
        matches = find_item_codes(ITEM_SHARE_WITH_NAME)
        assert len(matches) == 1
        left, right, code, name_hint = matches[0]
        assert code == _item_code(1)
        assert right == len(ITEM_SHARE_WITH_NAME)
        assert name_hint == "Divzer qol"

    def test_bare_code_without_name_suffix(self):
        code = _item_code(1)
        text = f"check this out {code} nice right"
        matches = find_item_codes(text)
        assert len(matches) == 1
        left, right, matched_code, name_hint = matches[0]
        assert matched_code == code
        assert name_hint is None
        assert text[left:right] == code

    def test_multiple_distinct_items_in_one_message(self):
        text = f"{_item_code(1)} and also {_item_code(2)}"
        matches = find_item_codes(text)
        assert len(matches) == 2
        assert matches[0][2] != matches[1][2]

    def test_crafted_item_and_name_are_detected(self):
        matches = find_item_codes(f'{CRAFTED_RING} "Embodiment of Scam"')
        assert len(matches) == 1
        assert matches[0][2] == CRAFTED_RING
        assert matches[0][3] == "Embodiment of Scam"

    def test_tome_is_detected(self):
        matches = find_item_codes(TOME_CODE)
        assert len(matches) == 1
        assert matches[0][2] == TOME_CODE

    def test_oversized_message_rejected(self):
        with pytest.raises(ValueError):
            find_item_codes("x" * 4001)


class TestItemFromApi:
    def _decoded(self, *, identifications=None, rolled=None, original_overrides=None):
        original = {
            "id": "Test Item",
            "displayName": "Test Item",
            "tier": "legendary",
            "identifications": identifications if identifications is not None else {
                "rawHealth": {"min": 0, "raw": 100, "max": 200},
                "rawStrength": {"min": 0, "raw": 10, "max": 20},
            },
        }
        if original_overrides:
            original.update(original_overrides)
        return {
            "original": original,
            "input": {
                "identifications": rolled if rolled is not None else {"rawHealth": 100, "rawStrength": 50},
                "rerollCount": 2,
            },
        }

    def _weights(self, item_id="Test Item"):
        return [{"item_id": item_id, "weight_name": "Main", "identifications": {"rawHealth": 1, "rawStrength": 2}}]

    def test_valid_item_builds_and_renders(self):
        item = item_from_api(self._decoded(), self._weights())
        assert item["itemName"] == "Test Item"
        assert item["stats"]["Health"] == 100
        assert round(item["rate"]["Health"]) == 50
        lines = build_lines(item)
        assert lines[0].text.startswith("Test Item")
        png = render_item_tooltip(item)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_all_tome_subtypes_use_the_tome_sprite(self):
        item = item_from_api(
            self._decoded(original_overrides={"type": "tome", "subType": "weapon_tome"}),
            self._weights(),
        )
        expected = render_item_tooltip(item)

        for subtype in (
            "armour_tome",
            "expertise_tome",
            "guild_tome",
            "lootrun_tome",
            "marathon_tome",
            "mysticism_tome",
            "weapon_tome",
        ):
            candidate = {**item, "entry": {**item["entry"], "subType": subtype}}
            assert render_item_tooltip(candidate) == expected

    def test_rounds_negative_stat_before_range_validation(self):
        decoded = self._decoded(
            identifications={"manaRegen": {"min": -58, "raw": -45, "max": -31}},
            rolled={"manaRegen": 129},
        )
        weights = [{
            "item_id": "Test Item",
            "weight_name": "Main",
            "identifications": {"manaRegen": 1},
        }]

        item = item_from_api(decoded, weights)

        assert item["stats"]["Mana Regen"] == -58
        assert item["rate"]["Mana Regen"] == 0
        assert render_item_tooltip(item)[:8] == b"\x89PNG\r\n\x1a\n"

    def test_uses_inverted_rounding_for_spell_costs(self):
        decoded = self._decoded(
            identifications={"raw1stSpellCost": {"min": -13, "raw": -10, "max": -7}},
            rolled={"raw1stSpellCost": 75},
        )
        weights = [{
            "item_id": "Test Item",
            "weight_name": "Main",
            "identifications": {"raw1stSpellCost": 1},
        }]

        item = item_from_api(decoded, weights)

        assert item["stats"]["1st Spell Cost"] == -8

    def test_mismatched_wynnpool_item_id_rejected(self):
        with pytest.raises(ValueError):
            item_from_api(self._decoded(), self._weights(item_id="Other Item"))

    def test_missing_rolled_identifications_rejected(self):
        with pytest.raises(ValueError):
            item_from_api(self._decoded(rolled={}), self._weights())

    def test_missing_range_for_rolled_stat_rejected(self):
        only_health = {"rawHealth": {"min": 0, "raw": 100, "max": 200}}
        with pytest.raises(ValueError):
            item_from_api(self._decoded(identifications=only_health), self._weights())

    def test_degenerate_range_rejected(self):
        degenerate = {
            "rawHealth": {"min": 100, "raw": 100, "max": 100},
            "rawStrength": {"min": 0, "raw": 10, "max": 20},
        }
        with pytest.raises(ValueError):
            item_from_api(self._decoded(identifications=degenerate), self._weights())

    def test_negative_scale_weight_inverts_roll(self):
        item = item_from_api(self._decoded(), [{
            "item_id": "Test Item",
            "weight_name": "Main",
            "identifications": {"rawHealth": -1, "rawStrength": 1},
        }])
        assert calculate_custom_scales(item)[0].score == pytest.approx(37.5)

    def test_percent_stat_names(self):
        mapping = stat_names()
        assert mapping["reflection"] == "Reflection %"
        assert mapping["thunderDamage"] == "Thunder Damage %"
        assert mapping["criticalDamageBonus"] == "Critical Damage Bonus %"


class TestCraftedItem:
    def test_decodes_and_renders_logged_ring(self):
        item = decode_crafted_gear(CRAFTED_RING, "Embodiment of Scam")
        assert item["itemName"] == "Embodiment of Scam"
        assert item["gearType"] == "Ring"
        assert item["durability"] == {"effectStrength": 100, "max": 67, "current": 29}
        assert item["requirements"]["level"] == 103
        assert item["identifications"] == [
            ("rawStrength", 4),
            ("rawDexterity", 6),
            ("manaRegen", 6),
            ("spellDamage", 5),
        ]
        lines = build_crafted_lines(item)
        assert any(line.text == "+5% Spell Damage" for line in lines)
        assert render_crafted_tooltip(item)[:8] == b"\x89PNG\r\n\x1a\n"


class TestItemTooltipBridgePrepare:
    def _bridge(self, monkeypatch, render_result=None, render_error=None, calls=None):
        bridge = ItemTooltipBridge()

        async def fake_render(code, name_hint):
            if calls is not None:
                calls.append((code, name_hint))
            if render_error is not None:
                raise render_error
            return render_result or ("Fake Item", b"fake-png-bytes")

        monkeypatch.setattr(bridge, "_render", fake_render)
        return bridge

    @pytest.mark.asyncio
    async def test_no_items_passes_text_through(self, monkeypatch):
        bridge = self._bridge(monkeypatch)
        prepared = await bridge.prepare("just chatting")
        assert prepared.content == "just chatting"
        assert prepared.attachments == ()

    @pytest.mark.asyncio
    async def test_dedups_repeated_code(self, monkeypatch):
        calls = []
        bridge = self._bridge(monkeypatch, calls=calls)
        code = _item_code(1)
        prepared = await bridge.prepare(f"{code} and again {code}")
        assert calls == [(code, None)]
        assert prepared.content == "Fake Item and again Fake Item"
        assert len(prepared.attachments) == 1

    @pytest.mark.asyncio
    async def test_item_limit_falls_back_for_extra_items(self, monkeypatch):
        calls = []
        bridge = self._bridge(monkeypatch, calls=calls)
        codes = [_item_code(i) for i in range(5)]
        prepared = await bridge.prepare(" ".join(codes))
        assert len(calls) == 4
        assert prepared.content.count("Fake Item") == 4
        assert "[item preview limit reached]" in prepared.content
        assert prepared.errors

    @pytest.mark.asyncio
    async def test_render_failure_falls_back_to_placeholder(self, monkeypatch):
        bridge = self._bridge(monkeypatch, render_error=ValueError("wynnpool is down"))
        prepared = await bridge.prepare(_item_code(1))
        assert prepared.content == "[item preview unavailable]"
        assert prepared.attachments == ()
        assert prepared.errors

    @pytest.mark.asyncio
    async def test_quoted_name_suffix_not_duplicated(self, monkeypatch):
        bridge = self._bridge(monkeypatch, render_result=("Divzer qol", b"png"))
        prepared = await bridge.prepare(f'{_item_code(1)} "Divzer qol"')
        assert prepared.content == "Divzer qol"

    @pytest.mark.asyncio
    async def test_quoted_name_hint_reaches_render(self, monkeypatch):
        calls = []
        bridge = self._bridge(monkeypatch, calls=calls)
        code = _item_code(1)
        await bridge.prepare(f'{code} "Divzer qol"')
        assert calls == [(code, "Divzer qol")]

    @pytest.mark.asyncio
    async def test_busy_semaphore_raises(self, monkeypatch):
        bridge = self._bridge(monkeypatch)
        await bridge._slots.acquire()
        await bridge._slots.acquire()
        with pytest.raises(ValueError):
            await bridge.prepare(_item_code(1))
        bridge._slots.release()
        bridge._slots.release()


class TestDecodeGear:
    def test_decodes_actual_values_and_reroll_count(self):
        decoded = decode_gear(VOLATILITY_CODE)
        assert decoded["itemName"] == "Volatility"
        assert decoded["rerollCount"] == 2
        assert decoded["powders"] == {"slots": 3, "powders": []}
        assert decoded["shiny"] is None
        assert [(stat["key"], stat["value"]) for stat in decoded["identifications"]] == [
            ("rawMaxMana", -13),
            ("lifeSteal", -356),
            ("exploding", 58),
            ("damage", 44),
            ("2ndSpellCost", 25),
            ("3rdSpellCost", 19),
        ]
        assert all(stat["kind"] == "actual" for stat in decoded["identifications"])

    def test_rejects_unsupported_version(self):
        with pytest.raises(ValueError, match="version"):
            decode_gear("\U000F0000\U000F0100\U000F0011")

    def test_rejects_crafted_code(self):
        with pytest.raises(ValueError):
            decode_gear(CRAFTED_RING)


class TestWynnItemIndex:
    def _index(self, *entries):
        index = WynnItemIndex()
        index.load(list(entries))
        return index

    def test_reused_internal_name_resolves_by_stat_set(self):
        index = self._index(IONIC_SPARK_ENTRY, VOLATILITY_ENTRY)
        decoded = decode_gear(VOLATILITY_CODE)
        entry = index.resolve(decoded["itemName"], decoded["identifications"])
        assert entry["internalName"] == "Volatility2"

    def test_unique_name_resolves_without_stat_set(self):
        index = self._index(IONIC_SPARK_ENTRY, VOLATILITY_ENTRY)
        assert index.resolve("Ionic Spark", [])["internalName"] == "Volatility"

    def test_unknown_name_raises(self):
        index = self._index(VOLATILITY_ENTRY)
        with pytest.raises(ValueError, match="No Wynncraft item"):
            index.resolve("Nonexistent", [])

    def test_ambiguous_name_raises(self):
        twin = {**VOLATILITY_ENTRY, "internalName": "Volatility3"}
        index = self._index(VOLATILITY_ENTRY, twin)
        decoded = decode_gear(VOLATILITY_CODE)
        with pytest.raises(ValueError, match="Ambiguous"):
            index.resolve(decoded["itemName"], decoded["identifications"])

    def test_rejects_empty_database(self):
        with pytest.raises(ValueError):
            WynnItemIndex().load([])


class TestItemFromGear:
    def test_builds_stats_and_inverts_spell_cost_sign(self):
        item = item_from_gear(decode_gear(VOLATILITY_CODE), VOLATILITY_ENTRY, [])
        assert item["itemName"] == "Volatility"
        assert item["internalName"] == "Volatility"
        assert item["tier"] == "fabled"
        assert item["reroll"] == 2
        assert item["stats"]["2nd Spell Cost %"] == -25
        assert item["stats"]["3rd Spell Cost %"] == -19
        assert item["stats"]["Life Steal"] == -356
        assert round(item["rate"]["2nd Spell Cost %"], 2) == 95.0
        assert round(item["rate"]["Max Mana"], 2) == 62.5
        assert item["stats"]["Damage %"] == 44
        assert item["rate"]["Damage %"] == 100

    def test_renders_png(self):
        item = item_from_gear(decode_gear(VOLATILITY_CODE), VOLATILITY_ENTRY, [])
        assert build_lines(item)[0].text.startswith("Volatility")
        assert render_item_tooltip(item)[:8] == b"\x89PNG\r\n\x1a\n"

    def test_fully_static_item_renders_from_database_identifications(self):
        decoded = decode_gear(PROWESS_CODE)
        item = item_from_gear(decoded, PROWESS_ENTRY, [])

        assert decoded["identifications"] == []
        assert [(stat["key"], stat["value"]) for stat in item["renderStats"]] == [
            ("rawAgility", 4),
            ("rawDefence", 4),
            ("rawStrength", 4),
            ("rawDexterity", 4),
            ("rawIntelligence", 4),
        ]
        assert render_item_tooltip(item)[:8] == b"\x89PNG\r\n\x1a\n"

    def test_missing_range_raises(self):
        with pytest.raises(ValueError, match="Missing roll range"):
            item_from_gear(decode_gear(VOLATILITY_CODE), IONIC_SPARK_ENTRY, [])

    def test_weights_for_another_item_rejected(self):
        weights = [{"item_id": "Ionic Spark", "weight_name": "Main", "identifications": {"damage": 1}}]
        with pytest.raises(ValueError, match="belonging to another item"):
            item_from_gear(decode_gear(VOLATILITY_CODE), VOLATILITY_ENTRY, weights)

    def test_skill_stat_icon_only_prefixes_negative_values(self):
        item = {
            "tier": "rare",
            "renderStats": [
                {"key": "rawDexterity", "label": "Dexterity", "value": 5, "rate": None},
                {"key": "rawStrength", "label": "Strength", "value": -5, "rate": None},
            ],
        }

        negative, positive = _stats(item, {}, False)

        assert positive.draw is None
        assert positive.final_draw is None
        assert positive.left[0].value == "Dexterity"
        assert negative.draw is not None
        assert negative.final_draw is not None
        assert negative.right[0].value == "-5"


class TestRenderNameHint:
    def _stub_bridge(self, monkeypatch):
        bridge = ItemTooltipBridge()
        bridge._items.load([VOLATILITY_ENTRY, WILD_GROWTH_ENTRY, PROWESS_ENTRY])

        async def fake_json_request(method, url, payload=None, **kwargs):
            raise AssertionError("gear items must resolve from the cached item index")

        async def fake_weights(name):
            return []

        monkeypatch.setattr(bridge, "_json_request", fake_json_request)
        monkeypatch.setattr(bridge, "_get_weights", fake_weights)
        return bridge

    @pytest.mark.asyncio
    async def test_name_hint_overrides_decoded_name(self, monkeypatch):
        bridge = self._stub_bridge(monkeypatch)
        name, png = await bridge._render(VOLATILITY_CODE, "Volatility qol")
        assert name == "Volatility qol"
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    @pytest.mark.asyncio
    async def test_no_hint_keeps_decoded_name(self, monkeypatch):
        bridge = self._stub_bridge(monkeypatch)
        name, _ = await bridge._render(VOLATILITY_CODE, None)
        assert name == "Volatility"

    @pytest.mark.asyncio
    async def test_gear_render_does_not_call_wynnpool_decode(self, monkeypatch):
        bridge = self._stub_bridge(monkeypatch)
        name, _ = await bridge._render(WILD_GROWTH_CODE, None)
        assert name == "Wild Growth"

    @pytest.mark.asyncio
    async def test_fully_static_gear_renders_through_bridge(self, monkeypatch):
        bridge = self._stub_bridge(monkeypatch)
        name, png = await bridge._render(PROWESS_CODE, None)

        assert name == "Prowess"
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    @pytest.mark.asyncio
    async def test_crafted_item_does_not_call_wynnpool(self, monkeypatch):
        bridge = ItemTooltipBridge()

        async def unexpected_request(*args, **kwargs):
            raise AssertionError("crafted items must render locally")

        monkeypatch.setattr(bridge, "_json_request", unexpected_request)
        name, png = await bridge._render(CRAFTED_RING, "Embodiment of Scam")
        assert name == "Embodiment of Scam"
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
