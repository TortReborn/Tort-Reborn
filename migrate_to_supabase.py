"""
migrate_to_supabase.py
Upload existing local profile backgrounds and cached avatars to Supabase Storage.
Safe to re-run (overwrites existing objects).

Usage: python migrate_to_supabase.py
"""
import os
import certifi
import boto3
from botocore.config import Config
from dotenv import load_dotenv

load_dotenv()

test_mode = os.getenv("TEST_MODE", "").lower() in ("true", "1", "t")
bucket = os.getenv("TEST_S3_BUCKET_NAME" if test_mode else "S3_BUCKET_NAME")

client = boto3.client(
    "s3",
    endpoint_url=os.getenv("S3_ENDPOINT_URL"),
    aws_access_key_id=os.getenv("S3_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("S3_SECRET_ACCESS_KEY"),
    config=Config(signature_version="s3v4"),
    region_name=os.getenv("S3_REGION", "us-east-1"),
    verify=certifi.where(),
)


def upload_dir(local_dir, s3_prefix):
    if not os.path.isdir(local_dir):
        print(f"  Skipping {local_dir} (not found)")
        return 0
    count = 0
    for fn in os.listdir(local_dir):
        path = os.path.join(local_dir, fn)
        if not os.path.isfile(path):
            continue
        key = f"{s3_prefix}/{fn}"
        print(f"  {path} -> {key}")
        try:
            with open(path, "rb") as f:
                client.put_object(Bucket=bucket, Key=key, Body=f.read(), ContentType="image/png")
            count += 1
        except Exception as e:
            print(f"    ERROR: {e}")
    return count


print(f"Migrating to Supabase bucket: {bucket}\n")

print("=== Profile Backgrounds ===")
bg = upload_dir("images/profile_backgrounds", "profile_backgrounds")
print(f"  Uploaded {bg} background(s)\n")

print("=== Cached Avatars ===")
av = upload_dir("cache/avatars", "avatars")
print(f"  Uploaded {av} avatar(s)\n")

print(f"Done! Total: {bg + av} files")
