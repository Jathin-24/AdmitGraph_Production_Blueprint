"""One-off: reformat legacy fit_assessment explanations to human-readable text."""

import ast
import asyncio

import asyncpg

DSN = "postgresql://admitgraph:admitgraph_dev_password@localhost:5433/admitgraph"


async def main() -> None:
    conn = await asyncpg.connect(DSN)
    rows = await conn.fetch("SELECT id, explanation FROM fit_assessments")
    updated = 0
    for r in rows:
        ex = r["explanation"] or ""
        if not ex.startswith("subscores="):
            continue
        subscores = ast.literal_eval(ex[len("subscores="):])
        breakdown = ", ".join(
            f"{k.replace('_', ' ').capitalize()} {v}" for k, v in subscores.items()
        )
        new = (
            f"Weighted from your profile — {breakdown}. "
            "This is a fit score, not an admission probability."
        )
        await conn.execute(
            "UPDATE fit_assessments SET explanation = $1 WHERE id = $2", new, r["id"]
        )
        updated += 1
    await conn.close()
    print(f"reformatted {updated}/{len(rows)} fit explanations")


asyncio.run(main())
