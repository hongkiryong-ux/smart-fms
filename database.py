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
    """기존 DB에 새 컬럼/테이블 추가(마이그레이션 없이 운영할 때)."""
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError, OperationalError, ProgrammingError

    import models  # noqa: F401

    url = (_RAW_DATABASE_URL or "").lower()
    is_pg = "postgresql" in url or "postgres" in url

    # 신규 테이블은 별도 트랜잭션으로 확실히 생성
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception as e:
        print(f"[db] create_all warning: {e}", flush=True)

    async def _exec(stmt: str) -> None:
        # 문장마다 독립 트랜잭션 — PG에서 한 문장 실패 시 전체 롤백 방지
        try:
            async with engine.begin() as conn:
                await conn.execute(text(stmt))
        except (OperationalError, ProgrammingError, DBAPIError) as e:
            print(f"[db] schema skip: {e}", flush=True)

    if is_pg:
        await _exec(
            "ALTER TABLE equipment ADD COLUMN IF NOT EXISTS category VARCHAR(50) DEFAULT '설비'"
        )
        await _exec(
            "ALTER TABLE equipment_types ADD COLUMN IF NOT EXISTS category VARCHAR(50) DEFAULT '설비'"
        )
        await _exec(
            "ALTER TABLE equipment ADD COLUMN IF NOT EXISTS extra_data JSONB DEFAULT '{}'::jsonb"
        )
        await _exec(
            "ALTER TABLE equipment ALTER COLUMN category TYPE VARCHAR(50) USING category::varchar(50)"
        )
        await _exec("UPDATE equipment SET category = '설비' WHERE category IS NULL")
        await _exec(
            "UPDATE equipment_types SET category = '설비' WHERE category IS NULL"
        )
        await _exec(
            """
            CREATE TABLE IF NOT EXISTS maintenance_records (
                id SERIAL PRIMARY KEY,
                equipment_id INTEGER NOT NULL REFERENCES equipment(id),
                work_order_id INTEGER REFERENCES work_orders(id),
                title VARCHAR(300) NOT NULL,
                work_date DATE NOT NULL,
                worker_name VARCHAR(100),
                cause TEXT,
                action TEXT,
                parts_used TEXT,
                work_hours DOUBLE PRECISION,
                cost DOUBLE PRECISION,
                note TEXT,
                is_manual BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP WITHOUT TIME ZONE
            )
            """
        )
        await _exec(
            "CREATE INDEX IF NOT EXISTS ix_maintenance_records_equipment_id ON maintenance_records (equipment_id)"
        )
        await _exec(
            "ALTER TABLE work_orders ADD COLUMN IF NOT EXISTS scheduled_date DATE"
        )
        await _exec(
            "ALTER TABLE work_orders ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE"
        )
        await _exec(
            "UPDATE work_orders SET is_active = TRUE WHERE is_active IS NULL"
        )
    else:
        for stmt in (
            "ALTER TABLE equipment ADD COLUMN category VARCHAR(50) DEFAULT '설비'",
            "ALTER TABLE equipment_types ADD COLUMN category VARCHAR(50) DEFAULT '설비'",
            "ALTER TABLE equipment ADD COLUMN extra_data TEXT",
            "ALTER TABLE work_orders ADD COLUMN scheduled_date DATE",
            "ALTER TABLE work_orders ADD COLUMN is_active BOOLEAN DEFAULT 1",
            """
            CREATE TABLE IF NOT EXISTS maintenance_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                equipment_id INTEGER NOT NULL,
                work_order_id INTEGER,
                title VARCHAR(300) NOT NULL,
                work_date DATE NOT NULL,
                worker_name VARCHAR(100),
                cause TEXT,
                action TEXT,
                parts_used TEXT,
                work_hours FLOAT,
                cost FLOAT,
                note TEXT,
                is_manual BOOLEAN DEFAULT 0,
                created_at DATETIME
            )
            """,
        ):
            await _exec(stmt)
        await _exec("UPDATE work_orders SET is_active = 1 WHERE is_active IS NULL")


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
