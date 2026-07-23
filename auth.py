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
from models import User, UserRole

ADMIN_ID = os.environ.get("ADMIN_ID", "admin")
ADMIN_PW = os.environ.get("ADMIN_PW", "password123")


def hash_password(password: str) -> str:
    salt = "smart_fms_salt_v1"
    return hashlib.sha256(f"{salt}{password}".encode()).hexdigest()


def verify_password(password: str, password_hash: str) -> bool:
    return secrets.compare_digest(hash_password(password), password_hash)


async def get_current_user(
    request: Request, db: AsyncSession = Depends(get_db)
) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    result = await db.execute(select(User).where(User.id == user_id, User.is_active == True))
    return result.scalar_one_or_none()


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
