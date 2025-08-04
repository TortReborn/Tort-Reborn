import os
import psycopg2
from psycopg2 import OperationalError

class DB:
    def __init__(self):
        self.connection = None
        self.cursor = None

    def connect(self):
        try:
            if os.getenv("TEST_MODE").lower() == "true":
                self.connection = psycopg2.connect(
                    user=os.getenv("TEST_DB_LOGIN"),
                    password=os.getenv("TEST_DB_PASS"),
                    host=os.getenv("TEST_DB_HOST"),
                    port=int(os.getenv("TEST_DB_PORT")),
                    database=os.getenv("TEST_DB_DATABASE", "postgres"),
                    sslmode=os.getenv("TEST_DB_SSLMODE"),
                    keepalives=1,
                    keepalives_idle=30,
                    keepalives_interval=10,
                    keepalives_count=5
                )
                self.cursor = self.connection.cursor()
            elif os.getenv("TEST_MODE").lower() == "false":
                self.connection = psycopg2.connect(
                    user=os.getenv("DB_LOGIN"),
                    password=os.getenv("DB_PASS"),
                    host=os.getenv("DB_HOST"),
                    port=int(os.getenv("DB_PORT")),
                    database=os.getenv("DB_DATABASE", "postgres"),
                    sslmode=os.getenv("DB_SSLMODE"),
                    keepalives=1,
                    keepalives_idle=30,
                    keepalives_interval=10,
                    keepalives_count=5
                )
                self.cursor = self.connection.cursor()
            else:
                print("Problem logging into db")
                exit(-1)
        except OperationalError as e:
            print(f"[DB] Connection failed: {e}")
            raise

    def close(self):
        """Close cursor and connection."""
        if self.cursor:
            self.cursor.close()
        if self.connection:
            self.connection.close()
