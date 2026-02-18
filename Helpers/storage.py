"""
Helpers/storage.py
Cloudflare R2 storage abstraction for profile backgrounds and avatar caching.
"""

import io
import os
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from PIL import Image

AVATAR_TTL_SECONDS = 3 * 24 * 60 * 60  # 3 days


class R2Storage:
    """S3-compatible storage client for Cloudflare R2."""

    def __init__(self):
        self._client = None
        test_mode = os.getenv("TEST_MODE", "").lower() in ("true", "1", "t")
        if test_mode:
            self._bucket = os.getenv("TEST_R2_BUCKET_NAME", "tort-reborn-dev")
        else:
            self._bucket = os.getenv("R2_BUCKET_NAME", "tort-reborn-prod")

    @property
    def client(self):
        if self._client is None:
            self._client = boto3.client(
                "s3",
                endpoint_url=os.getenv("R2_ENDPOINT_URL"),
                aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID"),
                aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY"),
                config=Config(signature_version="s3v4"),
                region_name="auto",
            )
        return self._client

    def get_bytes(self, key: str) -> bytes | None:
        try:
            resp = self.client.get_object(Bucket=self._bucket, Key=key)
            return resp["Body"].read()
        except ClientError:
            return None

    def get_image(self, key: str) -> Image.Image | None:
        data = self.get_bytes(key)
        if data:
            return Image.open(io.BytesIO(data)).convert("RGBA")
        return None

    def get_bytes_if_fresh(self, key: str, max_age_seconds: int) -> bytes | None:
        """Return object bytes only if younger than max_age_seconds, else None."""
        try:
            head = self.client.head_object(Bucket=self._bucket, Key=key)
            age = (datetime.now(timezone.utc) - head["LastModified"]).total_seconds()
            if age > max_age_seconds:
                return None
            return self.get_bytes(key)
        except ClientError:
            return None

    def put_bytes(self, key: str, data: bytes, content_type: str = "image/png"):
        self.client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

    def put_image(self, key: str, image: Image.Image):
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        buf.seek(0)
        self.put_bytes(key, buf.getvalue())


# Singleton
r2 = R2Storage()


# --- Profile background helpers ---

def get_background(bg_id) -> Image.Image:
    """Download a profile background from R2. Falls back to bg 1, then solid color."""
    img = r2.get_image(f"profile_backgrounds/{bg_id}.png")
    if img:
        return img

    if str(bg_id) != "1":
        img = r2.get_image("profile_backgrounds/1.png")
        if img:
            return img

    return Image.new("RGBA", (800, 526), (30, 30, 50, 255))


def get_background_file(bg_id):
    """Download a profile background and return as a discord.File."""
    import discord

    data = r2.get_bytes(f"profile_backgrounds/{bg_id}.png")
    if data:
        return discord.File(io.BytesIO(data), filename=f"{bg_id}.png")
    return None


def save_background(bg_id, image: Image.Image):
    """Upload a profile background to R2."""
    r2.put_image(f"profile_backgrounds/{bg_id}.png", image)


# --- Avatar cache helpers ---

def get_cached_avatar(uuid: str) -> bytes | None:
    """Download a cached avatar from R2 if it's less than 3 days old."""
    return r2.get_bytes_if_fresh(f"avatars/{uuid}.png", AVATAR_TTL_SECONDS)


def save_cached_avatar(uuid: str, data: bytes):
    """Upload an avatar to R2 cache."""
    r2.put_bytes(f"avatars/{uuid}.png", data)
