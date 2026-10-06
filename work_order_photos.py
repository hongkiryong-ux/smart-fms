# -*- coding: utf-8 -*-
"""정비의뢰 첨부 사진 — 500KB 이하 자동 축소 · 기본 1년 보관 · 영구 보관."""
from __future__ import annotations

import io
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import WorkOrderPhoto

MAX_PHOTO_BYTES = 500 * 1024
MAX_PHOTOS_PER_ORDER = 10
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
RETENTION_DAYS = 365
_MAX_EDGE = 1920
_MIN_EDGE = 320


def retention_expiry(base: datetime | None = None) -> datetime:
    return (base or datetime.utcnow()) + timedelta(days=RETENTION_DAYS)


def shrink_image(data: bytes) -> tuple[bytes, str, int, int]:
    """사진을 500KB 이하 JPEG로 축소. 이미 500KB 이하인 JPEG/PNG/WEBP는 그대로 둔다."""
    from PIL import Image, ImageOps

    img = Image.open(io.BytesIO(data))
    img.load()
    fmt = (img.format or "").upper()
    if len(data) <= MAX_PHOTO_BYTES and fmt in ("JPEG", "PNG", "WEBP"):
        ctype = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}[fmt]
        return data, ctype, img.width, img.height

    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        rgba = img.convert("RGBA")
        background.paste(rgba, mask=rgba.split()[-1])
        img = background
    elif img.mode == "L":
        img = img.convert("RGB")

    scale = min(1.0, _MAX_EDGE / max(img.width, img.height))
    while True:
        w = max(1, int(img.width * scale))
        h = max(1, int(img.height * scale))
        resized = img if scale >= 1.0 else img.resize((w, h), Image.LANCZOS)
        for quality in (85, 75, 65, 55, 45):
            buf = io.BytesIO()
            resized.save(buf, format="JPEG", quality=quality, optimize=True)
            out = buf.getvalue()
            if len(out) <= MAX_PHOTO_BYTES:
                return out, "image/jpeg", resized.width, resized.height
        if max(w, h) <= _MIN_EDGE:
            return out, "image/jpeg", resized.width, resized.height
        scale *= 0.75


async def count_photos(db: AsyncSession, work_order_id: int) -> int:
    return int(
        (
            await db.execute(
                select(func.count(WorkOrderPhoto.id)).where(
                    WorkOrderPhoto.work_order_id == work_order_id
                )
            )
        ).scalar()
        or 0
    )


async def save_uploads(
    db: AsyncSession,
    work_order_id: int,
    files: list[Any] | None,
    *,
    uploader_name: str | None,
    uploader_id: int | None,
) -> tuple[int, int]:
    """업로드된 사진을 축소해 저장. (저장 수, 제외 수) 반환."""
    saved = skipped = 0
    existing = await count_photos(db, work_order_id)
    for up in files or []:
        name = (getattr(up, "filename", "") or "").strip()
        if not name:
            continue
        if existing + saved >= MAX_PHOTOS_PER_ORDER:
            skipped += 1
            continue
        raw = await up.read()
        if not raw or len(raw) > MAX_UPLOAD_BYTES:
            skipped += 1
            continue
        try:
            data, ctype, w, h = shrink_image(raw)
        except Exception:
            skipped += 1
            continue
        db.add(
            WorkOrderPhoto(
                work_order_id=work_order_id,
                original_name=name[:300],
                content_type=ctype,
                file_data=data,
                file_size=len(data),
                width=w,
                height=h,
                uploaded_by=(uploader_name or "")[:100] or None,
                uploaded_by_user_id=uploader_id,
                expires_at=retention_expiry(),
            )
        )
        saved += 1
    if saved:
        await db.flush()
    return saved, skipped


async def list_photos(db: AsyncSession, work_order_id: int) -> list[WorkOrderPhoto]:
    return list(
        (
            await db.execute(
                select(WorkOrderPhoto)
                .where(WorkOrderPhoto.work_order_id == work_order_id)
                .order_by(WorkOrderPhoto.id)
            )
        )
        .scalars()
        .all()
    )


async def photo_counts(db: AsyncSession, work_order_ids: list[int]) -> dict[int, int]:
    if not work_order_ids:
        return {}
    rows = (
        await db.execute(
            select(WorkOrderPhoto.work_order_id, func.count(WorkOrderPhoto.id))
            .where(WorkOrderPhoto.work_order_id.in_(work_order_ids))
            .group_by(WorkOrderPhoto.work_order_id)
        )
    ).all()
    return {int(wid): int(n) for wid, n in rows}


def set_permanent(photo: WorkOrderPhoto, keep: bool, by: str | None) -> None:
    if keep:
        photo.is_permanent = True
        photo.permanent_by = (by or "")[:100] or None
        photo.permanent_at = datetime.utcnow()
    else:
        photo.is_permanent = False
        photo.permanent_by = None
        photo.permanent_at = None
        photo.expires_at = max(retention_expiry(photo.created_at), datetime.utcnow() + timedelta(days=30))


async def purge_expired(db: AsyncSession) -> int:
    result = await db.execute(
        delete(WorkOrderPhoto).where(
            WorkOrderPhoto.is_permanent == False,  # noqa: E712
            WorkOrderPhoto.expires_at.is_not(None),
            WorkOrderPhoto.expires_at < datetime.utcnow(),
        )
    )
    await db.commit()
    return int(result.rowcount or 0)


def register_scheduler(scheduler, session_factory, kst) -> None:
    """매일 03:20 KST — 보관기간(1년)이 지난 정비의뢰 사진 삭제 (영구 보관 제외)."""

    async def _purge_job():
        async with session_factory() as session:
            try:
                n = await purge_expired(session)
                if n:
                    print(f"[wo-photo] expired photos purged: {n}", flush=True)
            except Exception as e:
                print(f"[wo-photo] purge failed: {e}", flush=True)

    scheduler.add_job(
        _purge_job,
        trigger="cron",
        hour=3,
        minute=20,
        timezone=kst,
        id="work_order_photo_purge",
        replace_existing=True,
    )
