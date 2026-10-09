"""Export the FastAPI OpenAPI document to ``api/openapi.json``.

The committed spec is the source of truth for cross-team contract checks:
frontend types are generated from it (``npm run gen:api-types``) and CI
fails when the app's live spec drifts from the committed file.

Usage:
    python -m scripts.export_openapi                # write ../api/openapi.json
    python -m scripts.export_openapi --output FILE  # write elsewhere
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_OUTPUT = Path(__file__).resolve().parents[2] / "api" / "openapi.json"


def build_spec() -> dict:
    from app.main import app

    return app.openapi()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export the FastAPI OpenAPI document.")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output path (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args(argv)

    spec = build_spec()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(spec, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
