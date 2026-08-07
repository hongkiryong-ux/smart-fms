#!/usr/bin/env python3
"""streetlamp-db → smart-fms-db 테이블 이관 도우미.

사용 (로컬):
  set STREETLAMP_DATABASE_URL=postgresql://...streetlamp...
  set DATABASE_URL=postgresql://...smart_fms...
  python scripts/migrate_streetlamp_db.py              # lamps + 의뢰 + 설정
  python scripts/migrate_streetlamp_db.py --lamps-only # lamps(주소)만 교체

주의: 대상 DB의 해당 테이블은 TRUNCATE 후 덮어씁니다.
       --lamps-only 도 의뢰 FK 때문에 maintenance_requests 를 함께 비웁니다.
"""
from __future__ import annotations

import argparse
import os
import sys


def _norm_pg(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="streetlamp-db → smart-fms-db 이관")
    parser.add_argument(
        "--lamps-only",
        action="store_true",
        help="가로등(lamps: code/location/description)만 교체 이관",
    )
    parser.add_argument(
        "--with-requests",
        action="store_true",
        help="--lamps-only 와 함께 쓰면 의뢰도 이관",
    )
    args = parser.parse_args(argv)

    src = _norm_pg(
        os.environ.get("STREETLAMP_DATABASE_URL")
        or os.environ.get("STREETLAMP_DB_URL")
        or ""
    )
    dst = _norm_pg(os.environ.get("DATABASE_URL") or "")
    if not src or not dst:
        print(
            "STREETLAMP_DATABASE_URL 과 DATABASE_URL(smart-fms) 를 설정하세요.",
            file=sys.stderr,
        )
        return 2

    import psycopg

    migrate_requests = (not args.lamps_only) or args.with_requests
    migrate_settings = not args.lamps_only

    with psycopg.connect(src) as sconn, psycopg.connect(dst) as dconn:
        dconn.execute("SET session_replication_role = replica")
        with sconn.cursor() as sc, dconn.cursor() as dc:
            dc.execute(
                """
                CREATE TABLE IF NOT EXISTS lamps (
                  id SERIAL PRIMARY KEY,
                  code VARCHAR(64) UNIQUE,
                  location VARCHAR(255) NOT NULL,
                  description TEXT
                );
                """
            )
            dc.execute("CREATE INDEX IF NOT EXISTS ix_lamps_id ON lamps (id)")
            dc.execute("CREATE INDEX IF NOT EXISTS ix_lamps_code ON lamps (code)")
            dc.execute(
                """
                DO $$ BEGIN
                  CREATE TYPE requeststatus AS ENUM ('received', 'in_progress', 'done');
                EXCEPTION WHEN duplicate_object THEN null; END $$;
                """
            )
            dc.execute(
                """
                DO $$ BEGIN
                  CREATE TYPE requesttype AS ENUM (
                    'outage', 'globe_broken', 'fall_risk', 'low_brightness', 'other'
                  );
                EXCEPTION WHEN duplicate_object THEN null; END $$;
                """
            )
            dc.execute(
                """
                CREATE TABLE IF NOT EXISTS maintenance_requests (
                  id SERIAL PRIMARY KEY,
                  lamp_id INTEGER REFERENCES lamps(id),
                  name VARCHAR(100) NOT NULL,
                  phone VARCHAR(50) NOT NULL,
                  request_type requesttype NOT NULL,
                  content TEXT,
                  status requeststatus,
                  work_memo TEXT,
                  created_at TIMESTAMP WITHOUT TIME ZONE,
                  completed_at TIMESTAMP WITHOUT TIME ZONE
                );
                """
            )
            dc.execute(
                """
                CREATE TABLE IF NOT EXISTS app_settings (
                  key VARCHAR(64) PRIMARY KEY,
                  value TEXT
                );
                """
            )
            dconn.commit()

            sc.execute("SELECT count(*) FROM lamps")
            print(f"[src] lamps: {sc.fetchone()[0]} rows")
            if migrate_requests:
                sc.execute("SELECT count(*) FROM maintenance_requests")
                print(f"[src] maintenance_requests: {sc.fetchone()[0]} rows")

            sc.execute(
                "SELECT id, code, location FROM lamps "
                "WHERE location IS NOT NULL AND location <> '' "
                "ORDER BY id LIMIT 5"
            )
            print("[src] location preview:")
            for row in sc.fetchall():
                print(f"  id={row[0]} code={row[1]!r} location={row[2]!r}")

            dc.execute("TRUNCATE maintenance_requests, lamps RESTART IDENTITY CASCADE")
            dconn.commit()

            sc.execute("SELECT id, code, location, description FROM lamps ORDER BY id")
            lamps = sc.fetchall()
            if lamps:
                dc.executemany(
                    "INSERT INTO lamps (id, code, location, description) VALUES (%s,%s,%s,%s)",
                    lamps,
                )
            print(f"[dst] lamps inserted: {len(lamps)} (location/address 포함)")

            if migrate_requests:
                sc.execute(
                    """
                    SELECT id, lamp_id, name, phone, request_type::text, content,
                           status::text, work_memo, created_at, completed_at
                    FROM maintenance_requests ORDER BY id
                    """
                )
                reqs = sc.fetchall()
                if reqs:
                    dc.executemany(
                        """
                        INSERT INTO maintenance_requests
                          (id, lamp_id, name, phone, request_type, content,
                           status, work_memo, created_at, completed_at)
                        VALUES
                          (%s,%s,%s,%s,%s::requesttype,%s,
                           %s::requeststatus,%s,%s,%s)
                        """,
                        reqs,
                    )
                print(f"[dst] maintenance_requests inserted: {len(reqs)}")
            else:
                print("[dst] maintenance_requests: skipped (--lamps-only, 테이블은 비움)")

            dc.execute(
                "SELECT setval(pg_get_serial_sequence('lamps','id'),"
                " COALESCE((SELECT MAX(id) FROM lamps),1))"
            )
            if migrate_requests:
                dc.execute(
                    "SELECT setval(pg_get_serial_sequence('maintenance_requests','id'),"
                    " COALESCE((SELECT MAX(id) FROM maintenance_requests),1))"
                )

            if migrate_settings:
                sc.execute("SELECT key, value FROM app_settings")
                settings = sc.fetchall()
                copied = 0
                for key, value in settings:
                    if not key:
                        continue
                    sk = (
                        key
                        if str(key).startswith("streetlamp.")
                        else f"streetlamp.{key}"
                    )
                    dc.execute(
                        """
                        INSERT INTO app_settings (key, value) VALUES (%s, %s)
                        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                        """,
                        (sk, value),
                    )
                    copied += 1
                print(f"[dst] app_settings (streetlamp.*) upserted: {copied}")
            dconn.commit()

        dconn.execute("SET session_replication_role = DEFAULT")
        dconn.commit()

    print("이관 완료.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
