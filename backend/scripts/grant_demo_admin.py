"""One-off: grant the local demo user the ADMIN role (admin endpoint gating)."""

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
