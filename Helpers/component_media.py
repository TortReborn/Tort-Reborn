"""Rebind Components V2 media slots to the files uploaded alongside an edit.

Reading a message resolves attachment:// into a CDN URL for an attachment the
next edit drops, so slots must be rewritten or the images 404 with no API error.
"""

IS_COMPONENTS_V2 = 1 << 15


def _basename(url: str) -> str:
    return url.split("?", 1)[0].rsplit("/", 1)[-1]


def _iter_media(components):
    for component in components or ():
        if not isinstance(component, dict):
            continue

        for key in ("media", "file"):
            slot = component.get(key)
            if isinstance(slot, dict):
                yield slot

        for item in component.get("items") or ():
            if isinstance(item, dict) and isinstance(item.get("media"), dict):
                yield item["media"]

        accessory = component.get("accessory")
        if isinstance(accessory, dict):
            yield from _iter_media([accessory])

        yield from _iter_media(component.get("components"))


def rebind_attachment_urls(components, filenames) -> list[str]:
    """Rewrite matching media slots in place. Returns filenames no slot uses."""
    wanted = set(filenames)
    referenced = set()

    for media in _iter_media(components):
        name = _basename(media.get("url") or "")
        if name not in wanted:
            continue
        media.clear()
        media["url"] = f"attachment://{name}"
        referenced.add(name)

    return [name for name in filenames if name not in referenced]
