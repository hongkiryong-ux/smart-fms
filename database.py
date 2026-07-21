# database.py
import os
from urllib.parse import unquote, urlparse

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base

_RAW_DATABASE_URL = (
    os.environ.get("DATABASE_INTERNAL_URL", "").strip()
    or os.environ.get("DATABASE_URL", "").strip()
    or "sqlite+aiosqlite:///./smart_fms.db"
)


def _parse_postgres_url(raw: str) -> dict:
    url = raw
    if url.startswith("postgresql+psycopg://"):
        url = url.replace("postgresql+psycopg://", "postgresql://", 1)
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    parsed = urlparse(url)
    host = parsed.hostname or ""
    internal = host.split(".")[0] if host.startswith("dpg-") and "." in host else host
    return {
        "external_host": host,
        "internal_host": internal,
        "port": parsed.port or 5432,
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "dbname": (parsed.path or "/").lstrip("/") or "postgres",
    }


def _create_engine():
    raw = _RAW_DATABASE_URL
    lower = raw.lower()

    if lower.startswith("sqlite"):
        return create_async_engine(raw, echo=False)

    if "postgres" not in lower:
        return create_async_engine(raw, echo=False)

    pg = _parse_postgres_url(raw)
    print(
        f"[db] external={pg['external_host']} internal={pg['internal_host']}",
        flush=True,
    )

    async def _connect():
        import psycopg

        attempts: list[tuple[str, str]] = []
        if pg["internal_host"] != pg["external_host"]:
            attempts.append((pg["internal_host"], "prefer"))
        attempts.append((pg["external_host"], "require"))
        if pg["internal_host"] != pg["external_host"]:
            attempts.append((pg["external_host"], "prefer"))

        last_err: Exception | None = None
        for host, sslmode in attempts:
            try:
                print(f"[db] connect try host={host} sslmode={sslmode}", flush=True)
                return await psycopg.AsyncConnection.connect(
                    host=host,
                    port=pg["port"],
                    user=pg["user"],
                    password=pg["password"],
                    dbname=pg["dbname"],
                    sslmode=sslmode,
                    connect_timeout=15,
                )
            except Exception as e:
                last_err = e
                print(f"[db] failed host={host} sslmode={sslmode}: {e}", flush=True)

        try:
            import asyncpg

            for host, _ in attempts[:2]:
                print(f"[db] asyncpg fallback host={host} ssl=require", flush=True)
                return await asyncpg.connect(
                    host=host,
                    port=pg["port"],
                    user=pg["user"],
                    password=pg["password"],
                    database=pg["dbname"],
                    ssl="require",
                    timeout=15,
                )
        except Exception as e:
            print(f"[db] asyncpg fallback failed: {e}", flush=True)
            if last_err:
                raise last_err from e
            raise

        raise last_err  # type: ignore[misc]

    return create_async_engine(
        "postgresql+psycopg://",
        async_creator=_connect,
        pool_pre_ping=True,
        pool_recycle=3600,
    )


DATABASE_URL = _RAW_DATABASE_URL
engine = _create_engine()

AsyncSessionLocal = async_sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()


async def ensure_schema_updates() -> None:
    """기존 DB에 새 컬럼 추가(마이그레이션 없이 운영할 때)."""
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError, ProgrammingError

    url = (_RAW_DATABASE_URL or "").lower()
    is_pg = "postgresql" in url or "postgres" in url

    async with engine.begin() as conn:
        if is_pg:
            await conn.execute(
                text(
                    "ALTER TABLE equipment ADD COLUMN IF NOT EXISTS category VARCHAR(20) DEFAULT '설비'"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE equipment_types ADD COLUMN IF NOT EXISTS category VARCHAR(20) DEFAULT '설비'"
                )
            )
            await conn.execute(
                text("UPDATE equipment SET category = '설비' WHERE category IS NULL")
            )
            await conn.execute(
                text(
                    "UPDATE equipment_types SET category = '설비' WHERE category IS NULL"
                )
            )
        else:
            for stmt in (
                "ALTER TABLE equipment ADD COLUMN category VARCHAR(20) DEFAULT '설비'",
                "ALTER TABLE equipment_types ADD COLUMN category VARCHAR(20) DEFAULT '설비'",
            ):
                try:
                    await conn.execute(text(stmt))
                except OperationalError:
                    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
