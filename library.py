"""자료실 — 하위메뉴(시스템관리자 관리) · 폴더 · 자료 등록/다운로드."""
from __future__ import annotations

import mimetypes
from datetime import datetime, timedelta, timezone
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


LIB_MANAGE_URL = "/admin/library?manage=1"


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

_KST = timezone(timedelta(hours=9))

_FILE_TYPES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("pdf",), "PDF 문서", "pdf"),
    (("xls", "xlsx", "xlsm", "csv"), "Excel 문서", "xls"),
    (("doc", "docx"), "Word 문서", "doc"),
    (("hwp", "hwpx"), "한글 문서", "hwp"),
    (("ppt", "pptx"), "PowerPoint 문서", "ppt"),
    (("jpg", "jpeg", "png", "gif", "webp", "bmp"), "이미지", "img"),
    (("dwg", "dxf"), "CAD 도면", "cad"),
    (("zip", "7z", "rar"), "압축 파일", "zip"),
    (("txt", "log"), "텍스트", "txt"),
)


def _fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_KST).strftime("%Y-%m-%d %H:%M")


def _file_type(name: str) -> tuple[str, str, str]:
    """(표시 유형, 아이콘 종류, 확장자)."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    for exts, label, kind in _FILE_TYPES:
        if ext in exts:
            return label, kind, ext
    return (f"{ext.upper()} 파일" if ext else "파일"), "etc", ext


def _latest(*values: datetime | None) -> datetime | None:
    vals = [v for v in values if v is not None]
    return max(vals) if vals else None


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


# ---------------------------------------------------------------- 자료실 홈

def _file_item(user: User, f: LibraryFile, cat_id: int, location: str = "") -> dict:
    label, kind, ext = _file_type(f.original_name)
    return {
        "kind": "file",
        "id": f.id,
        "name": f.original_name,
        "url": f"/admin/library/file/{f.id}?inline=1",
        "download_url": f"/admin/library/file/{f.id}",
        "modified": _fmt_dt(f.created_at),
        "sort_ts": f.created_at.timestamp() if f.created_at else 0,
        "type": label,
        "icon": kind,
        "ext": ext[:4].upper(),
        "size": f.file_size or 0,
        "size_label": _fmt_size(f.file_size),
        "note": f.note or "",
        "by": f.uploaded_by or "",
        "location": location,
        "can_delete": _can_delete_file(user, f),
    }


async def _render_explorer(
    request: Request,
    db: AsyncSession,
    user: User,
    *,
    cat: LibraryCategory | None = None,
    current: LibraryFolder | None = None,
    q: str = "",
):
    """윈도우 탐색기형 자료실 — 왼쪽 폴더 트리 · 오른쪽 목록 (홈/하위메뉴/폴더 공용)."""
    cats = (
        await db.execute(select(LibraryCategory).order_by(LibraryCategory.sort_order, LibraryCategory.id))
    ).scalars().all()
    all_folders = (
        await db.execute(select(LibraryFolder).order_by(LibraryFolder.name, LibraryFolder.id))
    ).scalars().all()
    folder_by_id = {f.id: f for f in all_folders}
    children: dict[tuple[int, int | None], list[LibraryFolder]] = {}
    for f in all_folders:
        children.setdefault((f.category_id, f.parent_id), []).append(f)

    folder_counts: dict[int, int] = {}
    for f in all_folders:
        folder_counts[f.category_id] = folder_counts.get(f.category_id, 0) + 1
    file_rows = (
        await db.execute(
            select(LibraryFile.folder_id, func.count(LibraryFile.id), func.max(LibraryFile.created_at))
            .group_by(LibraryFile.folder_id)
        )
    ).all()
    files_in_folder = {int(fid): int(n) for fid, n, _ in file_rows}
    latest_in_folder = {int(fid): ts for fid, _, ts in file_rows}
    file_counts: dict[int, int] = {}
    latest_in_cat: dict[int, datetime | None] = {}
    for fid, n in files_in_folder.items():
        fd = folder_by_id.get(fid)
        if fd is None:
            continue
        file_counts[fd.category_id] = file_counts.get(fd.category_id, 0) + n
        latest_in_cat[fd.category_id] = _latest(latest_in_cat.get(fd.category_id), latest_in_folder.get(fid))

    path_ids: set[int] = set()
    breadcrumb = await _breadcrumb(db, current) if current else []
    path_ids.update(b.id for b in breadcrumb)

    def folder_url(f: LibraryFolder) -> str:
        return _folder_url(f.category_id, f.id)

    def tree_nodes(cat_id: int, parent_id: int | None, depth: int) -> list[dict]:
        out = []
        for f in children.get((cat_id, parent_id), []):
            if depth > 30:
                break
            out.append(
                {
                    "name": f.name,
                    "url": folder_url(f),
                    "active": current is not None and f.id == current.id,
                    "open": f.id in path_ids,
                    "children": tree_nodes(cat_id, f.id, depth + 1),
                }
            )
        return out

    tree = [
        {
            "name": c.name,
            "url": f"/admin/library/category/{c.id}",
            "active": cat is not None and c.id == cat.id and current is None,
            "open": cat is not None and c.id == cat.id,
            "section": True,
            "children": tree_nodes(c.id, None, 0),
        }
        for c in cats
    ]

    def folder_item(f: LibraryFolder) -> dict:
        sub = len(children.get((f.category_id, f.id), []))
        nfiles = files_in_folder.get(f.id, 0)
        return {
            "kind": "folder",
            "id": f.id,
            "name": f.name,
            "url": folder_url(f),
            "modified": _fmt_dt(_latest(f.created_at, latest_in_folder.get(f.id))),
            "sort_ts": (_latest(f.created_at, latest_in_folder.get(f.id)) or datetime(1970, 1, 1)).timestamp(),
            "type": "폴더",
            "icon": "folder",
            "size": -1,
            "size_label": "-",
            "meta": (f"폴더 {sub} · " if sub else "") + f"자료 {nfiles}",
            "by": f.created_by or "",
            "can_manage": _can_manage_folder(user, f),
            "can_delete": can_delete(user) or (_can_manage_folder(user, f) and not sub and not nfiles),
        }

    def folder_path(fid: int) -> str:
        names, seen, cur = [], set(), folder_by_id.get(fid)
        while cur is not None and cur.id not in seen:
            seen.add(cur.id)
            names.append(cur.name)
            cur = folder_by_id.get(cur.parent_id) if cur.parent_id else None
        return " › ".join(reversed(names))

    query = (q or "").strip()
    items: list[dict] = []
    if query:
        like = f"%{query}%"
        stmt = (
            select(LibraryFile, LibraryFolder)
            .join(LibraryFolder, LibraryFolder.id == LibraryFile.folder_id)
            .where(or_(LibraryFile.original_name.ilike(like), LibraryFile.note.ilike(like)))
            .order_by(LibraryFile.created_at.desc())
            .limit(300)
        )
        if cat is not None:
            stmt = stmt.where(LibraryFolder.category_id == cat.id)
        cat_names = {c.id: c.name for c in cats}
        for f, fd in (await db.execute(stmt)).all():
            loc = f"{cat_names.get(fd.category_id, '')} › {folder_path(fd.id)}"
            item = _file_item(user, f, fd.category_id, loc)
            item["location_url"] = folder_url(fd)
            items.append(item)
        heading = f"「{query}」 검색 결과"
        summary = f"{len(items)}개 파일"
    elif cat is None:
        for c in cats:
            items.append(
                {
                    "kind": "category",
                    "id": c.id,
                    "name": c.name,
                    "url": f"/admin/library/category/{c.id}",
                    "modified": _fmt_dt(_latest(c.created_at, latest_in_cat.get(c.id))),
                    "sort_ts": (_latest(c.created_at, latest_in_cat.get(c.id)) or datetime(1970, 1, 1)).timestamp(),
                    "type": "폴더",
                    "icon": "folder",
                    "size": -1,
                    "size_label": "-",
                    "meta": f"폴더 {folder_counts.get(c.id, 0)} · 자료 {file_counts.get(c.id, 0)}",
                    "can_delete": False,
                }
            )
        heading = "자료실"
        summary = f"{len(items)}개 폴더"
    else:
        for f in children.get((cat.id, current.id if current else None), []):
            items.append(folder_item(f))
        nfold = len(items)
        if current is not None:
            for f in (
                await db.execute(
                    select(LibraryFile)
                    .where(LibraryFile.folder_id == current.id)
                    .order_by(LibraryFile.original_name, LibraryFile.id)
                )
            ).scalars().all():
                items.append(_file_item(user, f, cat.id))
        nfile = len(items) - nfold
        heading = current.name if current else cat.name
        summary = f"{nfold}개 폴더" + (f" · {nfile}개 파일" if current is not None else "")

    address = [{"name": "자료실", "url": "/admin/library"}]
    if cat is not None:
        address.append({"name": cat.name, "url": f"/admin/library/category/{cat.id}"})
        for b in breadcrumb:
            address.append({"name": b.name, "url": folder_url(b)})
    if current is not None:
        up_url = _folder_url(cat.id, current.parent_id)
    elif cat is not None:
        up_url = "/admin/library"
    else:
        up_url = ""

    here_url = address[-1]["url"]
    back_url = here_url
    if query:
        back_url += ("&" if "?" in here_url else "?") + f"q={quote(query)}"
    return templates.TemplateResponse(
        request,
        "library_explorer.html",
        {
            "user": user,
            "category": cat,
            "current": current,
            "tree": tree,
            "items": items,
            "heading": heading,
            "summary": summary,
            "address": address,
            "up_url": up_url,
            "here_url": here_url,
            "back_url": back_url,
            "query": query,
            "categories": cats,
            "folder_counts": folder_counts,
            "file_counts": file_counts,
            "is_admin": _is_admin(user),
            "can_create": can_create(user),
            "can_manage_current": current is not None and _can_manage_folder(user, current),
            "max_file_mb": MAX_FILE_BYTES // (1024 * 1024),
            "max_files": MAX_FILES_PER_UPLOAD,
            "flash_message": request.query_params.get("message", ""),
            "flash_error": request.query_params.get("error", ""),
        },
    )


@router.get("/admin/library")
async def library_home(
    request: Request,
    q: str = "",
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    return await _render_explorer(request, db, user, q=q)


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
        return _back(LIB_MANAGE_URL, error="하위메뉴 이름을 입력하세요.")
    max_order = (await db.execute(select(func.max(LibraryCategory.sort_order)))).scalar() or 0
    db.add(LibraryCategory(name=nm, sort_order=int(max_order) + 1, created_by=_person(user)))
    await db.commit()
    invalidate_nav_cache()
    return _back(LIB_MANAGE_URL, message=f"하위메뉴 「{nm}」을 추가했습니다.")


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
        return _back(LIB_MANAGE_URL, error="하위메뉴 이름을 입력하세요.")
    cat.name = nm
    await db.commit()
    invalidate_nav_cache()
    return _back(LIB_MANAGE_URL, message="하위메뉴 이름을 변경했습니다.")


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
    return _back(LIB_MANAGE_URL)


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
    return _back(LIB_MANAGE_URL, message=f"하위메뉴 「{name}」을 삭제했습니다.")


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
    return await _render_explorer(request, db, user, cat=cat, current=current, q=q)


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
    stay: int = Form(0),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    folder = await _get_folder(db, folder_id)
    if not _can_manage_folder(user, folder):
        raise HTTPException(403, "폴더 이름을 바꿀 권한이 없습니다.")
    nm = _clean_name(name, 200)
    back = _folder_url(folder.category_id, folder.id if stay else folder.parent_id)
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


@router.post("/admin/library/bulk-delete")
async def library_bulk_delete(
    folder_ids: list[int] = Form(default=[]),
    file_ids: list[int] = Form(default=[]),
    back: str = Form("/admin/library"),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    """탐색기 체크박스 선택 삭제 — 항목별 권한은 개별 삭제와 동일."""
    back_url = back if back.startswith("/admin/library") else "/admin/library"
    deleted = 0
    skipped: list[str] = []
    for fid in dict.fromkeys(file_ids):
        f = await db.get(LibraryFile, fid)
        if f is None:
            continue
        if not _can_delete_file(user, f):
            skipped.append(f.original_name)
            continue
        await db.delete(f)
        deleted += 1
    for fid in dict.fromkeys(folder_ids):
        folder = await db.get(LibraryFolder, fid)
        if folder is None:
            continue
        ids = await _descendant_folder_ids(db, [folder.id])
        has_content = len(ids) > 1 or bool(
            (await db.execute(select(func.count(LibraryFile.id)).where(LibraryFile.folder_id == folder.id))).scalar()
        )
        if not (can_delete(user) or (_can_manage_folder(user, folder) and not has_content)):
            skipped.append(folder.name)
            continue
        await _delete_folders(db, ids)
        deleted += 1
    await db.commit()
    if skipped:
        return _back(back_url, error=f"{deleted}개 삭제 · 권한이 없어 제외: {', '.join(skipped[:5])}")
    if not deleted:
        return _back(back_url, error="삭제할 항목을 선택하세요.")
    return _back(back_url, message=f"{deleted}개 항목을 삭제했습니다.")


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
