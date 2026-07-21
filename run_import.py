#!/usr/bin/env python
"""설비현황 엑셀 일괄 import CLI."""
import asyncio
import sys
from pathlib import Path

from database import AsyncSessionLocal, ensure_schema_updates, engine, Base
from excel_import import import_from_directory, ensure_all_buildings


async def main(directory: str | None = None) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await ensure_schema_updates()

    candidates = [
        Path(directory) if directory else None,
        Path(r"\\poscowide1\홍기룡\202010 설비현황"),
        Path("data"),
    ]
    src = next((p for p in candidates if p and p.is_dir()), None)
    if not src:
        print("ERROR: import 폴더를 찾을 수 없습니다.", file=sys.stderr)
        sys.exit(1)

    print(f"[import] source: {src}", flush=True)
    async with AsyncSessionLocal() as session:
        results = await import_from_directory(session, src, replace=True)
        print(f"[import] buildings={results['buildings']}", flush=True)
        print(f"[import] created={results['total_created']} updated={results['total_updated']}", flush=True)
        if results["errors"]:
            print(f"[import] errors ({len(results['errors'])}):", flush=True)
            for e in results["errors"][:20]:
                print(f"  - {e}", flush=True)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None))
