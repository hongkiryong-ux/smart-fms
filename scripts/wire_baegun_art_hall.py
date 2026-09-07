# -*- coding: utf-8 -*-
"""Insert baegun_art_hall models + main.py wiring from human_center patterns."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def transform_routes(block: str) -> str:
    reps = [
        ("human-center", "baegun-art-hall"),
        ("human_center", "baegun_art_hall"),
        ("HumanCenter", "BaegunArtHall"),
        ("hcenter", "bahall"),
        ("HCENTER", "BAHALL"),
        ("is_human_center_building", "is_baegun_art_hall_building"),
        ("human_center_daily_qr_url", "baegun_art_hall_daily_qr_url"),
        ("ensure_hcenter_tables", "ensure_bahall_tables"),
        ("[hcenter]", "[bahall]"),
        ('"hcenter"', '"bahall"'),
        ("/hcenter/", "/bahall/"),
    ]
    for a, b in reps:
        block = block.replace(a, b)
    return block


def main() -> None:
    models = ROOT / "models.py"
    text = models.read_text(encoding="utf-8")
    if "class BaegunArtHallDaily" not in text:
        insert = '''

class BaegunArtHallDaily(Base):
    """백운아트홀 운영일보[냉,난방] 1일."""

    __tablename__ = "baegun_art_hall_daily"

    id = Column(Integer, primary_key=True)
    building_id = Column(Integer, ForeignKey("buildings.id"), nullable=False, index=True)
    log_date = Column(Date, nullable=False, index=True)
    data = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    building = relationship("Building")

    __table_args__ = (
        UniqueConstraint("building_id", "log_date", name="uq_bahall_daily"),
    )


class BaegunArtHallArchive(Base):
    """백운아트홀 운영일보 1일 아카이브."""

    __tablename__ = "baegun_art_hall_archives"

    id = Column(Integer, primary_key=True)
    building_id = Column(Integer, ForeignKey("buildings.id"), nullable=False, index=True)
    log_date = Column(Date, nullable=False, index=True)
    original_name = Column(String(300), nullable=True)
    file_data = Column(LargeBinary, nullable=True)
    file_size = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    building = relationship("Building")

    __table_args__ = (
        UniqueConstraint("building_id", "log_date", name="uq_bahall_archive"),
    )

'''
        anchor = "class InspectionLogFile(Base):"
        if anchor not in text:
            raise SystemExit("models anchor missing")
        text = text.replace(anchor, insert + anchor, 1)
        models.write_text(text, encoding="utf-8")
        print("models: added BaegunArtHall*")
    else:
        print("models: already present")

    main_py = ROOT / "main.py"
    main = main_py.read_text(encoding="utf-8")

    if "BaegunArtHallDaily" not in main:
        main = main.replace(
            "    Park1538Archive,\n    Park1538Daily,\n",
            "    Park1538Archive,\n    Park1538Daily,\n"
            "    BaegunArtHallArchive,\n    BaegunArtHallDaily,\n",
            1,
        )
        print("main: imports added")

    if "register_bahall_scheduler" not in main:
        main = main.replace(
            "            register_p1538_scheduler(scheduler, AsyncSessionLocal, KST)\n",
            "            register_p1538_scheduler(scheduler, AsyncSessionLocal, KST)\n"
            "            from baegun_art_hall import register_scheduler as register_bahall_scheduler\n\n"
            "            register_bahall_scheduler(scheduler, AsyncSessionLocal, KST)\n",
            1,
        )
        print("main: scheduler added")

    redirect_snip = '''    from baegun_art_hall import is_baegun_art_hall_building

    if is_baegun_art_hall_building(building):
        return RedirectResponse(
            f"/admin/inspection-logs2/{building_id}/baegun-art-hall",
            status_code=303,
        )
'''
    if "is_baegun_art_hall_building" not in main:
        # insert before baegun_dorm so 백운아트홀 wins over any loose 백운 match (dorm uses 백운생활관)
        main = main.replace(
            "    from baegun_dorm import is_baegun_dorm_building\n",
            redirect_snip + "    from baegun_dorm import is_baegun_dorm_building\n",
            1,
        )
        print("main: redirect added")

    if '"/admin/inspection-logs2/{building_id}/baegun-art-hall"' not in main or "async def baegun_art_hall_page" not in main:
        # extract human_center route block
        start = main.find('@app.get("/admin/inspection-logs2/{building_id}/human-center/qr.png")')
        end = main.find('@app.get("/admin/inspection-logs2/{building_id}/eoulrim-gym/qr.png")')
        if start < 0 or end < 0:
            raise SystemExit(f"route anchors missing start={start} end={end}")
        block = transform_routes(main[start:end])
        # insert after park1538 qr save block — before next major section
        insert_at = main.find('@app.get("/admin/inspection-logs2/{building_id}/ccr-facility/qr.png")')
        if insert_at < 0:
            # fallback: after park1538_qr_save function end marker
            insert_at = main.find('@app.get("/p1538/{code}/daily/save")')
            # better find end of park1538 by searching next @app after last p1538
            idx = main.rfind('@app.post("/p1538/{code}/daily/save")')
            if idx < 0:
                raise SystemExit("park1538 end missing")
            # find next @app.get after this function
            next_app = main.find("\n@app.", idx + 10)
            # skip to after the function - find double newline after return Redirect of qr save
            # Use ccr-facility if available else after p1538 block
            raise SystemExit("ccr-facility qr route not found; abort")
        if "async def baegun_art_hall_page" not in main:
            main = main[:insert_at] + block + "\n" + main[insert_at:]
            print("main: routes inserted")
        else:
            print("main: routes already present")
    else:
        print("main: routes already present")

    main_py.write_text(main, encoding="utf-8")
    print("done")


if __name__ == "__main__":
    main()
