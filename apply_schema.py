"""
Run this script to apply schema.sql to the database.
Uses the same connection logic as the bot (respects TEST_MODE env var).

Usage:
    python apply_schema.py           # applies to whichever DB TEST_MODE points to
    TEST_MODE=true python apply_schema.py
    TEST_MODE=false python apply_schema.py
"""

import os
import sys

import psycopg2
from dotenv import load_dotenv

load_dotenv()

SCHEMA_FILE = os.path.join(os.path.dirname(__file__), "schema.sql")


def get_connection():
    test_mode = os.getenv("TEST_MODE", "").lower()
    if test_mode == "true":
        env = "TEST"
        conn = psycopg2.connect(
            user=os.getenv("TEST_DB_LOGIN"),
            password=os.getenv("TEST_DB_PASS"),
            host=os.getenv("TEST_DB_HOST"),
            port=int(os.getenv("TEST_DB_PORT")),
            database=os.getenv("TEST_DB_DATABASE", "postgres"),
            sslmode=os.getenv("TEST_DB_SSLMODE"),
        )
    elif test_mode == "false":
        env = "PROD"
        conn = psycopg2.connect(
            user=os.getenv("DB_LOGIN"),
            password=os.getenv("DB_PASS"),
            host=os.getenv("DB_HOST"),
            port=int(os.getenv("DB_PORT")),
            database=os.getenv("DB_DATABASE", "postgres"),
            sslmode=os.getenv("DB_SSLMODE"),
        )
    else:
        print("ERROR: TEST_MODE env var must be 'true' or 'false'.")
        sys.exit(1)

    return conn, env


def main():
    conn, env = get_connection()
    print(f"Connected to {env} database.")

    with open(SCHEMA_FILE, "r", encoding="utf-8") as f:
        schema_sql = f.read()

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(schema_sql)
        print("Schema applied successfully.")
    except Exception as e:
        print(f"ERROR applying schema: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
