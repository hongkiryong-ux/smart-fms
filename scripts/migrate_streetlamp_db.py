#!/usr/bin/env python3
"""streetlamp-db → smart-fms-db 테이블 이관 도우미.

사용 (로컬에서 DATABASE URL 설정 후):
  set STREETLAMP_DATABASE_URL=postgresql://...streetlamp...
  set DATABASE_URL=postgresql://...smart_fms...
  python scripts/migrate_streetlamp_db.py

이관 대상: lamps, maintenance_requests
설정(app_settings)은 streetlamp. 접두사로 복사 (FMS AppSetting 과 공유)

주의: 대상 DB의 lamps / maintenance_requests 는 TRUNCATE 후 덮어씁니다.
"""
from __future__ import annotations

import os
import sys


def main() -> int:
    src = (
        os.environ.get("STREETLAMP_DATABASE_URL")
        or os.environ.get("STREETLAMP_DB_URL")
        or ""
    ).strip()
    dst = (os.environ.get("DATABASE_URL") or "").strip()
    if not src or not dst:
        print(
            "STREETLAMP_DATABASE_URL 과 DATABASE_URL(smart-fms) 를 설정하세요.",
            file=sys.stderr,
        )
        return 2
    if src.startswith("postgres://"):
        src = "postgresql://" + src[len("postgres://") :]
    if dst.startswith("postgres://"):
        dst = "postgresql://" + dst[len("postgres://") :]

    import psycopg

    tables = ("lamps", "maintenance_requests")
    with psycopg.connect(src) as sconn, psycopg.connect(dst) as dconn:
        dconn.execute("SET session_replication_role = replica")
        with sconn.cursor() as sc, dconn.cursor() as dc:
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
            dconn.commit()

            for table in tables:
                sc.execute(f"SELECT count(*) FROM {table}")
                print(f"[src] {table}: {sc.fetchone()[0]} rows")

            dc.execute("TRUNCATE maintenance_requests, lamps RESTART IDENTITY CASCADE")
            dconn.commit()

            sc.execute("SELECT id, code, location, description FROM lamps ORDER BY id")
            lamps = sc.fetchall()
            if lamps:
                dc.executemany(
                    "INSERT INTO lamps (id, code, location, description) VALUES (%s,%s,%s,%s)",
                    lamps,
                )
            print(f"[dst] lamps inserted: {len(lamps)}")

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

            dc.execute(
                "SELECT setval(pg_get_serial_sequence('lamps','id'),"
                " COALESCE((SELECT MAX(id) FROM lamps),1))"
            )
            dc.execute(
                "SELECT setval(pg_get_serial_sequence('maintenance_requests','id'),"
                " COALESCE((SELECT MAX(id) FROM maintenance_requests),1))"
            )

            sc.execute("SELECT key, value FROM app_settings")
            settings = sc.fetchall()
            copied = 0
            for key, value in settings:
                if not key:
                    continue
                sk = key if str(key).startswith("streetlamp.") else f"streetlamp.{key}"
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

    print("이관 완료. Render smart-fms 를 재시작하면 됩니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
