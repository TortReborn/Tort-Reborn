"""
Supabase S3 connectivity test.
Run: python test_r2.py
"""
import os
import certifi
import boto3
from botocore.config import Config
from dotenv import load_dotenv

load_dotenv()

endpoint = "https://iwsxvkdagcbvlhajvtvf.supabase.co/storage/v1/s3"
access_key = "c56223502ed60a17f85d21ba0816f2be"
secret_key = "b8927eab67cc7e390fe128f197d44df4a487c8f0f2d0810db1b963c2bc658759"
bucket = "Tort-Reborn-Dev"

print(f"Endpoint: {endpoint}")
print(f"Bucket:   {bucket}")
print()

client = boto3.client(
    "s3",
    endpoint_url=endpoint,
    aws_access_key_id=access_key,
    aws_secret_access_key=secret_key,
    config=Config(signature_version="s3v4"),
    region_name="us-east-1",
    verify=certifi.where(),
)

# Test 1: List objects
print("--- Test 1: ListObjectsV2 ---")
try:
    resp = client.list_objects_v2(Bucket=bucket, MaxKeys=5)
    contents = resp.get("Contents", [])
    print(f"  OK - Found {len(contents)} object(s)")
    for obj in contents:
        print(f"    {obj['Key']} ({obj['Size']} bytes)")
except Exception as e:
    print(f"  FAIL: {e}")

# Test 2: Put + Get round-trip
print("\n--- Test 2: Put + Get round-trip ---")
test_key = "_test/connectivity_check.txt"
test_data = b"Supabase S3 connectivity OK"
try:
    client.put_object(Bucket=bucket, Key=test_key, Body=test_data, ContentType="text/plain")
    print(f"  PUT OK: {test_key}")

    resp = client.get_object(Bucket=bucket, Key=test_key)
    body = resp["Body"].read()
    assert body == test_data, f"Data mismatch: {body!r}"
    print(f"  GET OK: {body.decode()}")

    client.delete_object(Bucket=bucket, Key=test_key)
    print(f"  DELETE OK: cleaned up test object")
except Exception as e:
    print(f"  FAIL: {e}")

print("\nDone!")
