# auth.py
from __future__ import annotations

import hashlib
import os
import secrets
from typing import Callable

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models import Building, InspectionLogBuilding, Partner, User, UserRole

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


def group_buildings_by_site(buildings: list) -> list[dict]:
    """건물을 사업장(site) 단위로 묶어 사이드바·설비관리 목록에 사용."""
    groups: dict[int | str, dict] = {}
    for b in buildings:
        site = getattr(b, "site", None)
        site_id = getattr(b, "site_id", None)
        if site_id is None and site is not None:
            site_id = getattr(site, "id", None)
        key: int | str = site_id if site_id is not None else "_none"
        site_name = ""
        if site is not None:
            site_name = (getattr(site, "name", None) or "").strip()
        if not site_name:
            site_name = "미지정 사업장"
        if key not in groups:
            groups[key] = {
                "site_id": site_id,
                "site_name": site_name,
                "buildings": [],
            }
        item = b
        if not isinstance(b, dict):
            item = {
                "id": b.id,
                "name": b.name or "",
                "code": getattr(b, "code", "") or "",
                "site_id": site_id,
                "site_name": site_name,
            }
        groups[key]["buildings"].append(item)

    result = list(groups.values())
    for g in result:
        g["buildings"] = sorted(
            g["buildings"],
            key=lambda x: nav_building_sort_key(
                x.get("name") if isinstance(x, dict) else getattr(x, "name", None)
            ),
        )
    result.sort(key=lambda g: nav_building_sort_key(g.get("site_name")))
    return result


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
    user.menu_access = default_menu_access(user.role)


# 메인 메뉴 키 (사이드바 표시·접근 제어). 내 계정(account)은 항상 허용.
MENU_ITEMS: tuple[tuple[str, str], ...] = (
    ("dashboard", "Dashboard"),
    ("sites", "사업장/건물"),
    ("equipment", "설비관리"),
    ("pm", "점검(PM)"),
    ("inspection_logs", "점검일지"),
    ("work_orders", "정비접수/승인(정비섹션)"),
    ("d1", "정비 List(D-1)/협력사"),
    ("facility_section", "작업허가/승인(시설섹션)"),
    ("streetlamp", "가로등"),
    ("risk_assessment", "위험성평가"),
    ("materials", "자재관리"),
    ("partners", "협력사"),
    ("users", "계정관리"),
)
MENU_KEYS: tuple[str, ...] = tuple(k for k, _ in MENU_ITEMS)
MENU_LABELS: dict[str, str] = dict(MENU_ITEMS)

# 경로 prefix → 메뉴 키 (긴 경로 우선 매칭용으로 길이순 정렬해 사용)
_MENU_PATH_PREFIXES: tuple[tuple[str, str], ...] = (
    ("/admin/users", "users"),
    ("/admin/dashboard", "dashboard"),
    ("/admin/sites", "sites"),
    ("/admin/buildings", "sites"),
    ("/admin/equipment", "equipment"),
    ("/admin/inspection-logs", "inspection_logs"),
    ("/admin/pm", "pm"),
    ("/admin/work-orders", "work_orders"),
    ("/admin/facility-section", "facility_section"),
    ("/admin/streetlamp", "streetlamp"),
    ("/admin/d1", "d1"),
    ("/admin/risk-assessment", "risk_assessment"),
    ("/admin/materials", "materials"),
    ("/admin/partners", "partners"),
)

_MENU_HOME_PATHS: tuple[tuple[str, str], ...] = (
    ("dashboard", "/admin/dashboard"),
    ("sites", "/admin/sites"),
    ("equipment", "/admin/equipment"),
    ("pm", "/admin/pm"),
    ("inspection_logs", "/admin/inspection-logs"),
    ("work_orders", "/admin/work-orders"),
    ("d1", "/admin/d1"),
    ("facility_section", "/admin/facility-section"),
    ("streetlamp", "/admin/streetlamp/requests"),
    ("risk_assessment", "/admin/risk-assessment"),
    ("materials", "/admin/materials?popup=1"),
    ("partners", "/admin/partners"),
    ("users", "/admin/users"),
)


def default_menu_access(role: UserRole) -> list[str]:
    """역할별 기본 메뉴 접근 목록."""
    if role == UserRole.system_admin:
        return list(MENU_KEYS)
    denied = {"users"}
    if role in (UserRole.partner, UserRole.external):
        denied |= {"equipment", "pm", "inspection_logs", "facility_section", "streetlamp"}
    return [k for k in MENU_KEYS if k not in denied]


def normalize_menu_access(raw) -> list[str]:
    """폼/DB 값을 유효한 메뉴 키 목록으로 정규화."""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [x.strip() for x in raw.split(",") if x.strip()]
    if not isinstance(raw, (list, tuple, set)):
        return []
    allowed = set(MENU_KEYS)
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        key = str(item or "").strip()
        if key in allowed and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def effective_menu_access(user: User | None) -> list[str]:
    if user is None:
        return []
    if user.role == UserRole.system_admin:
        return list(MENU_KEYS)
    raw = getattr(user, "menu_access", None)
    if raw is None:
        return default_menu_access(user.role)
    keys = normalize_menu_access(raw)
    # 신규 메뉴 자동 부여 (저장된 권한에 없을 때)
    if (
        user.role not in (UserRole.partner, UserRole.external)
        and "facility_section" not in keys
        and ("d1" in keys or "work_orders" in keys)
    ):
        if "d1" in keys:
            keys.insert(keys.index("d1") + 1, "facility_section")
        else:
            keys.append("facility_section")
    if (
        user.role not in (UserRole.partner, UserRole.external)
        and "streetlamp" not in keys
        and ("work_orders" in keys or "d1" in keys or "facility_section" in keys)
    ):
        keys.append("streetlamp")
    return keys


def can_access_menu(user: User | None, menu_key: str) -> bool:
    """메인 메뉴 접근 가능 여부. account(내 계정)는 항상 True."""
    if user is None:
        return False
    if menu_key == "account":
        return True
    if user.role == UserRole.system_admin:
        return True
    return menu_key in effective_menu_access(user)


def menu_key_for_path(path: str) -> str | None:
    for prefix, key in _MENU_PATH_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return key
    return None


def home_path_for_user(user: User | None) -> str:
    if user is None:
        return "/admin/login"
    for key, path in _MENU_HOME_PATHS:
        if can_access_menu(user, key):
            return path
    return "/admin/account"


async def get_current_user(
    request: Request, db: AsyncSession = Depends(get_db)
) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        request.state.nav_buildings = []
        request.state.nav_building_groups = []
        request.state.nav_inspection_log_buildings = []
        request.state.nav_partners = []
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
        request.state.nav_building_groups = []
        request.state.nav_inspection_log_buildings = []
        request.state.nav_partners = []
        if user:
            try:
                rows = (
                    await db.execute(
                        select(Building)
                        .where(Building.is_active == True)  # noqa: E712
                        .options(selectinload(Building.site))
                    )
                ).scalars().all()
                request.state.nav_building_groups = group_buildings_by_site(list(rows))
                # 하위 호환: 평면 목록
                request.state.nav_buildings = [
                    b
                    for g in request.state.nav_building_groups
                    for b in g.get("buildings", [])
                ]
            except Exception:
                request.state.nav_buildings = []
                request.state.nav_building_groups = []
            try:
                log_rows = (
                    await db.execute(
                        select(InspectionLogBuilding, Building)
                        .join(Building, Building.id == InspectionLogBuilding.building_id)
                        .where(Building.is_active == True)  # noqa: E712
                        .options(selectinload(Building.site))
                    )
                ).all()
                log_buildings = [b for _, b in log_rows]
                request.state.nav_inspection_log_buildings = [
                    {
                        "id": b.id,
                        "name": b.name or "",
                        "code": getattr(b, "code", "") or "",
                        "site_name": (b.site.name if b.site else "") or "",
                    }
                    for b in sorted(
                        log_buildings,
                        key=lambda x: nav_building_sort_key(getattr(x, "name", None)),
                    )
                ]
            except Exception:
                request.state.nav_inspection_log_buildings = []
            try:
                partners = (
                    await db.execute(
                        select(Partner)
                        .where(Partner.is_active == True)  # noqa: E712
                        .order_by(Partner.name)
                    )
                ).scalars().all()
                request.state.nav_partners = [
                    {"id": p.id, "name": p.name or "", "code": getattr(p, "code", "") or ""}
                    for p in partners
                ]
            except Exception:
                request.state.nav_partners = []
    if not hasattr(request.state, "nav_building_groups"):
        request.state.nav_building_groups = []
    if not hasattr(request.state, "nav_inspection_log_buildings"):
        request.state.nav_inspection_log_buildings = []
    if not hasattr(request.state, "nav_partners"):
        request.state.nav_partners = []
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

# 가입신청 시 선택 가능 역할 (시스템관리자 제외)
SIGNUP_ROLES: tuple[UserRole, ...] = (
    UserRole.facility_manager,
    UserRole.site_admin,
    UserRole.group_leader,
    UserRole.part_leader,
    UserRole.viewer,
    UserRole.partner,
    UserRole.external,
)


def can_access_equipment_pm(user: User | None) -> bool:
    """설비관리·점검(PM) 둘 다 접근 가능한지 (하위 호환)."""
    return can_access_menu(user, "equipment") and can_access_menu(user, "pm")
