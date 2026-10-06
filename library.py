"""자료실 — 하위메뉴(시스템관리자 관리) · 폴더 · 자료 등록/다운로드."""
from __future__ import annotations

import mimetypes
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app_templates import templates
from auth import can_create, can_delete, can_edit, invalidate_nav_cache, require_login
from database import AsyncSessionLocal, get_db
from models import AppSetting, LibraryCategory, LibraryFile, LibraryFolder, User, UserRole

router = APIRouter()

MAX_FILE_BYTES = 30 * 1024 * 1024
MAX_FILES_PER_UPLOAD = 20
_INLINE_TYPES = ("image/", "application/pdf", "text/plain")


def _is_admin(user: User | None) -> bool:
    return user is not None and user.role == UserRole.system_admin


def _person(user: User) -> str:
    return ((user.name or "").strip() or (user.username or "").strip())[:100]


def _require_admin(user: User) -> None:
    if not _is_admin(user):
        raise HTTPException(403, "시스템관리자만 하위메뉴를 관리할 수 있습니다.")


def _back(url: str, *, message: str = "", error: str = "") -> RedirectResponse:
    sep = "&" if "?" in url else "?"
    if message:
        url = f"{url}{sep}message={quote(message)}"
    elif error:
        url = f"{url}{sep}error={quote(error)}"
    return RedirectResponse(url, status_code=303)


def _folder_url(category_id: int, folder_id: int | None = None) -> str:
    base = f"/admin/library/category/{category_id}"
    return f"{base}?folder={folder_id}" if folder_id else base


def _clean_name(raw: str, limit: int) -> str:
    return " ".join((raw or "").split())[:limit]


def _fmt_size(n: int | None) -> str:
    n = int(n or 0)
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f}MB"
    if n >= 1024:
        return f"{n / 1024:.0f}KB"
    return f"{n}B"


templates.env.filters.setdefault("lib_size", _fmt_size)


def _can_manage_folder(user: User, folder: LibraryFolder) -> bool:
    return can_edit(user) or (folder.created_by_user_id is not None and folder.created_by_user_id == user.id)


def _can_delete_file(user: User, f: LibraryFile) -> bool:
    return can_delete(user) or (f.uploaded_by_user_id is not None and f.uploaded_by_user_id == user.id)


async def _get_category(db: AsyncSession, category_id: int) -> LibraryCategory:
    cat = await db.get(LibraryCategory, category_id)
    if not cat:
        raise HTTPException(404, "하위메뉴를 찾을 수 없습니다.")
    return cat


async def _get_folder(db: AsyncSession, folder_id: int) -> LibraryFolder:
    folder = await db.get(LibraryFolder, folder_id)
    if not folder:
        raise HTTPException(404, "폴더를 찾을 수 없습니다.")
    return folder


async def _breadcrumb(db: AsyncSession, folder: LibraryFolder | None) -> list[LibraryFolder]:
    chain: list[LibraryFolder] = []
    seen: set[int] = set()
    cur = folder
    while cur is not None and cur.id not in seen:
        seen.add(cur.id)
        chain.append(cur)
        cur = await db.get(LibraryFolder, cur.parent_id) if cur.parent_id else None
    return list(reversed(chain))


async def _descendant_folder_ids(db: AsyncSession, root_ids: list[int]) -> list[int]:
    out = list(root_ids)
    frontier = list(root_ids)
    while frontier:
        rows = (
            await db.execute(select(LibraryFolder.id).where(LibraryFolder.parent_id.in_(frontier)))
        ).scalars().all()
        frontier = [r for r in rows if r not in out]
        out.extend(frontier)
    return out


async def _delete_folders(db: AsyncSession, folder_ids: list[int]) -> None:
    if not folder_ids:
        return
    await db.execute(delete(LibraryFile).where(LibraryFile.folder_id.in_(folder_ids)))
    await db.execute(
        update(LibraryFolder).where(LibraryFolder.id.in_(folder_ids)).values(parent_id=None)
    )
    await db.execute(delete(LibraryFolder).where(LibraryFolder.id.in_(folder_ids)))


async def _folder_stats(db: AsyncSession, folder_ids: list[int]) -> dict[int, dict]:
    """폴더별 직속 하위폴더 수·파일 수."""
    stats = {fid: {"folders": 0, "files": 0} for fid in folder_ids}
    if not folder_ids:
        return stats
    for pid, n in (
        await db.execute(
            select(LibraryFolder.parent_id, func.count(LibraryFolder.id))
            .where(LibraryFolder.parent_id.in_(folder_ids))
            .group_by(LibraryFolder.parent_id)
        )
    ).all():
        stats[int(pid)]["folders"] = int(n)
    for fid, n in (
        await db.execute(
            select(LibraryFile.folder_id, func.count(LibraryFile.id))
            .where(LibraryFile.folder_id.in_(folder_ids))
            .group_by(LibraryFile.folder_id)
        )
    ).all():
        stats[int(fid)]["files"] = int(n)
    return stats


# ---------------------------------------------------------------- 자료실 홈

@router.get("/admin/library")
async def library_home(
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    cats = (
        await db.execute(select(LibraryCategory).order_by(LibraryCategory.sort_order, LibraryCategory.id))
    ).scalars().all()
    folder_counts = dict(
        (
            await db.execute(
                select(LibraryFolder.category_id, func.count(LibraryFolder.id)).group_by(LibraryFolder.category_id)
            )
        ).all()
    )
    file_counts = dict(
        (
            await db.execute(
                select(LibraryFolder.category_id, func.count(LibraryFile.id))
                .join(LibraryFile, LibraryFile.folder_id == LibraryFolder.id)
                .group_by(LibraryFolder.category_id)
            )
        ).all()
    )
    return templates.TemplateResponse(
        request,
        "library.html",
        {
            "user": user,
            "categories": cats,
            "folder_counts": folder_counts,
            "file_counts": file_counts,
            "is_admin": _is_admin(user),
            "flash_message": request.query_params.get("message", ""),
            "flash_error": request.query_params.get("error", ""),
        },
    )


# ---------------------------------------------------------------- 하위메뉴 (시스템관리자)

@router.post("/admin/library/categories")
async def library_category_create(
    name: str = Form(...),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(user)
    nm = _clean_name(name, 100)
    if not nm:
        return _back("/admin/library", error="하위메뉴 이름을 입력하세요.")
    max_order = (await db.execute(select(func.max(LibraryCategory.sort_order)))).scalar() or 0
    db.add(LibraryCategory(name=nm, sort_order=int(max_order) + 1, created_by=_person(user)))
    await db.commit()
    invalidate_nav_cache()
    return _back("/admin/library", message=f"하위메뉴 「{nm}」을 추가했습니다.")


@router.post("/admin/library/categories/{category_id}/rename")
async def library_category_rename(
    category_id: int,
    name: str = Form(...),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(user)
    cat = await _get_category(db, category_id)
    nm = _clean_name(name, 100)
    if not nm:
        return _back("/admin/library", error="하위메뉴 이름을 입력하세요.")
    cat.name = nm
    await db.commit()
    invalidate_nav_cache()
    return _back("/admin/library", message="하위메뉴 이름을 변경했습니다.")


@router.post("/admin/library/categories/{category_id}/move")
async def library_category_move(
    category_id: int,
    direction: str = Form(...),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(user)
    cats = list(
        (
            await db.execute(select(LibraryCategory).order_by(LibraryCategory.sort_order, LibraryCategory.id))
        ).scalars().all()
    )
    idx = next((i for i, c in enumerate(cats) if c.id == category_id), None)
    if idx is None:
        raise HTTPException(404, "하위메뉴를 찾을 수 없습니다.")
    swap = idx - 1 if direction == "up" else idx + 1
    if 0 <= swap < len(cats):
        cats[idx], cats[swap] = cats[swap], cats[idx]
        for order, c in enumerate(cats, start=1):
            c.sort_order = order
        await db.commit()
        invalidate_nav_cache()
    return _back("/admin/library")


@router.post("/admin/library/categories/{category_id}/delete")
async def library_category_delete(
    category_id: int,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(user)
    cat = await _get_category(db, category_id)
    folder_ids = list(
        (await db.execute(select(LibraryFolder.id).where(LibraryFolder.category_id == cat.id))).scalars().all()
    )
    await _delete_folders(db, folder_ids)
    name = cat.name
    await db.delete(cat)
    await db.commit()
    invalidate_nav_cache()
    return _back("/admin/library", message=f"하위메뉴 「{name}」을 삭제했습니다.")


# ---------------------------------------------------------------- 하위메뉴 화면 (폴더·자료)

@router.get("/admin/library/category/{category_id}")
async def library_category_view(
    category_id: int,
    request: Request,
    folder: int = 0,
    q: str = "",
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    cat = await _get_category(db, category_id)
    current = None
    if folder:
        current = await db.get(LibraryFolder, folder)
        if current is None or current.category_id != cat.id:
            return RedirectResponse(_folder_url(cat.id), status_code=303)

    parent_cond = LibraryFolder.parent_id == current.id if current else LibraryFolder.parent_id.is_(None)
    subfolders = (
        await db.execute(
            select(LibraryFolder)
            .where(LibraryFolder.category_id == cat.id, parent_cond)
            .order_by(LibraryFolder.name, LibraryFolder.id)
        )
    ).scalars().all()
    stats = await _folder_stats(db, [f.id for f in subfolders])

    files: list[LibraryFile] = []
    if current is not None:
        files = list(
            (
                await db.execute(
                    select(LibraryFile)
                    .where(LibraryFile.folder_id == current.id)
                    .order_by(LibraryFile.created_at.desc(), LibraryFile.id.desc())
                )
            ).scalars().all()
        )

    query = (q or "").strip()
    search_results: list[tuple[LibraryFile, LibraryFolder]] = []
    if query:
        like = f"%{query}%"
        search_results = list(
            (
                await db.execute(
                    select(LibraryFile, LibraryFolder)
                    .join(LibraryFolder, LibraryFolder.id == LibraryFile.folder_id)
                    .where(
                        LibraryFolder.category_id == cat.id,
                        or_(LibraryFile.original_name.ilike(like), LibraryFile.note.ilike(like)),
                    )
                    .order_by(LibraryFile.created_at.desc())
                    .limit(200)
                )
            ).all()
        )

    return templates.TemplateResponse(
        request,
        "library_category.html",
        {
            "user": user,
            "category": cat,
            "current": current,
            "breadcrumb": await _breadcrumb(db, current),
            "subfolders": subfolders,
            "folder_stats": stats,
            "files": files,
            "query": query,
            "search_results": search_results,
            "can_create": can_create(user),
            "can_manage_folder": (lambda f: _can_manage_folder(user, f)),
            "can_delete_file": (lambda f: _can_delete_file(user, f)),
            "max_file_mb": MAX_FILE_BYTES // (1024 * 1024),
            "max_files": MAX_FILES_PER_UPLOAD,
            "flash_message": request.query_params.get("message", ""),
            "flash_error": request.query_params.get("error", ""),
        },
    )


@router.post("/admin/library/category/{category_id}/folders")
async def library_folder_create(
    category_id: int,
    name: str = Form(...),
    parent_id: int = Form(0),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    cat = await _get_category(db, category_id)
    if not can_create(user):
        raise HTTPException(403, "폴더를 만들 권한이 없습니다.")
    parent = None
    if parent_id:
        parent = await _get_folder(db, parent_id)
        if parent.category_id != cat.id:
            raise HTTPException(400, "잘못된 상위 폴더입니다.")
    nm = _clean_name(name, 200)
    back = _folder_url(cat.id, parent.id if parent else None)
    if not nm:
        return _back(back, error="폴더 이름을 입력하세요.")
    db.add(
        LibraryFolder(
            category_id=cat.id,
            parent_id=parent.id if parent else None,
            name=nm,
            created_by=_person(user),
            created_by_user_id=user.id,
        )
    )
    await db.commit()
    return _back(back, message=f"폴더 「{nm}」을 만들었습니다.")


@router.post("/admin/library/folder/{folder_id}/rename")
async def library_folder_rename(
    folder_id: int,
    name: str = Form(...),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    folder = await _get_folder(db, folder_id)
    if not _can_manage_folder(user, folder):
        raise HTTPException(403, "폴더 이름을 바꿀 권한이 없습니다.")
    nm = _clean_name(name, 200)
    back = _folder_url(folder.category_id, folder.parent_id)
    if not nm:
        return _back(back, error="폴더 이름을 입력하세요.")
    folder.name = nm
    await db.commit()
    return _back(back, message="폴더 이름을 변경했습니다.")


@router.post("/admin/library/folder/{folder_id}/delete")
async def library_folder_delete(
    folder_id: int,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    folder = await _get_folder(db, folder_id)
    back = _folder_url(folder.category_id, folder.parent_id)
    ids = await _descendant_folder_ids(db, [folder.id])
    has_content = len(ids) > 1 or bool(
        (await db.execute(select(func.count(LibraryFile.id)).where(LibraryFile.folder_id == folder.id))).scalar()
    )
    if not (can_delete(user) or (_can_manage_folder(user, folder) and not has_content)):
        return _back(back, error="자료가 들어 있는 폴더는 삭제 권한이 있는 사용자만 삭제할 수 있습니다.")
    name = folder.name
    await _delete_folders(db, ids)
    await db.commit()
    return _back(back, message=f"폴더 「{name}」을 삭제했습니다.")


@router.post("/admin/library/folder/{folder_id}/files")
async def library_file_upload(
    folder_id: int,
    files: list[UploadFile] = File(default=[]),
    note: str = Form(""),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    folder = await _get_folder(db, folder_id)
    if not can_create(user):
        raise HTTPException(403, "자료를 등록할 권한이 없습니다.")
    back = _folder_url(folder.category_id, folder.id)
    memo = (note or "").strip()[:500] or None
    saved = 0
    skipped: list[str] = []
    for up in (files or [])[:MAX_FILES_PER_UPLOAD]:
        name = (getattr(up, "filename", "") or "").strip()
        if not name:
            continue
        data = await up.read(MAX_FILE_BYTES + 1)
        if not data or len(data) > MAX_FILE_BYTES:
            skipped.append(name)
            continue
        ctype = (up.content_type or "").strip() or mimetypes.guess_type(name)[0] or "application/octet-stream"
        db.add(
            LibraryFile(
                folder_id=folder.id,
                original_name=name[-300:],
                content_type=ctype[:150],
                file_data=data,
                file_size=len(data),
                note=memo,
                uploaded_by=_person(user),
                uploaded_by_user_id=user.id,
            )
        )
        saved += 1
    if len(files or []) > MAX_FILES_PER_UPLOAD:
        skipped.append(f"{len(files) - MAX_FILES_PER_UPLOAD}개(한 번에 최대 {MAX_FILES_PER_UPLOAD}개)")
    await db.commit()
    if not saved and not skipped:
        return _back(back, error="등록할 파일을 선택하세요.")
    if skipped:
        limit_mb = MAX_FILE_BYTES // (1024 * 1024)
        return _back(
            back,
            error=f"{saved}개 등록 · 제외: {', '.join(skipped[:5])} (파일당 최대 {limit_mb}MB)",
        )
    return _back(back, message=f"자료 {saved}개를 등록했습니다.")


@router.get("/admin/library/file/{file_id}")
async def library_file_download(
    file_id: int,
    inline: int = 0,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    f = (
        await db.execute(select(LibraryFile).options(undefer(LibraryFile.file_data)).where(LibraryFile.id == file_id))
    ).scalar_one_or_none()
    if not f:
        raise HTTPException(404, "자료를 찾을 수 없습니다.")
    ctype = f.content_type or "application/octet-stream"
    disp = "inline" if inline and ctype.startswith(_INLINE_TYPES) else "attachment"
    stem, dot, ext = f.original_name.rpartition(".")
    ascii_stem = (stem if dot else f.original_name).encode("ascii", "ignore").decode().strip(" ._-")
    ascii_ext = ext.encode("ascii", "ignore").decode() if dot else ""
    ascii_name = (ascii_stem or f"file{f.id}") + (f".{ascii_ext}" if ascii_ext else "")
    ascii_name = ascii_name.replace('"', "")
    headers = {
        "Content-Disposition": f"{disp}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(f.original_name)}",
        "Cache-Control": "private, max-age=3600",
    }
    return Response(content=f.file_data, media_type=ctype, headers=headers)


@router.post("/admin/library/file/{file_id}/delete")
async def library_file_delete(
    file_id: int,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    f = await db.get(LibraryFile, file_id)
    if not f:
        raise HTTPException(404, "자료를 찾을 수 없습니다.")
    folder = await _get_folder(db, f.folder_id)
    if not _can_delete_file(user, f):
        raise HTTPException(403, "자료를 삭제할 권한이 없습니다.")
    name = f.original_name
    await db.delete(f)
    await db.commit()
    return _back(_folder_url(folder.category_id, folder.id), message=f"「{name}」을 삭제했습니다.")


# ---------------------------------------------------------------- 기존 계정 메뉴 권한 보정

_MENU_BACKFILL_KEY = "library.menu_backfill_v1"


async def backfill_library_menu_access() -> None:
    """자료실 메뉴 추가 전 저장된 계정 메뉴 목록에 자료실을 1회 추가 (협력사·외부 제외)."""
    async with AsyncSessionLocal() as session:
        done = await session.get(AppSetting, _MENU_BACKFILL_KEY)
        if done and (done.value or "").strip():
            return
        from auth import menu_access_flags_from_raw, normalize_menu_access

        users = (await session.execute(select(User))).scalars().all()
        changed = 0
        for u in users:
            if u.role in (UserRole.system_admin, UserRole.partner, UserRole.external):
                continue
            if u.menu_access is None:
                continue
            raw = u.menu_access
            keys = normalize_menu_access(raw)
            if "library" in keys:
                continue
            u.menu_access = keys + ["library"] + sorted(menu_access_flags_from_raw(raw))
            changed += 1
        if done is None:
            session.add(AppSetting(key=_MENU_BACKFILL_KEY, value="1"))
        else:
            done.value = "1"
        await session.commit()
        print(f"[library] menu access backfilled: {changed}", flush=True)
