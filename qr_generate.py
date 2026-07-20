# qr_generate.py
"""설비 QR 코드 일괄 생성."""
from __future__ import annotations

import asyncio
import os

import qrcode
from sqlalchemy import select

from database import AsyncSessionLocal, engine, Base
from models import Equipment


async def generate_all() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    os.makedirs("qr_codes", exist_ok=True)
    base_url = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Equipment).where(Equipment.is_active == True))
        equipment_list = result.scalars().all()

        for eq in equipment_list:
            url = f"{base_url}/eq/{eq.code}"
            img = qrcode.make(url)
            path = os.path.join("qr_codes", f"{eq.code}.png")
            img.save(path)
            print(f"Generated: {path} -> {url}")

    print(f"Done. {len(equipment_list)} QR codes generated.")


if __name__ == "__main__":
    asyncio.run(generate_all())
