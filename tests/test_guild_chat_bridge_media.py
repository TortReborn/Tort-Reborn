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
    _discord_safe_text,
    _embed_has_preview,
    _fallback_message,
    _reply_excerpt,
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


def embed(url, *, title="", description="", provider="", embed_type="rich", preview="", image_url="",
          video_url="", video_preview=""):
    image = SimpleNamespace(url=image_url, proxy_url=preview) if preview or image_url else None
    return SimpleNamespace(
        url=url,
        title=title,
        description=description,
        provider=SimpleNamespace(name=provider) if provider else None,
        type=embed_type,
        image=image,
        thumbnail=None,
        video=SimpleNamespace(url=video_url, proxy_url=video_preview) if video_url or video_preview else None,
    )


def sticker(name, sticker_format, sticker_id=1):
    base = "https://media.discordapp.net" if sticker_format is bridge_module.discord.StickerFormatType.gif \
        else "https://cdn.discordapp.com"
    return SimpleNamespace(
        name=name,
        format=sticker_format,
        url=f"{base}/stickers/{sticker_id}.{sticker_format.file_extension}",
    )


def test_classifies_discord_attachments_with_mp4_preview():
    media = _bridge_media((
        attachment("map.png", "image/png", description="Territory map"),
        attachment("cast.gif", "image/gif"),
        attachment("war.mp4", "video/mp4"),
    ), ())

    assert [item.kind for item in media] == ["image", "gif", "video"]
    assert media[0].preview_url.startswith("https://media.discordapp.net/")
    assert media[1].preview_url.startswith("https://media.discordapp.net/")
    assert media[2].preview_url == "https://media.discordapp.net/attachments/1/2/war.mp4"
    assert all(not item.inline for item in media)


def test_video_preview_respects_spoilers_and_supported_container():
    media = _bridge_media((
        attachment("SPOILER_war.mp4", "video/mp4", spoiler=True),
        attachment("war.webm", "video/webm"),
        attachment("war.m4v", "application/octet-stream"),
    ), ())

    assert media[0].preview_url == ""
    assert media[1].preview_url == ""
    assert media[2].preview_url.endswith("war.m4v")


def test_gif_embed_prefers_mp4_proxy_to_static_thumbnail():
    proxy = "https://images-ext-1.discordapp.net/external/example/https/cdn.klipy.com/clip.mp4"
    item = embed(
        "https://klipy.com/gifs/skeleton-skeleton-run",
        provider="Klipy",
        embed_type="gifv",
        preview="https://images-ext-1.discordapp.net/external/thumbnail.png",
        video_url="https://cdn.klipy.com/clip.mp4",
        video_preview=proxy,
    )
    media = _bridge_media((), (item,))

    assert media[0].kind == "gif"
    assert media[0].preview_url == proxy
    assert media[0].url == item.url
    assert media[0].inline is True


def test_video_only_embed_has_preview_and_video_kind():
    proxy = "https://images-ext-1.discordapp.net/external/example/clip.mp4?width=240"
    item = embed("https://example.com/clip", video_preview=proxy)

    assert _embed_has_preview((item,)) is True
    media = _bridge_media((), (item,))
    assert media[0].kind == "video"
    assert media[0].preview_url == proxy


def test_discord_gif_embed_preserves_signed_video_proxy():
    proxy = "https://images-ext-1.discordapp.net/external/opaque?format=mp4&width=240"
    item = bridge_module.discord.Embed.from_dict({
        "type": "gifv",
        "url": "https://klipy.com/gifs/skeleton-skeleton-run",
        "provider": {"name": "Klipy"},
        "thumbnail": {"proxy_url": "https://images-ext-1.discordapp.net/external/thumbnail.png"},
        "video": {"url": "https://cdn.klipy.com/clip.mp4?signature=example", "proxy_url": proxy},
    })

    media = _bridge_media((), (item,))
    assert media[0].kind == "gif"
    assert media[0].preview_url == proxy
    assert _embed_has_preview((item,)) is True


@pytest.mark.parametrize("video_preview", ["", "https://untrusted.example/clip.mp4",
                                           "https://images-ext-1.discordapp.net/external/clip.webm"])
def test_unsupported_or_unproxied_video_keeps_image_preview(video_preview):
    thumbnail = "https://images-ext-1.discordapp.net/external/thumbnail.png"
    item = embed("https://example.com/clip", preview=thumbnail,
                 video_url="https://untrusted.example/clip.webm", video_preview=video_preview)

    media = _bridge_media((), (item,))
    assert media[0].kind == "link"
    assert media[0].preview_url == thumbnail


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


def test_classifies_attachments_by_file_ending_without_content_type():
    media = _bridge_media((
        attachment("scan.tiff", None),
        attachment("shot.webp", None),
        attachment("clip.mov", None),
    ), ())

    assert [item.kind for item in media] == ["image", "image", "video"]


def test_build_link_reaches_discord_as_a_masked_link():
    build_link = ("https://wynnbuilder.github.io/builder/"
                  "#CY0o2tuHWeYeIfHfHDYfHWY4iH-8e6971DnY1ZX8vGH2seF+QseV44XOcaMsKV3j0")

    assert _discord_safe_text(f"look at {build_link} pls") == (
        f"look at [build link](<{build_link}>) pls"
    )
    assert _discord_safe_text(build_link + ".") == f"[build link](<{build_link}>)."


def test_plain_links_stay_unescaped_and_unlabelled():
    link = "https://example.com/a_path_with_underscores"

    assert _discord_safe_text(f"see {link} now") == f"see {link} now"


def bridged_message(content):
    return SimpleNamespace(
        content=content,
        webhook_id=123,
        mentions=(),
        role_mentions=(),
        channel_mentions=(),
        attachments=(),
        embeds=(),
        stickers=(),
    )


def test_reply_excerpt_unwraps_our_own_masked_build_link():
    build_link = ("https://wynnbuilder.github.io/builder/"
                  "#CY0o2tuHWeYeIfHfHDYfHWY4iH-8e6971DnY1ZX8vGH2seF+QseV44XOcaMsKV3j0")

    assert _reply_excerpt(bridged_message(f"look at [build link](<{build_link}>) pls")) == (
        "look at build link pls"
    )

@pytest.mark.asyncio
@pytest.mark.parametrize("ign,label", [("Woealer", "Guild Storage"), ("GordLonner", "Guild Bank")])
async def test_service_account_poster_needs_no_discord_member(monkeypatch, ign, label):
    poster = bridge_module.BridgePoster(f"{label} ({ign})", "https://mc-heads.net/avatar/test/64")
    monkeypatch.setattr(bridge_module, "_service_poster", lambda name: poster if name == ign else None)
    monkeypatch.setattr(bridge_module, "_linked_discord_id", lambda name: pytest.fail("no Discord identity should be invented"))
    bridge = object.__new__(GuildChatBridge)
    assert await bridge._resolve_poster(SimpleNamespace(), ign) == poster


@pytest.mark.asyncio
async def test_normal_linked_poster_keeps_discord_name_and_avatar(monkeypatch):
    monkeypatch.setattr(bridge_module, "_service_poster", lambda name: None)
    monkeypatch.setattr(bridge_module, "_linked_discord_id", lambda name: 123)
    member = SimpleNamespace(display_name="Starfish Player", display_avatar=SimpleNamespace(url="avatar"))
    guild = SimpleNamespace(get_member=lambda discord_id: member if discord_id == 123 else None)
    bridge = object.__new__(GuildChatBridge)
    assert await bridge._resolve_poster(guild, "Player") == bridge_module.BridgePoster("Starfish Player", "avatar")


@pytest.mark.asyncio
async def test_unassigned_unlinked_poster_is_still_rejected(monkeypatch):
    monkeypatch.setattr(bridge_module, "_service_poster", lambda name: None)
    monkeypatch.setattr(bridge_module, "_linked_discord_id", lambda name: None)
    bridge = object.__new__(GuildChatBridge)
    assert await bridge._resolve_poster(SimpleNamespace(), "Woealer") is None


@pytest.mark.parametrize("row", [None, ("test-uuid", "Woealer", "Guild Storage"), ("test-uuid", "GordLonner", "Guild Bank")])
def test_service_poster_uses_pinned_uuid_and_current_roster(monkeypatch, row):
    from unittest.mock import MagicMock

    database = MagicMock()
    database.__enter__.return_value = database
    database.cursor.fetchone.return_value = row
    monkeypatch.setattr(bridge_module, "DB", lambda: database)
    result = bridge_module._service_poster("Woealer")
    sql, parameters = database.cursor.execute.call_args.args
    assert "storage.uuid = gr.uuid" in sql and "bank.uuid = gr.uuid" in sql
    assert "LOWER(gr.ign) = LOWER(%s)" in sql
    assert parameters == ("Woealer",)
    if row is None:
        assert result is None
    else:
        assert result == bridge_module.BridgePoster(f"{row[2]} ({row[1]})", "https://mc-heads.net/avatar/test-uuid/64")


@pytest.mark.asyncio
async def test_service_assignment_lookup_error_does_not_fall_back_to_username(monkeypatch):
    def unavailable(ign):
        raise RuntimeError("database offline")

    monkeypatch.setattr(bridge_module, "_service_poster", unavailable)
    monkeypatch.setattr(bridge_module, "_linked_discord_id", lambda name: pytest.fail("must fail closed"))
    with pytest.raises(RuntimeError, match="offline"):
        await object.__new__(GuildChatBridge)._resolve_poster(SimpleNamespace(), "Woealer")
