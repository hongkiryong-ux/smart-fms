# -*- coding: utf-8 -*-
"""점검일지(구 점검일지2) — 건물별 공개 QR 1일 경로."""
from __future__ import annotations

from models import Building


def inspection_log2_public_daily_path(building: Building | None) -> str | None:
    """설비 QR 「일지작성」용 공개 1일 URL. 해당 양식이 없으면 None."""
    if not building:
        return None
    code = (building.code or "").strip()
    if not code:
        return None

    from housing_substation import is_housing_substation_building
    from steelworks_hq import is_steelworks_hq_building
    from steelworks_hall import is_steelworks_hall_building
    from human_center import is_human_center_building
    from eoulrim_gym import is_eoulrim_gym_building
    from baegun_art_hall import is_baegun_art_hall_building
    from baegun_dorm import is_baegun_dorm_building
    from giga_town import is_giga_town_building
    from park1538 import is_park1538_building
    from sub53 import is_sub53_building
    from baegundae import is_baegundae_building
    from ccr_facility import is_ccr_facility_building
    from central_control_room import is_central_control_room_building

    # inspection_logs2_building_detail 분기와 동일 우선순위
    checks: list[tuple[object, str]] = [
        (is_housing_substation_building, f"/hs/{code}/daily"),
        (is_steelworks_hq_building, f"/swhq/{code}/daily"),
        (is_steelworks_hall_building, f"/swhall/{code}/daily"),
        (is_human_center_building, f"/hcenter/{code}/daily"),
        (is_eoulrim_gym_building, f"/egym/{code}/daily"),
        (is_baegun_art_hall_building, f"/bahall/{code}/daily"),
        (is_baegun_dorm_building, f"/bdorm/{code}/daily"),
        (is_giga_town_building, f"/gtown/{code}/daily"),
        (is_park1538_building, f"/p1538/{code}/daily"),
        (is_sub53_building, f"/s53/{code}/daily"),
        (is_baegundae_building, f"/bdae/{code}/daily"),
        (is_central_control_room_building, f"/ccr/{code}/daily"),
        (is_ccr_facility_building, f"/ccrf/{code}/daily"),
    ]
    for pred, path in checks:
        if pred(building):  # type: ignore[operator]
            return path
    return None
