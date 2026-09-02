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
    pool_size = int(os.environ.get("DB_POOL_SIZE", "8"))
    max_overflow = int(os.environ.get("DB_MAX_OVERFLOW", "12"))
    print(
        f"[db] external={pg['external_host']} internal={pg['internal_host']} "
        f"pool={pool_size}+{max_overflow}",
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
        pool_recycle=300,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=25,
        pool_use_lifo=True,
    )


DATABASE_URL = _RAW_DATABASE_URL
engine = _create_engine()

AsyncSessionLocal = async_sessionmaker(
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,  # Jinja 템플릿이 commit 이후 ORM 속성 접근 시 greenlet 오류 방지
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
            "ALTER TABLE work_orders ADD COLUMN IF NOT EXISTS d1_approved BOOLEAN DEFAULT FALSE"
        )
        await _exec(
            "ALTER TABLE work_orders ADD COLUMN IF NOT EXISTS approved_by VARCHAR(100)"
        )
        await _exec(
            "ALTER TABLE work_orders ADD COLUMN IF NOT EXISTS approved_at TIMESTAMP WITHOUT TIME ZONE"
        )
        await _exec(
            "UPDATE work_orders SET is_active = TRUE WHERE is_active IS NULL"
        )
        await _exec(
            "UPDATE work_orders SET d1_approved = FALSE WHERE d1_approved IS NULL"
        )
        await _exec(
            "ALTER TABLE work_orders ADD COLUMN IF NOT EXISTS approval_requested BOOLEAN DEFAULT FALSE"
        )
        await _exec(
            "ALTER TABLE work_orders ADD COLUMN IF NOT EXISTS approval_requested_by VARCHAR(100)"
        )
        await _exec(
            "ALTER TABLE work_orders ADD COLUMN IF NOT EXISTS approval_requested_at TIMESTAMP WITHOUT TIME ZONE"
        )
        await _exec(
            "ALTER TABLE work_orders ADD COLUMN IF NOT EXISTS work_permitted BOOLEAN DEFAULT FALSE"
        )
        await _exec(
            "ALTER TABLE work_orders ADD COLUMN IF NOT EXISTS work_permitted_by VARCHAR(100)"
        )
        await _exec(
            "ALTER TABLE work_orders ADD COLUMN IF NOT EXISTS work_permitted_at TIMESTAMP WITHOUT TIME ZONE"
        )
        await _exec(
            "UPDATE work_orders SET approval_requested = FALSE WHERE approval_requested IS NULL"
        )
        await _exec(
            "UPDATE work_orders SET work_permitted = FALSE WHERE work_permitted IS NULL"
        )
        await _exec(
            "ALTER TABLE work_orders ADD COLUMN IF NOT EXISTS requester_name VARCHAR(100)"
        )
        await _exec(
            "ALTER TABLE work_orders ADD COLUMN IF NOT EXISTS hazard_content TEXT"
        )
        await _exec(
            "ALTER TABLE work_orders ADD COLUMN IF NOT EXISTS safety_measures TEXT"
        )
        await _exec(
            "ALTER TABLE work_orders ADD COLUMN IF NOT EXISTS risk_grade VARCHAR(20)"
        )
        await _exec(
            "ALTER TABLE partners ADD COLUMN IF NOT EXISTS hazard_content TEXT"
        )
        await _exec(
            "ALTER TABLE partners ADD COLUMN IF NOT EXISTS safety_measures TEXT"
        )
        await _exec(
            "ALTER TABLE partners ADD COLUMN IF NOT EXISTS risk_grade VARCHAR(20)"
        )
        await _exec(
            "ALTER TABLE material_items ADD COLUMN IF NOT EXISTS location VARCHAR(200)"
        )
        await _exec(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS openai_api_key VARCHAR(200)"
        )
        await _exec(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS openai_model VARCHAR(80)"
        )
        await _exec(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_approved BOOLEAN DEFAULT TRUE"
        )
        await _exec(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS can_create BOOLEAN DEFAULT TRUE"
        )
        await _exec(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS can_edit BOOLEAN DEFAULT TRUE"
        )
        await _exec(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS can_delete BOOLEAN DEFAULT TRUE"
        )
        await _exec(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS partner_id INTEGER"
        )
        await _exec(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS company_name VARCHAR(200)"
        )
        await _exec(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS menu_access JSONB"
        )
        await _exec("UPDATE users SET is_approved = TRUE WHERE is_approved IS NULL")
        await _exec("UPDATE users SET can_create = TRUE WHERE can_create IS NULL")
        await _exec("UPDATE users SET can_edit = TRUE WHERE can_edit IS NULL")
        await _exec("UPDATE users SET can_delete = TRUE WHERE can_delete IS NULL")
        await _exec(
            "UPDATE users SET can_create = FALSE, can_edit = FALSE, can_delete = FALSE "
            "WHERE role = 'viewer'"
        )
        await _exec(
            "UPDATE users SET can_create = TRUE, can_edit = TRUE, can_delete = TRUE "
            "WHERE role = 'system_admin'"
        )
        await _exec(
            """
            CREATE TABLE IF NOT EXISTS material_groups (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100) NOT NULL UNIQUE
            )
            """
        )
        await _exec(
            """
            CREATE TABLE IF NOT EXISTS material_items (
                id SERIAL PRIMARY KEY,
                name VARCHAR(200) NOT NULL UNIQUE,
                quantity INTEGER DEFAULT 0,
                spec VARCHAR(300),
                remarks TEXT,
                group_name VARCHAR(100) NOT NULL DEFAULT '소모품',
                location VARCHAR(200),
                created_at TIMESTAMP WITHOUT TIME ZONE,
                updated_at TIMESTAMP WITHOUT TIME ZONE
            )
            """
        )
        await _exec(
            """
            CREATE TABLE IF NOT EXISTS material_logs (
                id SERIAL PRIMARY KEY,
                action VARCHAR(50) NOT NULL,
                name VARCHAR(200) NOT NULL,
                quantity INTEGER DEFAULT 0,
                reason TEXT,
                created_at TIMESTAMP WITHOUT TIME ZONE
            )
            """
        )
        await _exec(
            """
            CREATE TABLE IF NOT EXISTS pm_inspections (
                id SERIAL PRIMARY KEY,
                schedule_id INTEGER NOT NULL REFERENCES pm_schedules(id),
                equipment_id INTEGER NOT NULL REFERENCES equipment(id),
                result VARCHAR(20) NOT NULL DEFAULT 'normal',
                note TEXT,
                inspector_name VARCHAR(100),
                inspected_at TIMESTAMP WITHOUT TIME ZONE,
                work_order_id INTEGER REFERENCES work_orders(id)
            )
            """
        )
        await _exec(
            "CREATE INDEX IF NOT EXISTS ix_pm_inspections_schedule_id ON pm_inspections (schedule_id)"
        )
        await _exec(
            "CREATE INDEX IF NOT EXISTS ix_pm_inspections_equipment_id ON pm_inspections (equipment_id)"
        )
        await _exec(
            "ALTER TABLE floors ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE"
        )
        await _exec("UPDATE floors SET is_active = TRUE WHERE is_active IS NULL")
        await _exec(
            "ALTER TABLE zones ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE"
        )
        await _exec("UPDATE zones SET is_active = TRUE WHERE is_active IS NULL")
        await _exec(
            """
            CREATE TABLE IF NOT EXISTS building_drawings (
                id SERIAL PRIMARY KEY,
                building_id INTEGER NOT NULL REFERENCES buildings(id),
                floor_id INTEGER REFERENCES floors(id),
                title VARCHAR(200) NOT NULL,
                original_name VARCHAR(300),
                stored_name VARCHAR(300) NOT NULL,
                content_type VARCHAR(100),
                created_at TIMESTAMP WITHOUT TIME ZONE
            )
            """
        )
        await _exec(
            "CREATE INDEX IF NOT EXISTS ix_building_drawings_building_id ON building_drawings (building_id)"
        )
        await _exec(
            "CREATE INDEX IF NOT EXISTS ix_building_drawings_floor_id ON building_drawings (floor_id)"
        )
        await _exec(
            "ALTER TABLE building_drawings ADD COLUMN IF NOT EXISTS file_data BYTEA"
        )
        await _exec(
            "ALTER TABLE building_drawings ADD COLUMN IF NOT EXISTS file_size INTEGER"
        )
        await _exec(
            """
            CREATE TABLE IF NOT EXISTS building_standards (
                id SERIAL PRIMARY KEY,
                building_id INTEGER NOT NULL REFERENCES buildings(id),
                title VARCHAR(200) NOT NULL,
                original_name VARCHAR(300),
                stored_name VARCHAR(300) NOT NULL,
                content_type VARCHAR(100),
                file_data BYTEA,
                created_at TIMESTAMP WITHOUT TIME ZONE
            )
            """
        )
        await _exec(
            "CREATE INDEX IF NOT EXISTS ix_building_standards_building_id ON building_standards (building_id)"
        )
        await _exec(
            "ALTER TABLE building_standards ADD COLUMN IF NOT EXISTS file_data BYTEA"
        )
        await _exec(
            "ALTER TABLE building_standards ADD COLUMN IF NOT EXISTS file_size INTEGER"
        )
        await _exec(
            """
            CREATE TABLE IF NOT EXISTS inspection_log_buildings (
                id SERIAL PRIMARY KEY,
                building_id INTEGER NOT NULL UNIQUE REFERENCES buildings(id),
                created_at TIMESTAMP WITHOUT TIME ZONE
            )
            """
        )
        await _exec(
            "CREATE INDEX IF NOT EXISTS ix_inspection_log_buildings_building_id ON inspection_log_buildings (building_id)"
        )
        await _exec(
            """
            CREATE TABLE IF NOT EXISTS inspection_log_files (
                id SERIAL PRIMARY KEY,
                building_id INTEGER NOT NULL REFERENCES buildings(id),
                title VARCHAR(200) NOT NULL,
                original_name VARCHAR(300),
                stored_name VARCHAR(300) NOT NULL,
                content_type VARCHAR(100),
                file_data BYTEA,
                uploaded_by VARCHAR(100),
                created_at TIMESTAMP WITHOUT TIME ZONE
            )
            """
        )
        await _exec(
            "CREATE INDEX IF NOT EXISTS ix_inspection_log_files_building_id ON inspection_log_files (building_id)"
        )
        await _exec("ALTER TABLE inspection_log_files ADD COLUMN IF NOT EXISTS last_edit_pos JSONB")
        await _exec("ALTER TABLE inspection_log_files ADD COLUMN IF NOT EXISTS file_size INTEGER")
        await _exec(
            "ALTER TABLE inspection_log_buildings ADD COLUMN IF NOT EXISTS qr_write_file_id INTEGER"
        )
        await _exec(
            "ALTER TABLE inspection_log_files ADD COLUMN IF NOT EXISTS qr_equipment_id INTEGER"
        )
        await _exec(
            "CREATE INDEX IF NOT EXISTS ix_inspection_log_files_qr_equipment_id "
            "ON inspection_log_files (qr_equipment_id)"
        )
        await _exec(
            """
            CREATE TABLE IF NOT EXISTS equipment_change_logs (
                id SERIAL PRIMARY KEY,
                equipment_id INTEGER NOT NULL REFERENCES equipment(id),
                changed_by VARCHAR(100),
                changed_at TIMESTAMP WITHOUT TIME ZONE,
                summary VARCHAR(300) NOT NULL DEFAULT '',
                changes JSONB DEFAULT '[]'::jsonb
            )
            """
        )
        await _exec(
            "CREATE INDEX IF NOT EXISTS ix_equipment_change_logs_equipment_id ON equipment_change_logs (equipment_id)"
        )
        await _exec(
            "CREATE INDEX IF NOT EXISTS ix_equipment_change_logs_changed_at ON equipment_change_logs (changed_at)"
        )
        await _exec(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key VARCHAR(64) PRIMARY KEY,
                value TEXT
            )
            """
        )
        await _exec("ALTER TABLE access_logs ADD COLUMN IF NOT EXISTS http_method VARCHAR(10)")
        await _exec("ALTER TABLE access_logs ADD COLUMN IF NOT EXISTS path VARCHAR(500)")
        await _exec("ALTER TABLE access_logs ADD COLUMN IF NOT EXISTS status_code INTEGER")
        await _exec("ALTER TABLE access_logs ADD COLUMN IF NOT EXISTS resource VARCHAR(100)")
        await _exec("ALTER TABLE access_logs ADD COLUMN IF NOT EXISTS summary VARCHAR(500)")
        await _exec("CREATE INDEX IF NOT EXISTS ix_access_logs_path ON access_logs (path)")
        await _exec("CREATE INDEX IF NOT EXISTS ix_access_logs_resource ON access_logs (resource)")
    else:
        for stmt in (
            "ALTER TABLE equipment ADD COLUMN category VARCHAR(50) DEFAULT '설비'",
            "ALTER TABLE equipment_types ADD COLUMN category VARCHAR(50) DEFAULT '설비'",
            "ALTER TABLE equipment ADD COLUMN extra_data TEXT",
            "ALTER TABLE work_orders ADD COLUMN scheduled_date DATE",
            "ALTER TABLE work_orders ADD COLUMN is_active BOOLEAN DEFAULT 1",
            "ALTER TABLE work_orders ADD COLUMN d1_approved BOOLEAN DEFAULT 0",
            "ALTER TABLE work_orders ADD COLUMN approved_by VARCHAR(100)",
            "ALTER TABLE work_orders ADD COLUMN approved_at DATETIME",
            "ALTER TABLE work_orders ADD COLUMN approval_requested BOOLEAN DEFAULT 0",
            "ALTER TABLE work_orders ADD COLUMN approval_requested_by VARCHAR(100)",
            "ALTER TABLE work_orders ADD COLUMN approval_requested_at DATETIME",
            "ALTER TABLE work_orders ADD COLUMN work_permitted BOOLEAN DEFAULT 0",
            "ALTER TABLE work_orders ADD COLUMN work_permitted_by VARCHAR(100)",
            "ALTER TABLE work_orders ADD COLUMN work_permitted_at DATETIME",
            "ALTER TABLE work_orders ADD COLUMN requester_name VARCHAR(100)",
            "ALTER TABLE work_orders ADD COLUMN hazard_content TEXT",
            "ALTER TABLE work_orders ADD COLUMN safety_measures TEXT",
            "ALTER TABLE work_orders ADD COLUMN risk_grade VARCHAR(20)",
            "ALTER TABLE partners ADD COLUMN hazard_content TEXT",
            "ALTER TABLE partners ADD COLUMN safety_measures TEXT",
            "ALTER TABLE partners ADD COLUMN risk_grade VARCHAR(20)",
            "ALTER TABLE material_items ADD COLUMN location TEXT",
            "ALTER TABLE users ADD COLUMN openai_api_key VARCHAR(200)",
            "ALTER TABLE users ADD COLUMN openai_model VARCHAR(80)",
            "ALTER TABLE users ADD COLUMN is_approved BOOLEAN DEFAULT 1",
            "ALTER TABLE users ADD COLUMN can_create BOOLEAN DEFAULT 1",
            "ALTER TABLE users ADD COLUMN can_edit BOOLEAN DEFAULT 1",
            "ALTER TABLE users ADD COLUMN can_delete BOOLEAN DEFAULT 1",
            "ALTER TABLE users ADD COLUMN partner_id INTEGER",
            "ALTER TABLE users ADD COLUMN company_name VARCHAR(200)",
            "ALTER TABLE users ADD COLUMN menu_access TEXT",
            "ALTER TABLE floors ADD COLUMN is_active BOOLEAN DEFAULT 1",
            "ALTER TABLE zones ADD COLUMN is_active BOOLEAN DEFAULT 1",
            "ALTER TABLE building_drawings ADD COLUMN file_data BLOB",
            "ALTER TABLE building_drawings ADD COLUMN file_size INTEGER",
            "ALTER TABLE building_standards ADD COLUMN file_size INTEGER",
            "ALTER TABLE inspection_log_files ADD COLUMN last_edit_pos TEXT",
            "ALTER TABLE inspection_log_files ADD COLUMN file_size INTEGER",
            "ALTER TABLE inspection_log_buildings ADD COLUMN qr_write_file_id INTEGER",
            "ALTER TABLE inspection_log_files ADD COLUMN qr_equipment_id INTEGER",
            "CREATE INDEX IF NOT EXISTS ix_inspection_log_files_qr_equipment_id "
            "ON inspection_log_files (qr_equipment_id)",
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
        await _exec("UPDATE users SET is_approved = 1 WHERE is_approved IS NULL")
        await _exec("UPDATE users SET can_create = 1 WHERE can_create IS NULL")
        await _exec("UPDATE users SET can_edit = 1 WHERE can_edit IS NULL")
        await _exec("UPDATE users SET can_delete = 1 WHERE can_delete IS NULL")
        await _exec(
            "UPDATE users SET can_create = 0, can_edit = 0, can_delete = 0 "
            "WHERE role = 'viewer'"
        )
        await _exec(
            "UPDATE users SET can_create = 1, can_edit = 1, can_delete = 1 "
            "WHERE role = 'system_admin'"
        )
        await _exec("UPDATE work_orders SET is_active = 1 WHERE is_active IS NULL")
        await _exec("UPDATE floors SET is_active = 1 WHERE is_active IS NULL")
        await _exec("UPDATE zones SET is_active = 1 WHERE is_active IS NULL")
        await _exec(
            """
            CREATE TABLE IF NOT EXISTS building_drawings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                building_id INTEGER NOT NULL,
                floor_id INTEGER,
                title VARCHAR(200) NOT NULL,
                original_name VARCHAR(300),
                stored_name VARCHAR(300) NOT NULL,
                content_type VARCHAR(100),
                created_at DATETIME
            )
            """
        )
        await _exec(
            """
            CREATE TABLE IF NOT EXISTS building_standards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                building_id INTEGER NOT NULL,
                title VARCHAR(200) NOT NULL,
                original_name VARCHAR(300),
                stored_name VARCHAR(300) NOT NULL,
                content_type VARCHAR(100),
                file_data BLOB,
                created_at DATETIME
            )
            """
        )
        await _exec(
            """
            CREATE TABLE IF NOT EXISTS inspection_log_buildings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                building_id INTEGER NOT NULL UNIQUE,
                created_at DATETIME
            )
            """
        )
        await _exec(
            """
            CREATE TABLE IF NOT EXISTS inspection_log_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                building_id INTEGER NOT NULL,
                title VARCHAR(200) NOT NULL,
                original_name VARCHAR(300),
                stored_name VARCHAR(300) NOT NULL,
                content_type VARCHAR(100),
                file_data BLOB,
                uploaded_by VARCHAR(100),
                created_at DATETIME
            )
            """
        )
        await _exec(
            "ALTER TABLE inspection_log_files ADD COLUMN qr_equipment_id INTEGER"
        )
        await _exec(
            "CREATE INDEX IF NOT EXISTS ix_inspection_log_files_qr_equipment_id "
            "ON inspection_log_files (qr_equipment_id)"
        )
        await _exec(
            """
            CREATE TABLE IF NOT EXISTS equipment_change_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                equipment_id INTEGER NOT NULL,
                changed_by VARCHAR(100),
                changed_at DATETIME,
                summary VARCHAR(300) NOT NULL DEFAULT '',
                changes TEXT
            )
            """
        )
        await _exec(
            "CREATE INDEX IF NOT EXISTS ix_equipment_change_logs_equipment_id ON equipment_change_logs (equipment_id)"
        )
        await _exec(
            "CREATE INDEX IF NOT EXISTS ix_equipment_change_logs_changed_at ON equipment_change_logs (changed_at)"
        )
        await _exec(
            """
            CREATE TABLE IF NOT EXISTS pm_inspections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                schedule_id INTEGER NOT NULL,
                equipment_id INTEGER NOT NULL,
                result VARCHAR(20) NOT NULL DEFAULT 'normal',
                note TEXT,
                inspector_name VARCHAR(100),
                inspected_at DATETIME,
                work_order_id INTEGER
            )
            """
        )
        await _exec(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key VARCHAR(64) PRIMARY KEY,
                value TEXT
            )
            """
        )
        for stmt in (
            "ALTER TABLE access_logs ADD COLUMN http_method VARCHAR(10)",
            "ALTER TABLE access_logs ADD COLUMN path VARCHAR(500)",
            "ALTER TABLE access_logs ADD COLUMN status_code INTEGER",
            "ALTER TABLE access_logs ADD COLUMN resource VARCHAR(100)",
            "ALTER TABLE access_logs ADD COLUMN summary VARCHAR(500)",
            "CREATE INDEX IF NOT EXISTS ix_access_logs_path ON access_logs (path)",
            "CREATE INDEX IF NOT EXISTS ix_access_logs_resource ON access_logs (resource)",
            "ALTER TABLE work_orders ADD COLUMN IF NOT EXISTS requester_user_id INTEGER",
            "ALTER TABLE work_orders ADD COLUMN IF NOT EXISTS is_rejected BOOLEAN DEFAULT FALSE",
            "ALTER TABLE work_orders ADD COLUMN IF NOT EXISTS rejected_by VARCHAR(100)",
            "ALTER TABLE work_orders ADD COLUMN IF NOT EXISTS rejected_at TIMESTAMP WITHOUT TIME ZONE",
            "ALTER TABLE work_orders ADD COLUMN IF NOT EXISTS rejection_reason TEXT",
            "UPDATE work_orders SET is_rejected = FALSE WHERE is_rejected IS NULL",
        ):
            await _exec(stmt)
        await _exec(
            """
            CREATE TABLE IF NOT EXISTS user_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                work_order_id INTEGER,
                kind VARCHAR(50) DEFAULT 'wo_rejected',
                title VARCHAR(200) NOT NULL,
                body TEXT NOT NULL,
                is_read BOOLEAN DEFAULT FALSE,
                created_at DATETIME
            )
            """
        )
        await _exec(
            "CREATE INDEX IF NOT EXISTS ix_user_notifications_user_id ON user_notifications (user_id)"
        )
        await _exec(
            "CREATE INDEX IF NOT EXISTS ix_user_notifications_created_at ON user_notifications (created_at)"
        )


async def ensure_app_settings_table() -> None:
    """대시보드 화면 구성 등 앱 설정 테이블 (기동 전 요청 대비)."""
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError, OperationalError, ProgrammingError

    stmt = """
        CREATE TABLE IF NOT EXISTS app_settings (
            key VARCHAR(64) PRIMARY KEY,
            value TEXT
        )
    """
    try:
        async with engine.begin() as conn:
            await conn.execute(text(stmt))
    except (OperationalError, ProgrammingError, DBAPIError) as e:
        print(f"[db] app_settings ensure skip: {e}", flush=True)


async def deactivate_test_buildings_47_48() -> None:
    """서버관리 백업에 섞이던 테스트 건물(id 47·48) 및 하위 데이터를 비활성화."""
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError, OperationalError, ProgrammingError

    ids = (47, 48)
    try:
        async with engine.begin() as conn:
            for bid in ids:
                r = await conn.execute(
                    text(
                        "UPDATE buildings SET is_active = FALSE "
                        "WHERE id = :id AND (is_active IS NULL OR is_active = TRUE)"
                    ),
                    {"id": bid},
                )
                if r.rowcount and r.rowcount > 0:
                    print(f"[db] deactivated test building id={bid}", flush=True)
                await conn.execute(
                    text(
                        "UPDATE floors SET is_active = FALSE "
                        "WHERE building_id = :id AND (is_active IS NULL OR is_active = TRUE)"
                    ),
                    {"id": bid},
                )
                await conn.execute(
                    text(
                        "UPDATE zones SET is_active = FALSE WHERE floor_id IN "
                        "(SELECT id FROM floors WHERE building_id = :id) "
                        "AND (is_active IS NULL OR is_active = TRUE)"
                    ),
                    {"id": bid},
                )
                await conn.execute(
                    text(
                        "UPDATE equipment SET is_active = FALSE WHERE zone_id IN "
                        "(SELECT z.id FROM zones z "
                        " JOIN floors f ON z.floor_id = f.id "
                        " WHERE f.building_id = :id) "
                        "AND (is_active IS NULL OR is_active = TRUE)"
                    ),
                    {"id": bid},
                )
    except (OperationalError, ProgrammingError, DBAPIError) as e:
        print(f"[db] deactivate test buildings 47/48 skip: {e}", flush=True)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
