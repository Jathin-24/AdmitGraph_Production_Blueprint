"""Demo example & replay: captured real run (fixture) + demo run orchestration.

- fixture.py   — load scripts/demo/example.json and upsert it idempotently
- persona.py   — MASTER_SPEC §18 synthetic student, fills empty fields only
- runner.py    — POST /research/demo background run (no SerpApi, no LLM)
"""
