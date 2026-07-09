"""
Simulates your real application writing a new customer. The trigger
on `customer` (see sql/central_db_outbox.sql) fires automatically and
queues an outbox entry -- nothing else needs to know this happened.

    python scripts/insert_sample_row.py "Ada Lovelace" ada@example.com
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.db import central_conn


def insert_customer(full_name: str, email: str, phone: str = None, status: str = "active"):
    conn = central_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO irimsv.customer (full_name, email, phone, created_at, status)
                VALUES (%s, %s, %s, now(), %s)
                RETURNING id
                """,
                (full_name, email, phone, status),
            )
            new_id = cur.fetchone()[0]
        conn.commit()
        print(f"Inserted customer id={new_id}")
    finally:
        conn.close()


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "Ada Lovelace"
    email = sys.argv[2] if len(sys.argv) > 2 else "ada@example.com"
    insert_customer(name, email)
