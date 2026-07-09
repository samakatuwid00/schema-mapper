"""Bootstrap or update an admin UI user.

Usage: python scripts/create_admin_user.py USERNAME --role admin
Prompts for the password (or pass --password, discouraged on shared shells).
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.admin_api.auth import hash_password
from src.connectors import PostgresCentralConnector


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("username")
    parser.add_argument("--role", choices=("operator", "admin"), default="operator")
    parser.add_argument("--password", default=None)
    args = parser.parse_args()
    password = args.password or getpass.getpass(f"Password for {args.username}: ")
    if len(password) < 8:
        raise SystemExit("password must be at least 8 characters")
    connector = PostgresCentralConnector()
    try:
        with connector.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO integration.admin_user (username, password_hash, role)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (username) DO UPDATE
                        SET password_hash = EXCLUDED.password_hash,
                            role = EXCLUDED.role, is_active = true
                """, (args.username, hash_password(password), args.role))
            conn.commit()
        print(f"user '{args.username}' ready with role {args.role}")
    finally:
        connector.close()


if __name__ == "__main__":
    main()
