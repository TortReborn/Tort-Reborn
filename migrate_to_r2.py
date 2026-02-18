"""
One-time migration script: Upload existing profile backgrounds and cached avatars to R2.

Uses the Cloudflare Wrangler CLI (avoids S3 API TLS issues on Windows).

Usage:
    1. Authenticate: npx wrangler login
    2. Run: python migrate_to_r2.py
    3. Delete this script after migration is complete.
"""

import os
import subprocess

from dotenv import load_dotenv

load_dotenv()

test_mode = os.getenv("TEST_MODE", "").lower() in ("true", "1", "t")
if test_mode:
    bucket = os.getenv("TEST_R2_BUCKET_NAME", "tort-reborn-dev")
else:
    bucket = os.getenv("R2_BUCKET_NAME", "tort-reborn-prod")


def upload_file(local_path, r2_key):
    """Upload a single file to R2 using wrangler CLI."""
    object_path = f"{bucket}/{r2_key}"
    result = subprocess.run(
        ["npx", "wrangler", "r2", "object", "put", object_path,
         "--file", local_path, "--content-type", "image/png", "--remote"],
        capture_output=True, encoding="utf-8", errors="replace"
    )
    if result.returncode != 0:
        print(f"    ERROR: {result.stderr.strip()}")
        return False
    return True


def upload_dir(local_dir, r2_prefix):
    if not os.path.isdir(local_dir):
        print(f"  Skipping {local_dir} (directory not found)")
        return 0

    count = 0
    for filename in os.listdir(local_dir):
        filepath = os.path.join(local_dir, filename)
        if not os.path.isfile(filepath):
            continue

        key = f"{r2_prefix}/{filename}"
        print(f"  Uploading {filepath} -> {key}")
        if upload_file(filepath, key):
            count += 1

    return count


print(f"Migrating to R2 bucket: {bucket}")
print()

print("=== Profile Backgrounds ===")
bg_count = upload_dir("images/profile_backgrounds", "profile_backgrounds")
print(f"  Uploaded {bg_count} background(s)")
print()

print("=== Cached Avatars ===")
av_count = upload_dir("cache/avatars", "avatars")
print(f"  Uploaded {av_count} avatar(s)")
print()

print(f"Done! Total files uploaded: {bg_count + av_count}")
