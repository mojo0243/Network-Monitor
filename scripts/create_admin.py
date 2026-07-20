"""Create (or reset the password of) a dashboard login.

Usage:
    python scripts/create_admin.py

There's no self-service signup in the app on purpose -- this script is the
only way to create a user, which is appropriate for a single-household tool.
"""
from __future__ import annotations

import asyncio
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from netmon.config import ConfigError, load_config  # noqa: E402
from netmon.db import create_all_tables, init_engine, session_scope  # noqa: E402
from netmon.models import User  # noqa: E402
from netmon.security import hash_password  # noqa: E402


async def main() -> None:
    config_path = os.environ.get("NETMON_CONFIG", "config.yml")
    try:
        settings = load_config(config_path)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(1)

    init_engine(settings)
    await create_all_tables()

    username = input("Username: ").strip()
    if not username:
        print("Username cannot be empty.", file=sys.stderr)
        raise SystemExit(1)

    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords did not match.", file=sys.stderr)
        raise SystemExit(1)
    if len(password) < 8:
        print("Password must be at least 8 characters.", file=sys.stderr)
        raise SystemExit(1)

    async with session_scope() as session:
        existing = await session.scalar(select(User).where(User.username == username))
        if existing:
            existing.password_hash = hash_password(password)
            print(f"Updated password for existing user '{username}'.")
        else:
            session.add(User(username=username, password_hash=hash_password(password)))
            print(f"Created user '{username}'.")


if __name__ == "__main__":
    asyncio.run(main())
