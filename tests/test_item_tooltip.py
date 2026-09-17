import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Helpers.item_tooltip import ItemTooltipBridge, find_item_codes
from Helpers.item_tooltip_render import build_lines, item_from_api, render_item_tooltip


def _item_code(tag: int) -> str:
    return "\U000F0000\U000F0100" + chr(0xF0010 + tag)


ITEM_SHARE_WITH_NAME = f'{_item_code(1)} "Divzer qol"'


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


class TestRenderNameHint:
    def _stub_bridge(self, monkeypatch, decoded_name="Recipe Placeholder"):
        bridge = ItemTooltipBridge()

        async def fake_json_request(method, url, payload=None):
            if "full-decode" in url:
                return {
                    "original": {
                        "id": decoded_name,
                        "displayName": decoded_name,
                        "tier": "legendary",
                        "identifications": {"rawHealth": {"min": 0, "raw": 100, "max": 200}},
                    },
                    "input": {"identifications": {"rawHealth": 100}, "rerollCount": 0},
                }
            return [{"item_id": decoded_name, "weight_name": "Main", "identifications": {"rawHealth": 1}}]

        monkeypatch.setattr(bridge, "_json_request", fake_json_request)
        return bridge

    @pytest.mark.asyncio
    async def test_name_hint_overrides_decoded_name(self, monkeypatch):
        bridge = self._stub_bridge(monkeypatch)
        name, png = await bridge._render(_item_code(1), "Divzer qol")
        assert name == "Divzer qol"
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    @pytest.mark.asyncio
    async def test_no_hint_keeps_decoded_name(self, monkeypatch):
        bridge = self._stub_bridge(monkeypatch)
        name, _ = await bridge._render(_item_code(1), None)
        assert name == "Recipe Placeholder"
