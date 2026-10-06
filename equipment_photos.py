"""설비 사진 — 설비 상세(엑셀 양식) 우측에 표시. 500KB 이하로 자동 축소해 DB 저장."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from auth import can_create, can_delete, can_edit, require_login
from database import get_db
from models import Equipment, EquipmentPhoto, User
from work_order_photos import MAX_UPLOAD_BYTES, shrink_image

router = APIRouter()

MAX_PHOTOS_PER_EQUIPMENT = 10


def can_add_equipment_photo(user: User | None) -> bool:
    return can_create(user) or can_edit(user)


def can_remove_equipment_photo(user: User | None, photo: EquipmentPhoto) -> bool:
    if user is None:
        return False
    return can_delete(user) or can_edit(user) or (
        photo.uploaded_by_user_id is not None and photo.uploaded_by_user_id == user.id
    )


async def list_equipment_photos(db: AsyncSession, equipment_id: int) -> list[EquipmentPhoto]:
    return list(
        (
            await db.execute(
                select(EquipmentPhoto)
                .where(EquipmentPhoto.equipment_id == equipment_id)
                .order_by(EquipmentPhoto.sort_order, EquipmentPhoto.id)
            )
        ).scalars().all()
    )


def _back(eq_id: int, *, message: str = "", error: str = "") -> RedirectResponse:
    url = f"/admin/equipment/{eq_id}"
    if message:
        url += f"?photo_message={quote(message)}"
    elif error:
        url += f"?photo_error={quote(error)}"
    return RedirectResponse(url + "#eq-photos", status_code=303)


def _person(user: User) -> str:
    return ((user.name or "").strip() or (user.username or "").strip())[:100]


async def _get_photo(db: AsyncSession, eq_id: int, photo_id: int) -> EquipmentPhoto:
    photo = await db.get(EquipmentPhoto, photo_id)
    if not photo or photo.equipment_id != eq_id:
        raise HTTPException(404, "사진을 찾을 수 없습니다.")
    return photo


@router.get("/admin/equipment/{eq_id}/photos/{photo_id}")
async def equipment_photo_file(
    eq_id: int,
    photo_id: int,
    download: int = 0,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    photo = (
        await db.execute(
            select(EquipmentPhoto)
            .options(undefer(EquipmentPhoto.file_data))
            .where(EquipmentPhoto.id == photo_id, EquipmentPhoto.equipment_id == eq_id)
        )
    ).scalar_one_or_none()
    if not photo:
        raise HTTPException(404, "사진을 찾을 수 없습니다.")
    name = photo.original_name or f"equipment_{eq_id}_{photo_id}.jpg"
    disp = "attachment" if download else "inline"
    return Response(
        content=photo.file_data,
        media_type=photo.content_type or "image/jpeg",
        headers={
            "Content-Disposition": f"{disp}; filename=\"photo_{photo_id}.jpg\"; filename*=UTF-8''{quote(name)}",
            "Cache-Control": "private, max-age=86400",
        },
    )


@router.post("/admin/equipment/{eq_id}/photos")
async def equipment_photo_upload(
    eq_id: int,
    photos: list[UploadFile] = File(default=[]),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    if not can_add_equipment_photo(user):
        raise HTTPException(403, "사진을 등록할 권한이 없습니다.")
    eq = await db.get(Equipment, eq_id)
    if not eq or not eq.is_active:
        raise HTTPException(404, "설비를 찾을 수 없습니다.")
    existing = (
        await db.execute(select(func.count(EquipmentPhoto.id)).where(EquipmentPhoto.equipment_id == eq_id))
    ).scalar() or 0
    max_order = (
        await db.execute(select(func.max(EquipmentPhoto.sort_order)).where(EquipmentPhoto.equipment_id == eq_id))
    ).scalar() or 0
    saved = skipped = 0
    for up in photos or []:
        name = (getattr(up, "filename", "") or "").strip()
        if not name:
            continue
        if existing + saved >= MAX_PHOTOS_PER_EQUIPMENT:
            skipped += 1
            continue
        raw = await up.read(MAX_UPLOAD_BYTES + 1)
        if not raw or len(raw) > MAX_UPLOAD_BYTES:
            skipped += 1
            continue
        try:
            data, ctype, w, h = shrink_image(raw)
        except Exception:
            skipped += 1
            continue
        saved += 1
        db.add(
            EquipmentPhoto(
                equipment_id=eq_id,
                original_name=name[:300],
                content_type=ctype,
                file_data=data,
                file_size=len(data),
                width=w,
                height=h,
                sort_order=int(max_order) + saved,
                uploaded_by=_person(user) or None,
                uploaded_by_user_id=user.id,
            )
        )
    await db.commit()
    if not saved and not skipped:
        return _back(eq_id, error="등록할 사진을 선택하세요.")
    if skipped:
        return _back(
            eq_id,
            error=f"사진 {saved}장 등록 · {skipped}장 제외 (이미지가 아니거나 너무 큼, 설비당 최대 {MAX_PHOTOS_PER_EQUIPMENT}장)",
        )
    return _back(eq_id, message=f"사진 {saved}장을 등록했습니다.")


@router.post("/admin/equipment/{eq_id}/photos/{photo_id}/main")
async def equipment_photo_set_main(
    eq_id: int,
    photo_id: int,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    if not can_add_equipment_photo(user):
        raise HTTPException(403, "대표 사진을 바꿀 권한이 없습니다.")
    photo = await _get_photo(db, eq_id, photo_id)
    min_order = (
        await db.execute(select(func.min(EquipmentPhoto.sort_order)).where(EquipmentPhoto.equipment_id == eq_id))
    ).scalar() or 0
    photo.sort_order = int(min_order) - 1
    await db.commit()
    return _back(eq_id, message="대표 사진으로 지정했습니다.")


@router.post("/admin/equipment/{eq_id}/photos/{photo_id}/delete")
async def equipment_photo_delete(
    eq_id: int,
    photo_id: int,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    photo = await _get_photo(db, eq_id, photo_id)
    if not can_remove_equipment_photo(user, photo):
        raise HTTPException(403, "사진을 삭제할 권한이 없습니다.")
    await db.delete(photo)
    await db.commit()
    return _back(eq_id, message="사진을 삭제했습니다.")
