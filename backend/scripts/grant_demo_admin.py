"""One-off: grant the local demo user the ADMIN role.

NOTE on what this does *not* do: admin endpoints require an **authenticated**
ADMIN bearer token — anonymous callers are always `403 FORBIDDEN`, whatever
role the demo row carries (see `app/api/v1/admin.py`). Running this only helps
if you can actually sign in as ``demo@admitgraph.local`` (the seeded demo
account is passwordless by construction, so it cannot log in). Grant
admin by adding your own address to ``ADMIN_EMAILS`` instead.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.db.session import get_engine


async def main() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        before = await conn.execute(text("SELECT email, role FROM users ORDER BY created_at"))
        print("before:", list(before))
        await conn.execute(
            text("UPDATE users SET role='ADMIN' WHERE email='demo@admitgraph.local'")
        )
        after = await conn.execute(text("SELECT email, role FROM users ORDER BY created_at"))
        print("after:", list(after))


if __name__ == "__main__":
    asyncio.run(main())
