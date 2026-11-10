from unittest.mock import Mock

import pytest
from PIL import Image

from Helpers import card_render


@pytest.fixture
def renderer(monkeypatch, tmp_path):
    card_render._cached_card_png.cache_clear()
    monkeypatch.setattr(card_render, "PORTRAIT_DIR", str(tmp_path))
    monkeypatch.setattr(card_render, "ART_CACHE", str(tmp_path))
    render = Mock(side_effect=lambda *a, **k: Image.new("RGB", (4, 4), "red"))
    monkeypatch.setattr(card_render, "render_card", render)
    yield render, tmp_path
    card_render._cached_card_png.cache_clear()


def test_cached_png_has_independent_upload_streams(renderer):
    render, _ = renderer
    card = {"slug": "test", "name": "Test", "tier": "rare"}
    first = card_render.card_file(card)
    expected = first.fp.read()
    first.close()
    second = card_render.card_file(card)
    assert second.fp.read() == expected
    assert render.call_count == 1
    second.close()


def test_cache_separates_card_name_stars_rank_and_ceiling(renderer):
    render, _ = renderer
    card = {"slug": "test", "name": "Test", "tier": "rare"}
    for changed, kwargs in (
        (card, {}),
        (dict(card, name="Renamed"), {}),
        (card, {"stars": 1}),
        (card, {"badge": "Hydra"}),
        (card, {"max_stars": 2}),
    ):
        card_render.card_file(changed, **kwargs).close()
    assert render.call_count == 5


def test_replacing_local_art_invalidates_the_cached_image(renderer):
    render, folder = renderer
    card = {"slug": "test", "name": "Test", "tier": "rare"}
    art = folder / "test.png"
    art.write_bytes(b"first")
    card_render.card_file(card).close()
    card_render.card_file(card).close()
    art.write_bytes(b"replacement")
    card_render.card_file(card).close()
    assert render.call_count == 2


def test_missing_remote_art_is_retried_instead_of_caching_the_fallback(renderer):
    render, _ = renderer
    card = {"slug": "test", "name": "Test", "tier": "rare",
            "image_url": "https://example.invalid/art.png"}
    card_render.card_file(card).close()
    card_render.card_file(card).close()
    assert render.call_count == 2
