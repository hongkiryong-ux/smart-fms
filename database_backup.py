# -*- coding: utf-8 -*-
"""Render PostgreSQL DB를 pg_dump custom 형식으로 안전하게 백업."""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


class DatabaseBackupError(RuntimeError):
    """DB 백업 생성 실패."""


def _database_url() -> str:
    raw = (
        os.environ.get("DATABASE_INTERNAL_URL", "").strip()
        or os.environ.get("DATABASE_URL", "").strip()
    )
    if not raw:
        raise DatabaseBackupError("DATABASE_URL이 설정되지 않았습니다.")
    return raw


def _parse_postgres_url(raw: str) -> dict[str, str | int]:
    normalized = re.sub(
        r"^postgres(?:ql)?(?:\+\w+)?://",
        "postgresql://",
        raw,
        count=1,
        flags=re.IGNORECASE,
    )
    parsed = urlparse(normalized)
    if parsed.scheme != "postgresql" or not parsed.hostname:
        raise DatabaseBackupError("현재 DB는 PostgreSQL이 아닙니다.")
    query = parse_qs(parsed.query)
    return {
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "dbname": unquote((parsed.path or "/").lstrip("/")) or "postgres",
        "sslmode": str((query.get("sslmode") or ["prefer"])[0]),
    }


def _pg_dump_version(path: str) -> int | None:
    try:
        output = subprocess.check_output(
            [path, "--version"],
            text=True,
            stderr=subprocess.STDOUT,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"(\d+)(?:\.\d+)?", output)
    return int(match.group(1)) if match else None


def _server_version(parts: dict[str, str | int]) -> int:
    try:
        import psycopg

        with psycopg.connect(
            host=str(parts["host"]),
            port=int(parts["port"]),
            user=str(parts["user"]),
            password=str(parts["password"]),
            dbname=str(parts["dbname"]),
            sslmode=str(parts["sslmode"]),
            connect_timeout=15,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SHOW server_version_num")
                value = int(cursor.fetchone()[0])
                return value // 10000
    except Exception as exc:
        raise DatabaseBackupError("PostgreSQL 버전을 확인할 수 없습니다.") from exc


def _select_pg_dump(server_major: int) -> tuple[str, int]:
    candidates: list[str] = []
    default = shutil.which("pg_dump")
    if default:
        candidates.append(default)
    candidates.extend(glob.glob("/usr/lib/postgresql/*/bin/pg_dump"))

    available: list[tuple[int, str]] = []
    seen: set[str] = set()
    for candidate in candidates:
        resolved = str(Path(candidate).resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        major = _pg_dump_version(resolved)
        if major is not None:
            available.append((major, resolved))

    compatible = sorted((item for item in available if item[0] >= server_major))
    if compatible:
        major, path = compatible[0]
        return path, major
    versions = ", ".join(str(v) for v, _ in sorted(available)) or "없음"
    raise DatabaseBackupError(
        f"PostgreSQL {server_major} 백업용 pg_dump가 없습니다. "
        f"서버에 설치된 버전: {versions}"
    )


def create_postgres_dump(destination: str | Path) -> dict[str, int]:
    """전체 PostgreSQL DB를 복원 가능한 custom-format 파일로 생성."""
    path = Path(destination)
    parts = _parse_postgres_url(_database_url())
    server_major = _server_version(parts)
    pg_dump, client_major = _select_pg_dump(server_major)

    env = os.environ.copy()
    env.update(
        {
            "PGPASSWORD": str(parts["password"]),
            "PGSSLMODE": str(parts["sslmode"]),
            "PGAPPNAME": "smart-fms-web-backup",
        }
    )
    command = [
        pg_dump,
        "--host",
        str(parts["host"]),
        "--port",
        str(parts["port"]),
        "--username",
        str(parts["user"]),
        "--dbname",
        str(parts["dbname"]),
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--file",
        str(path),
    ]
    try:
        completed = subprocess.run(
            command,
            env=env,
            capture_output=True,
            text=True,
            timeout=60 * 30,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        path.unlink(missing_ok=True)
        raise DatabaseBackupError("DB 백업 생성 제한시간(30분)을 초과했습니다.") from exc
    except OSError as exc:
        path.unlink(missing_ok=True)
        raise DatabaseBackupError("pg_dump 실행에 실패했습니다.") from exc

    if completed.returncode != 0 or not path.is_file() or path.stat().st_size <= 0:
        path.unlink(missing_ok=True)
        message = (completed.stderr or completed.stdout or "알 수 없는 오류").strip()
        password = str(parts["password"])
        if password:
            message = message.replace(password, "***")
        raise DatabaseBackupError(f"DB 백업 생성 실패: {message[:500]}")

    return {
        "size": path.stat().st_size,
        "server_major": server_major,
        "client_major": client_major,
    }
