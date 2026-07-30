# auth.py
from __future__ import annotations

import hashlib
import os
import secrets
from typing import Callable

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Building, User, UserRole

ADMIN_ID = os.environ.get("ADMIN_ID", "admin")
ADMIN_PW = os.environ.get("ADMIN_PW", "password123")


def hash_password(password: str) -> str:
    salt = "smart_fms_salt_v1"
    return hashlib.sha256(f"{salt}{password}".encode()).hexdigest()


def verify_password(password: str, password_hash: str) -> bool:
    return secrets.compare_digest(hash_password(password), password_hash)


def nav_building_sort_key(name: str | None) -> tuple:
    """건물명 가나다 → ABC → 숫자 → 기타."""
    n = (name or "").strip()
    if not n:
        return (3, "")
    ch = n[0]
    if "\uac00" <= ch <= "\ud7a3" or "\u3131" <= ch <= "\u318e":
        group = 0
    elif ch.isascii() and ch.isalpha():
        group = 1
    elif ch.isdigit():
        group = 2
    else:
        group = 3
    return (group, n.casefold())


def default_permissions(role: UserRole) -> tuple[bool, bool, bool]:
    """역할별 기본 권한 (추가, 수정, 삭제)."""
    if role == UserRole.system_admin:
        return True, True, True
    if role == UserRole.viewer:
        return False, False, False
    if role in (UserRole.partner, UserRole.external):
        return False, True, False
    return True, True, True


def apply_role_permissions(user: User) -> None:
    create, edit, delete = default_permissions(user.role)
    user.can_create = create
    user.can_edit = edit
    user.can_delete = delete


async def get_current_user(
    request: Request, db: AsyncSession = Depends(get_db)
) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        request.state.nav_buildings = []
        return None
    result = await db.execute(
        select(User).where(
            User.id == user_id,
            User.is_active == True,
            User.is_approved == True,
        )
    )
    user = result.scalar_one_or_none()
    if not hasattr(request.state, "nav_buildings"):
        request.state.nav_buildings = []
        if user:
            try:
                rows = (
                    await db.execute(select(Building).where(Building.is_active == True))
                ).scalars().all()
                # dict로 보관 — 이후 commit/expire 시 템플릿 greenlet 오류 방지
                sorted_rows = sorted(
                    list(rows),
                    key=lambda b: nav_building_sort_key(getattr(b, "name", None)),
                )
                request.state.nav_buildings = [
                    {"id": b.id, "name": b.name or "", "code": getattr(b, "code", "") or ""}
                    for b in sorted_rows
                ]
            except Exception:
                request.state.nav_buildings = []
    return user


def require_login(
    request: Request, user: User | None = Depends(get_current_user)
) -> User:
    if not user:
        # 303을 HTTPException으로 내면 일부 환경에서 오류 페이지로 보일 수 있음
        raise HTTPException(
            status_code=401,
            detail="login_required",
            headers={"X-Redirect": "/admin/login"},
        )
    return user


def require_roles(*roles: UserRole) -> Callable:
    async def _checker(user: User = Depends(require_login)) -> User:
        if user.role not in roles and user.role != UserRole.system_admin:
            raise HTTPException(status_code=403, detail="권한이 없습니다.")
        return user

    return _checker


def require_user_manager(user: User = Depends(require_login)) -> User:
    """계정관리(승인·권한·삭제) — 시스템관리자만."""
    if user.role != UserRole.system_admin:
        raise HTTPException(status_code=403, detail="계정 관리 권한이 없습니다.")
    return user


def can_create(user: User | None) -> bool:
    if user is None:
        return False
    if user.role == UserRole.system_admin:
        return True
    return bool(getattr(user, "can_create", True))


def can_edit(user: User | None) -> bool:
    if user is None:
        return False
    if user.role == UserRole.system_admin:
        return True
    return bool(getattr(user, "can_edit", True))


def can_delete(user: User | None) -> bool:
    """엔티티 삭제(사업장/설비/정비의뢰 등) 가능 여부."""
    if user is None:
        return False
    if user.role == UserRole.system_admin:
        return True
    return bool(getattr(user, "can_delete", False))


def require_can_create(user: User = Depends(require_login)) -> User:
    if not can_create(user):
        raise HTTPException(status_code=403, detail="추가 권한이 없습니다.")
    return user


def require_can_edit(user: User = Depends(require_login)) -> User:
    if not can_edit(user):
        raise HTTPException(status_code=403, detail="수정 권한이 없습니다.")
    return user


def require_can_delete(user: User = Depends(require_login)) -> User:
    if not can_delete(user):
        raise HTTPException(status_code=403, detail="삭제 권한이 없습니다.")
    return user


ROLE_LABELS = {
    UserRole.system_admin: "시스템관리자",
    UserRole.site_admin: "사업장관리자",
    UserRole.group_leader: "그룹장",
    UserRole.part_leader: "파트장",
    UserRole.facility_manager: "시설담당자",
    UserRole.partner: "협력사",
    UserRole.external: "외부업체",
    UserRole.viewer: "조회전용",
}
