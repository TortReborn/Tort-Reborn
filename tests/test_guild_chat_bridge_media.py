from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import Tasks.guild_chat_bridge as bridge_module

from Tasks.guild_chat_bridge import (
    BridgeMedia,
    BridgeReply,
    DiscordBridgeMessage,
    GuildChatBridge,
    LinkedBridgeMember,
    _bridge_media,
    _embed_has_preview,
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


def embed(url, *, title="", description="", provider="", embed_type="rich", preview="", image_url=""):
    image = SimpleNamespace(url=image_url, proxy_url=preview) if preview or image_url else None
    return SimpleNamespace(
        url=url,
        title=title,
        description=description,
        provider=SimpleNamespace(name=provider) if provider else None,
        type=embed_type,
        image=image,
        thumbnail=None,
    )


def sticker(name, sticker_format, sticker_id=1):
    base = "https://media.discordapp.net" if sticker_format is bridge_module.discord.StickerFormatType.gif \
        else "https://cdn.discordapp.com"
    return SimpleNamespace(
        name=name,
        format=sticker_format,
        url=f"{base}/stickers/{sticker_id}.{sticker_format.file_extension}",
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


def test_classifies_discord_stickers_without_changing_media_contract():
    formats = bridge_module.discord.StickerFormatType
    media = _bridge_media((), (), (
        sticker("Static", formats.png, 1),
        sticker("Animated PNG", formats.apng, 2),
        sticker("Animated GIF", formats.gif, 3),
    ))

    assert [item.kind for item in media] == ["image", "image", "gif"]
    assert all(item.provider == "Discord Sticker" for item in media)
    assert all(item.preview_url == item.url for item in media)

    lottie = _bridge_media((), (), (sticker("Lottie", formats.lottie, 4),))
    assert lottie[0].kind == "link"
    assert lottie[0].preview_url == ""


@pytest.mark.asyncio
async def test_prepare_accepts_sticker_only_message():
    formats = bridge_module.discord.StickerFormatType
    message = SimpleNamespace(
        id=42,
        content="",
        attachments=(),
        embeds=(),
        stickers=(sticker("Wave", formats.gif),),
        mentions=(),
        role_mentions=(),
        channel_mentions=(),
        reference=None,
    )
    bridge = object.__new__(GuildChatBridge)
    bridge._reply_context = AsyncMock(return_value=None)

    prepared = await bridge._prepare_discord_message(message)

    assert prepared.message == "[sticker]"
    assert prepared.content == ""
    assert prepared.media[0].kind == "gif"


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


def test_pasted_gif_preserves_signed_preview_url():
    preview = (
        "https://media.discordapp.net/attachments/1/2/togif.gif"
        "?ex=abc&is=def&hm=123&=&width=288&height=320"
    )
    proxy = "https://cdn.discordapp.com/attachments/1/2/togif.gif"
    media = _bridge_media((), (embed(
        "https://cdn.discordapp.com/attachments/1/2/togif.gif",
        image_url=preview,
        preview=proxy,
    ),))

    assert media[0].kind == "gif"
    assert media[0].preview_url == preview
    assert media[0].inline is True
    assert _embed_has_preview((embed("https://example.com/gif", image_url=preview),)) is True


def test_embed_preview_uses_discord_proxy_for_external_image_url():
    proxy = "https://images-ext-1.discordapp.net/external/preview.gif"
    media = _bridge_media((), (embed(
        "https://example.com/gif",
        image_url="https://media.example.com/preview.gif",
        preview=proxy,
    ),))

    assert media[0].preview_url == proxy


@pytest.mark.asyncio
async def test_prepare_waits_for_signed_embed_preview(monkeypatch):
    source = "https://cdn.discordapp.com/attachments/1/2/togif.gif"
    preview = "https://media.discordapp.net/attachments/1/2/togif.gif?ex=abc&is=def&hm=123"
    message = SimpleNamespace(
        id=42,
        content=source,
        attachments=(),
        embeds=(),
        stickers=(),
        mentions=(),
        role_mentions=(),
        channel_mentions=(),
        reference=None,
    )
    message.channel = SimpleNamespace(fetch_message=AsyncMock(side_effect=(
        SimpleNamespace(embeds=()),
        SimpleNamespace(embeds=()),
        SimpleNamespace(embeds=(embed(source, preview=preview),)),
    )))
    bridge = object.__new__(GuildChatBridge)
    bridge._reply_context = AsyncMock(return_value=None)
    monkeypatch.setattr(bridge_module.asyncio, "sleep", AsyncMock())

    prepared = await bridge._prepare_discord_message(message)

    assert message.channel.fetch_message.call_count == 3
    assert prepared.media[0].preview_url == preview


def test_fallback_remains_complete_for_old_clients():
    reply = BridgeReply("TargetIgn", "earlier message")
    media = (
        BridgeMedia("image", "https://cdn.discordapp.com/image.png", "map.png"),
        BridgeMedia("video", "https://cdn.discordapp.com/video.mp4", "video.mp4"),
        BridgeMedia("gif", "https://media.discordapp.net/stickers/1.gif", "Wave",
                    provider="Discord Sticker"),
    )

    assert _fallback_message("meet there", reply, media) == (
        "replied to TargetIgn: [image] [sent a video] [sticker] meet there"
    )

    prepared = DiscordBridgeMessage(
        "replied to TargetIgn: [image] [sent a video] [sticker] meet there",
        "meet there",
        reply,
        media,
    )
    payload = prepared.payload(LinkedBridgeMember(42, "SenderIgn", 0x123456), 99)
    assert payload["message"] == "replied to TargetIgn: [image] [sent a video] [sticker] meet there"
    assert payload["content"] == "meet there"
    assert payload["reply"] == {"username": "TargetIgn", "excerpt": "earlier message"}
    assert payload["media"][1]["kind"] == "video"
    assert "previewUrl" not in payload["media"][1]
