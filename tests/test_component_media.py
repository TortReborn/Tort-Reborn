import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Helpers.component_media import rebind_attachment_urls

INGS = "ingredient_shell_panel.png"
MATS = "materials_shell_panel.png"


def _cdn(filename):
    return (
        f"https://cdn.discordapp.com/attachments/752917987853467669/1135537781574205520/{filename}"
        "?ex=68c5a1b2&is=68c45032&hm=deadbeef&"
    )


def _gallery(filename):
    return {
        "type": 12,
        "items": [
            {
                "media": {
                    "url": _cdn(filename),
                    "proxy_url": f"https://media.discordapp.net/attachments/1/2/{filename}",
                    "width": 1208,
                    "height": 376,
                    "content_type": "image/png",
                }
            }
        ],
    }


def test_gallery_urls_are_rebound():
    components = [{"type": 17, "components": [_gallery(INGS), _gallery(MATS)]}]

    unreferenced = rebind_attachment_urls(components, [INGS, MATS])

    galleries = components[0]["components"]
    assert galleries[0]["items"][0]["media"] == {"url": f"attachment://{INGS}"}
    assert galleries[1]["items"][0]["media"] == {"url": f"attachment://{MATS}"}
    assert unreferenced == []


def test_unfurl_metadata_is_dropped():
    components = [_gallery(INGS)]

    rebind_attachment_urls(components, [INGS])

    assert components[0]["items"][0]["media"] == {"url": f"attachment://{INGS}"}


def test_section_accessory_and_file_components_are_reached():
    components = [
        {
            "type": 9,
            "components": [{"type": 10, "content": "text"}],
            "accessory": {"type": 11, "media": {"url": _cdn(INGS)}},
        },
        {"type": 13, "file": {"url": _cdn(MATS)}},
    ]

    unreferenced = rebind_attachment_urls(components, [INGS, MATS])

    assert components[0]["accessory"]["media"]["url"] == f"attachment://{INGS}"
    assert components[1]["file"]["url"] == f"attachment://{MATS}"
    assert unreferenced == []


def test_already_rebound_urls_stay_put():
    components = [_gallery(INGS)]
    components[0]["items"][0]["media"] = {"url": f"attachment://{INGS}"}

    unreferenced = rebind_attachment_urls(components, [INGS])

    assert components[0]["items"][0]["media"] == {"url": f"attachment://{INGS}"}
    assert unreferenced == []


def test_foreign_media_is_left_alone():
    banner = {"type": 12, "items": [{"media": {"url": "https://example.com/banner.png"}}]}
    components = [banner, _gallery(INGS)]

    unreferenced = rebind_attachment_urls(components, [INGS, MATS])

    assert banner["items"][0]["media"]["url"] == "https://example.com/banner.png"
    assert unreferenced == [MATS]


def test_missing_slot_is_reported():
    components = [{"type": 10, "content": "just text"}]

    assert rebind_attachment_urls(components, [INGS, MATS]) == [INGS, MATS]
