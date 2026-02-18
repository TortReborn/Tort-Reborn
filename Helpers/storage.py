"""
Helpers/storage.py
S3-compatible storage abstraction for profile backgrounds and avatar caching.
Currently backed by Supabase Storage (S3-compatible API).
"""

import io
import os
from datetime import datetime, timezone

import certifi
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from PIL import Image

from Helpers.logger import log, WARN, ERROR

AVATAR_TTL_SECONDS = 3 * 24 * 60 * 60  # 3 days


class S3Storage:
    """S3-compatible storage client for Supabase Storage."""

    def __init__(self):
        self._client = None
        test_mode = os.getenv("TEST_MODE", "").lower() in ("true", "1", "t")
        if test_mode:
            self._bucket = os.getenv("TEST_S3_BUCKET_NAME", "Tort-Reborn-Dev")
        else:
            self._bucket = os.getenv("S3_BUCKET_NAME", "Tort-Reborn-Prod")

    @property
    def client(self):
        if self._client is None:
            self._client = boto3.client(
                "s3",
                endpoint_url=os.getenv("S3_ENDPOINT_URL"),
                aws_access_key_id=os.getenv("S3_ACCESS_KEY_ID"),
                aws_secret_access_key=os.getenv("S3_SECRET_ACCESS_KEY"),
                config=Config(signature_version="s3v4"),
                region_name=os.getenv("S3_REGION", "us-east-1"),
                verify=certifi.where(),
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
        """Return object bytes only if younger than max_age_seconds."""
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
storage = S3Storage()


# --- Profile background helpers ---

def get_background(bg_id) -> Image.Image:
    """Download a profile background from S3."""
    img = storage.get_image(f"profile_backgrounds/{bg_id}.png")
    if img:
        return img
    raise FileNotFoundError(f"Background {bg_id} not found in S3")


def get_background_file(bg_id):
    """Download a profile background and return as a discord.File."""
    import discord

    data = storage.get_bytes(f"profile_backgrounds/{bg_id}.png")
    if data:
        return discord.File(io.BytesIO(data), filename=f"{bg_id}.png")
    raise FileNotFoundError(f"Background {bg_id} not found in S3")


def save_background(bg_id, image: Image.Image):
    """Upload a profile background to S3."""
    storage.put_image(f"profile_backgrounds/{bg_id}.png", image)


# --- Avatar cache helpers ---

def get_cached_avatar(uuid: str) -> bytes | None:
    """Download a cached avatar if it's less than 3 days old."""
    return storage.get_bytes_if_fresh(f"avatars/{uuid}.png", AVATAR_TTL_SECONDS)


def save_cached_avatar(uuid: str, data: bytes):
    """Upload an avatar to the cache."""
    storage.put_bytes(f"avatars/{uuid}.png", data)
