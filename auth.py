# auth.py
from __future__ import annotations

import hashlib
import os
import secrets
import time
from typing import Callable

from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import AsyncSessionLocal
from models import Building, InspectionLogBuilding, InspectionLogBuilding2, Partner, User, UserRole

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
    ("schedules", "주요설비 일정"),
    ("notices", "공지사항"),
    ("sites", "사업장/건물"),
    ("equipment", "설비관리"),
    ("pm", "점검(PM)"),
    ("inspection_logs", "점검일지"),
    ("inspection_logs2", "점검일지2"),
    ("work_orders", "정비접수/승인(정비섹션)"),
    ("d1", "정비 List(D-1)/협력사"),
    ("facility_section", "작업허가/승인(시설섹션)"),
    ("streetlamp", "가로등"),
    ("ai_analysis", "AI 분석"),
    ("server", "서버관리"),
    ("risk_assessment", "위험성평가"),
    ("materials", "자재관리"),
    ("partners", "협력사"),
    ("users", "계정관리"),
)
MENU_KEYS: tuple[str, ...] = tuple(k for k, _ in MENU_ITEMS)
MENU_LABELS: dict[str, str] = dict(MENU_ITEMS)

# menu_access JSON에 함께 저장되는 특수 플래그 (일반 메뉴 키 아님)
MENU_ACCESS_FLAG_KEYS: frozenset[str] = frozenset({"d1_all_partners"})
MENU_ACCESS_FLAG_LABELS: dict[str, str] = {
    "d1_all_partners": "D-1 전체 협력사 메뉴 (모든 업체 표시)",
}

# 경로 prefix → 메뉴 키 (긴 경로 우선 매칭용으로 길이순 정렬해 사용)
_MENU_PATH_PREFIXES: tuple[tuple[str, str], ...] = (
    ("/admin/users", "users"),
    ("/admin/dashboard", "dashboard"),
    ("/admin/schedules", "schedules"),
    ("/admin/notices", "notices"),
    ("/admin/sites", "sites"),
    ("/admin/buildings", "sites"),
    ("/admin/equipment", "equipment"),
    ("/admin/inspection-logs2", "inspection_logs2"),
    ("/admin/inspection-logs", "inspection_logs"),
    ("/admin/pm", "pm"),
    ("/admin/work-orders", "work_orders"),
    ("/admin/facility-section", "facility_section"),
    ("/admin/streetlamp", "streetlamp"),
    ("/admin/ai-analysis", "ai_analysis"),
    ("/admin/server", "server"),
    ("/admin/d1", "d1"),
    ("/admin/risk-assessment", "risk_assessment"),
    ("/admin/materials", "materials"),
    ("/admin/partners", "partners"),
)

_MENU_HOME_PATHS: tuple[tuple[str, str], ...] = (
    ("dashboard", "/admin/dashboard"),
    ("schedules", "/admin/schedules"),
    ("notices", "/admin/notices"),
    ("sites", "/admin/sites"),
    ("equipment", "/admin/equipment"),
    ("pm", "/admin/pm"),
    ("inspection_logs", "/admin/inspection-logs"),
    ("inspection_logs2", "/admin/inspection-logs2"),
    ("work_orders", "/admin/work-orders"),
    ("d1", "/admin/d1"),
    ("facility_section", "/admin/facility-section"),
    ("streetlamp", "/admin/streetlamp/requests"),
    ("ai_analysis", "/admin/ai-analysis"),
    ("server", "/admin/server"),
    ("risk_assessment", "/admin/risk-assessment"),
    ("materials", "/admin/materials?popup=1"),
    ("partners", "/admin/partners"),
    ("users", "/admin/users"),
)


def default_menu_access(role: UserRole) -> list[str]:
    """역할별 기본 메뉴 접근 목록."""
    if role == UserRole.system_admin:
        return list(MENU_KEYS)
    denied = {"users", "server"}
    if role in (UserRole.partner, UserRole.external):
        denied |= {"equipment", "pm", "inspection_logs", "inspection_logs2", "facility_section", "streetlamp"}
    return [k for k in MENU_KEYS if k not in denied]


def normalize_menu_access(raw) -> list[str]:
    """폼/DB 값을 유효한 메뉴 키 목록으로 정규화."""
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        # JSON 배열 문자열로 저장된 경우
        if text.startswith("["):
            try:
                import json

                parsed = json.loads(text)
                if isinstance(parsed, list):
                    raw = parsed
                else:
                    raw = [x.strip() for x in text.split(",") if x.strip()]
            except Exception:
                raw = [x.strip() for x in text.split(",") if x.strip()]
        else:
            raw = [x.strip() for x in text.split(",") if x.strip()]
    if not isinstance(raw, (list, tuple, set)):
        return []
    allowed = set(MENU_KEYS)
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        key = str(item or "").strip().strip('"').strip("'")
        if key in allowed and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def menu_access_flags_from_raw(raw) -> set[str]:
    """menu_access 원본에서 특수 플래그만 추출."""
    if raw is None:
        return set()
    items: list = []
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("["):
            try:
                import json

                parsed = json.loads(text)
                items = parsed if isinstance(parsed, list) else []
            except Exception:
                items = [x.strip() for x in text.split(",") if x.strip()]
        else:
            items = [x.strip() for x in text.split(",") if x.strip()]
    elif isinstance(raw, (list, tuple, set)):
        items = list(raw)
    return {str(x).strip() for x in items if str(x).strip() in MENU_ACCESS_FLAG_KEYS}


def menu_access_flags_for_edit(user: User | None) -> set[str]:
    if user is None or user.role == UserRole.system_admin:
        return set()
    return menu_access_flags_from_raw(getattr(user, "menu_access", None))


def d1_partner_nav_restricted(user: User | None) -> bool:
    """협력사 역할 + D-1 전체 메뉴 플래그 없음 → 내 회사만."""
    if user is None or user.role != UserRole.partner:
        return False
    if "d1_all_partners" in menu_access_flags_for_edit(user):
        return False
    return True


async def user_own_partner_id(db: AsyncSession, user: User | None) -> int | None:
    """계정에 연결된 협력사 ID (partner_id 또는 회사명 매칭)."""
    if user is None:
        return None
    if user.partner_id:
        return int(user.partner_id)
    cname = (user.company_name or "").strip()
    if not cname:
        return None
    rows = (
        await db.execute(
            select(Partner.id, Partner.name).where(Partner.is_active == True)  # noqa: E712
        )
    ).all()
    folded = cname.casefold()
    for pid, pname in rows:
        if (pname or "").strip().casefold() == folded:
            return int(pid)
    return None


def filter_nav_partners_for_user(user: User | None, partners: list[dict]) -> list[dict]:
    """사이드바 D-1 협력사 목록 — 협력사 제한 시 내 회사만."""
    if not d1_partner_nav_restricted(user):
        return partners
    if user is None:
        return []
    if user.partner_id:
        own = int(user.partner_id)
        return [p for p in partners if int(p.get("id") or 0) == own]
    cname = (user.company_name or "").strip().casefold()
    if not cname:
        return []
    return [
        p
        for p in partners
        if (p.get("name") or "").strip().casefold() == cname
    ]


def menu_access_for_edit(user: User | None) -> list[str]:
    """계정관리 체크박스용 — DB에 저장된 값(없으면 역할 기본값). 자동 추가 없음."""
    if user is None:
        return []
    if user.role == UserRole.system_admin:
        return list(MENU_KEYS)
    raw = getattr(user, "menu_access", None)
    if raw is None:
        return default_menu_access(user.role)
    return normalize_menu_access(raw)


def effective_menu_access(user: User | None) -> list[str]:
    """실제 사이드바 접근. 저장된 menu_access를 그대로 존중한다."""
    if user is None:
        return []
    if user.role == UserRole.system_admin:
        return list(MENU_KEYS)
    raw = getattr(user, "menu_access", None)
    if raw is None:
        return default_menu_access(user.role)
    return normalize_menu_access(raw)


def can_access_menu(user: User | None, menu_key: str) -> bool:
    """메인 메뉴 접근 가능 여부. account(내 계정)는 항상 True."""
    if user is None:
        return False
    if menu_key == "account":
        return True
    if menu_key == "server":
        return user.role == UserRole.system_admin
    if user.role == UserRole.system_admin:
        return True
    return menu_key in effective_menu_access(user)


# D-1 화면에서 호출하는 work-orders 하위 경로 (협력사는 d1만 허용된 경우가 많음)
_D1_WORK_ORDER_ACTIONS: frozenset[str] = frozenset(
    {"status", "request-approval", "delete"}
)


def d1_work_order_path(path: str) -> bool:
    """D-1 협력사 흐름에서 사용하는 /admin/work-orders/* 경로."""
    prefix = "/admin/work-orders/"
    if not path.startswith(prefix):
        return False
    sub = path[len(prefix) :].split("?", 1)[0].strip("/")
    if not sub:
        return False
    parts = sub.split("/")
    if len(parts) == 1 and parts[0].isdigit():
        return True
    if len(parts) == 2 and parts[0].isdigit() and parts[1] in _D1_WORK_ORDER_ACTIONS:
        return True
    return False


def menu_key_for_path(path: str) -> str | None:
    for prefix, key in _MENU_PATH_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return key
    return None


def can_access_admin_path(user: User | None, path: str) -> bool:
    """경로 prefix 기준 메뉴 접근. D-1 work-orders 하위는 d1 권한으로도 허용."""
    menu_key = menu_key_for_path(path)
    if not menu_key:
        return True
    if can_access_menu(user, menu_key):
        return True
    if menu_key == "work_orders" and d1_work_order_path(path):
        return can_access_menu(user, "d1")
    return False


def home_path_for_user(user: User | None) -> str:
    if user is None:
        return "/admin/login"
    for key, path in _MENU_HOME_PATHS:
        if can_access_menu(user, key):
            return path
    return "/admin/account"


_NAV_CACHE_TTL_SEC = 120.0
_nav_cache: dict = {
    "at": 0.0,
    "building_groups": [],
    "buildings": [],
    "inspection_log_buildings": [],
    "inspection_log2_buildings": [],
    "partners": [],
}


def invalidate_nav_cache() -> None:
    """사이드바 건물 목록 등 네비 캐시 즉시 갱신."""
    _nav_cache["at"] = 0.0


async def _load_nav_state(db: AsyncSession) -> dict:
    """사이드바 네비 데이터 (짧은 TTL 메모리 캐시)."""
    now = time.monotonic()
    if now - _nav_cache["at"] < _NAV_CACHE_TTL_SEC:
        return _nav_cache
    building_groups: list[dict] = []
    buildings: list = []
    inspection_log_buildings: list[dict] = []
    inspection_log2_buildings: list[dict] = []
    partners: list[dict] = []
    try:
        rows = (
            await db.execute(
                select(Building)
                .where(Building.is_active == True)  # noqa: E712
                .options(selectinload(Building.site))
            )
        ).scalars().all()
        building_groups = group_buildings_by_site(list(rows))
        buildings = [b for g in building_groups for b in g.get("buildings", [])]
    except Exception:
        pass
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
        inspection_log_buildings = [
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
        pass
    try:
        log2_rows = (
            await db.execute(
                select(InspectionLogBuilding2, Building)
                .join(Building, Building.id == InspectionLogBuilding2.building_id)
                .where(Building.is_active == True)  # noqa: E712
                .options(selectinload(Building.site))
            )
        ).all()
        log2_buildings = [b for _, b in log2_rows]
        inspection_log2_buildings = [
            {
                "id": b.id,
                "name": b.name or "",
                "code": getattr(b, "code", "") or "",
                "site_name": (b.site.name if b.site else "") or "",
            }
            for b in sorted(
                log2_buildings,
                key=lambda x: nav_building_sort_key(getattr(x, "name", None)),
            )
        ]
    except Exception:
        pass
    try:
        partner_rows = (
            await db.execute(
                select(Partner)
                .where(Partner.is_active == True)  # noqa: E712
                .order_by(Partner.name)
            )
        ).scalars().all()
        partners = [
            {"id": p.id, "name": p.name or "", "code": getattr(p, "code", "") or ""}
            for p in partner_rows
        ]
    except Exception:
        pass
    _nav_cache.update(
        {
            "at": now,
            "building_groups": building_groups,
            "buildings": buildings,
            "inspection_log_buildings": inspection_log_buildings,
            "inspection_log2_buildings": inspection_log2_buildings,
            "partners": partners,
        }
    )
    return _nav_cache


async def admin_request_bootstrap(
    request: Request, session: AsyncSession
) -> RedirectResponse | None:
    """/admin 미들웨어: 사용자·메뉴·사이드바 네비 (요청당 DB 1회)."""
    user_id = request.session.get("user_id") if "session" in request.scope else None
    request.state._current_user_loaded = bool(user_id)
    if user_id:
        u = (
            await session.execute(
                select(User).where(
                    User.id == user_id,
                    User.is_active == True,
                    User.is_approved == True,
                )
            )
        ).scalar_one_or_none()
        if u is not None:
            request.state.current_user = u
        path = request.url.path or ""
        if u is not None and not can_access_admin_path(u, path):
            return RedirectResponse("/admin/account?error=no_menu", status_code=303)

    now = time.monotonic()
    if now - _nav_cache["at"] < _NAV_CACHE_TTL_SEC:
        apply_nav_state(request, _nav_cache)
    elif user_id and getattr(request.state, "current_user", None):
        apply_nav_state(request, await _load_nav_state(session))
    else:
        apply_nav_state(request, {})
    user = getattr(request.state, "current_user", None)
    if user is not None:
        request.state.nav_partners = filter_nav_partners_for_user(
            user, list(getattr(request.state, "nav_partners", []) or [])
        )
        if d1_partner_nav_restricted(user):
            own_pid = None
            if user.partner_id:
                own_pid = int(user.partner_id)
            elif request.state.nav_partners:
                own_pid = int(request.state.nav_partners[0].get("id") or 0) or None
            request.state.nav_d1_own_partner_id = own_pid
        else:
            request.state.nav_d1_own_partner_id = None
    await apply_maint_nav_badges(request, session, user)
    return None


def apply_nav_state(request: Request, nav: dict) -> None:
    request.state.nav_building_groups = nav.get("building_groups") or []
    request.state.nav_buildings = nav.get("buildings") or []
    request.state.nav_inspection_log_buildings = nav.get("inspection_log_buildings") or []
    request.state.nav_inspection_log2_buildings = nav.get("inspection_log2_buildings") or []
    request.state.nav_partners = nav.get("partners") or []


async def apply_maint_nav_badges(request: Request, session: AsyncSession, user: User | None) -> None:
    from maint_nav_badges import compute_maint_badges

    request.state.nav_maint_badges = await compute_maint_badges(session, user)


async def get_current_user(request: Request) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        if not hasattr(request.state, "nav_buildings"):
            apply_nav_state(request, {})
        return None
    user = getattr(request.state, "current_user", None)
    if user is None and not getattr(request.state, "_current_user_loaded", False):
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(
                    User.id == user_id,
                    User.is_active == True,
                    User.is_approved == True,
                )
            )
            user = result.scalar_one_or_none()
            request.state._current_user_loaded = True
            if user is not None:
                request.state.current_user = user
    if not hasattr(request.state, "nav_buildings"):
        path = request.url.path or ""
        if user and not path.startswith("/eq/") and not path.startswith("/hs/"):
            now = time.monotonic()
            async with AsyncSessionLocal() as session:
                if now - _nav_cache["at"] < _NAV_CACHE_TTL_SEC:
                    apply_nav_state(request, _nav_cache)
                else:
                    apply_nav_state(request, await _load_nav_state(session))
                await apply_maint_nav_badges(request, session, user)
        else:
            apply_nav_state(request, {})
    elif user and not hasattr(request.state, "nav_maint_badges"):
        async with AsyncSessionLocal() as session:
            await apply_maint_nav_badges(request, session, user)
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
