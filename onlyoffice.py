"""OnlyOffice Docs (Community) 연동 헬퍼.

환경변수:
  ONLYOFFICE_URL              브라우저가 접속하는 Document Server URL (예: http://localhost:8080)
  ONLYOFFICE_JWT_SECRET       Document Server JWT_SECRET 과 동일해야 함
  ONLYOFFICE_JWT_ENABLED      기본 true (Community docker 기본값과 맞춤)
  PUBLIC_BASE_URL             외부에서 보이는 FMS URL (예: https://smart-fms.onrender.com)
  ONLYOFFICE_INTERNAL_FILE_URL
      Document Server 컨테이너가 파일/콜백에 쓰는 FMS 주소
      (로컬 Docker: http://host.docker.internal:8000)
"""
from __future__ import annotations

import hashlib
import os
import time
from typing import Any
from urllib.parse import quote

import jwt
from fastapi import Request


def onlyoffice_enabled() -> bool:
    return bool((os.environ.get("ONLYOFFICE_URL") or "").strip())


def onlyoffice_url() -> str:
    return (os.environ.get("ONLYOFFICE_URL") or "").rstrip("/")


def jwt_enabled() -> bool:
    v = (os.environ.get("ONLYOFFICE_JWT_ENABLED") or "true").strip().lower()
    return v in ("1", "true", "yes", "on")


def jwt_secret() -> str:
    return (
        (os.environ.get("ONLYOFFICE_JWT_SECRET") or "").strip()
        or (os.environ.get("APP_SECRET_KEY") or "").strip()
        or "change_this_secret_in_prod"
    )


def token_secret() -> str:
    """파일 다운로드/콜백용 HMAC 시크릿."""
    return jwt_secret() + "|oo-file-token"


def public_base_url(request: Request | None = None) -> str:
    env = (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")
    if env:
        return env
    if request is not None:
        return str(request.base_url).rstrip("/")
    return "http://127.0.0.1:8000"


def ds_reachable_base_url(request: Request | None = None) -> str:
    """Document Server가 FMS에 접근할 때 쓰는 베이스 URL."""
    internal = (os.environ.get("ONLYOFFICE_INTERNAL_FILE_URL") or "").rstrip("/")
    if internal:
        return internal
    return public_base_url(request)


def mint_access_token(
    *,
    building_id: int,
    file_id: int,
    purpose: str,
    user_id: int | None = None,
    can_edit: bool = False,
    ttl_sec: int = 8 * 3600,
) -> str:
    now = int(time.time())
    payload = {
        "bid": building_id,
        "fid": file_id,
        "p": purpose,
        "uid": user_id,
        "edit": bool(can_edit),
        "iat": now,
        "exp": now + int(ttl_sec),
    }
    return jwt.encode(payload, token_secret(), algorithm="HS256")


def verify_access_token(token: str, *, building_id: int, file_id: int, purpose: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, token_secret(), algorithms=["HS256"])
    except jwt.PyJWTError as e:
        raise ValueError(f"토큰이 유효하지 않습니다: {e}") from e
    if int(payload.get("bid") or -1) != int(building_id):
        raise ValueError("건물 정보가 일치하지 않습니다.")
    if int(payload.get("fid") or -1) != int(file_id):
        raise ValueError("파일 정보가 일치하지 않습니다.")
    if (payload.get("p") or "") != purpose:
        raise ValueError("토큰 용도가 일치하지 않습니다.")
    return payload


def document_key(file_id: int, data: bytes) -> str:
    """내용이 바뀌면 키가 바뀌어 OnlyOffice 캐시와 충돌하지 않음."""
    digest = hashlib.sha256(data or b"").hexdigest()[:16]
    # OnlyOffice key: 영숫자 권장
    return f"ilog{int(file_id)}{digest}"


def spreadsheet_file_type(filename: str) -> str:
    name = (filename or "").lower()
    if name.endswith(".xlsx"):
        return "xlsx"
    if name.endswith(".xlsm"):
        return "xlsm"
    if name.endswith(".xls"):
        return "xls"
    if name.endswith(".csv"):
        return "csv"
    if name.endswith(".ods"):
        return "ods"
    return "xlsx"


def sign_config(config: dict[str, Any]) -> dict[str, Any]:
    """OnlyOffice JWT가 켜져 있으면 config에 token 추가."""
    if not jwt_enabled():
        return config
    # 공식 권장: 전체 config를 서명
    token = jwt.encode(config, jwt_secret(), algorithm="HS256")
    out = dict(config)
    out["token"] = token
    return out


def verify_callback_jwt(authorization: str | None, body: dict[str, Any] | None = None) -> None:
    if not jwt_enabled():
        return
    token = None
    if authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1]
        else:
            token = authorization.strip()
    if not token and body:
        token = body.get("token")
    if not token:
        raise ValueError("OnlyOffice JWT가 없습니다.")
    try:
        jwt.decode(token, jwt_secret(), algorithms=["HS256"])
    except jwt.PyJWTError as e:
        raise ValueError(f"OnlyOffice JWT 검증 실패: {e}") from e


def build_editor_config(
    *,
    request: Request,
    building_id: int,
    file_id: int,
    filename: str,
    title: str,
    file_bytes: bytes,
    user_id: int,
    user_name: str,
    can_edit: bool,
) -> dict[str, Any]:
    base_ds = ds_reachable_base_url(request)
    content_token = mint_access_token(
        building_id=building_id,
        file_id=file_id,
        purpose="content",
        user_id=user_id,
        can_edit=can_edit,
    )
    callback_token = mint_access_token(
        building_id=building_id,
        file_id=file_id,
        purpose="callback",
        user_id=user_id,
        can_edit=can_edit,
    )
    file_url = (
        f"{base_ds}/oo/inspection-logs/{building_id}/files/{file_id}/content"
        f"?token={quote(content_token)}"
    )
    callback_url = (
        f"{base_ds}/oo/inspection-logs/{building_id}/files/{file_id}/callback"
        f"?token={quote(callback_token)}"
    )
    ftype = spreadsheet_file_type(filename)
    key = document_key(file_id, file_bytes)
    config: dict[str, Any] = {
        "documentType": "cell",
        "type": "desktop",
        "document": {
            "title": title or filename or "점검일지.xlsx",
            "url": file_url,
            "fileType": ftype,
            "key": key,
            "permissions": {
                "edit": bool(can_edit),
                "download": True,
                "print": True,
                "review": False,
                "comment": False,
                "fillForms": bool(can_edit),
                "modifyFilter": bool(can_edit),
                "modifyContentControl": bool(can_edit),
            },
        },
        "editorConfig": {
            "mode": "edit" if can_edit else "view",
            "lang": "ko",
            "callbackUrl": callback_url,
            "user": {
                "id": str(user_id),
                "name": user_name or f"user-{user_id}",
            },
            "customization": {
                "autosave": True,
                "forcesave": True,
                "compactHeader": True,
                "toolbar": True,
                "comments": False,
                "help": False,
            },
        },
        "height": "100%",
        "width": "100%",
    }
    return sign_config(config)
