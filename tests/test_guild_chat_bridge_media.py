from types import SimpleNamespace

from Tasks.guild_chat_bridge import (
    BridgeMedia,
    BridgeReply,
    DiscordBridgeMessage,
    LinkedBridgeMember,
    _bridge_media,
    _fallback_message,
)


def attachment(filename, content_type, *, spoiler=False, description=None):
    return SimpleNamespace(
        filename=filename,
        content_type=content_type,
        url=f"https://cdn.discordapp.com/attachments/1/2/{filename}",
        proxy_url=f"https://media.discordapp.net/attachments/1/2/{filename}",
        description=description,
        is_spoiler=lambda: spoiler,
    )


def embed(url, *, title="", description="", provider="", embed_type="rich", preview=""):
    image = SimpleNamespace(proxy_url=preview) if preview else None
    return SimpleNamespace(
        url=url,
        title=title,
        description=description,
        provider=SimpleNamespace(name=provider) if provider else None,
        type=embed_type,
        image=image,
        thumbnail=None,
    )


def test_classifies_discord_attachments_without_inlining_video():
    media = _bridge_media((
        attachment("map.png", "image/png", description="Territory map"),
        attachment("cast.gif", "image/gif"),
        attachment("war.mp4", "video/mp4"),
    ), ())

    assert [item.kind for item in media] == ["image", "gif", "video"]
    assert media[0].preview_url.startswith("https://media.discordapp.net/")
    assert media[1].preview_url.startswith("https://media.discordapp.net/")
    assert media[2].preview_url == ""
    assert all(not item.inline for item in media)


def test_spoiler_image_has_no_preview_and_link_embed_is_inline():
    media = _bridge_media(
        (attachment("SPOILER_plan.png", "image/png", spoiler=True),),
        (embed(
            "https://example.com/war-plan",
            title="War plan",
            description="Route and assignments",
            provider="Example",
            preview="https://images-ext-1.discordapp.net/external/preview.png",
        ),),
    )

    assert media[0].spoiler is True
    assert media[0].preview_url == ""
    assert media[1].kind == "link"
    assert media[1].inline is True
    assert media[1].preview_url.startswith("https://images-ext-1.discordapp.net/")


def test_fallback_remains_complete_for_old_clients():
    reply = BridgeReply("TargetIgn", "earlier message")
    media = (
        BridgeMedia("image", "https://cdn.discordapp.com/image.png", "map.png"),
        BridgeMedia("video", "https://cdn.discordapp.com/video.mp4", "video.mp4"),
    )

    assert _fallback_message("meet there", reply, media) == (
        "replied to TargetIgn: [image] [sent a video] meet there"
    )

    prepared = DiscordBridgeMessage(
        "replied to TargetIgn: [image] [sent a video] meet there",
        "meet there",
        reply,
        media,
    )
    payload = prepared.payload(LinkedBridgeMember(42, "SenderIgn", 0x123456), 99)
    assert payload["message"] == "replied to TargetIgn: [image] [sent a video] meet there"
    assert payload["content"] == "meet there"
    assert payload["reply"] == {"username": "TargetIgn", "excerpt": "earlier message"}
    assert payload["media"][1]["kind"] == "video"
    assert "previewUrl" not in payload["media"][1]
