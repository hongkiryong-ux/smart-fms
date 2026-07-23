"""유해·위험요인·재해유형에 맞는 법적근거(하이퍼링크) 추론

매칭 우선순위: 재해유형 → 유해위험요인(핵심) → 단위작업 → 개선대책(보조)
동일 점수일 때는 더 구체적인 조항 우선.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.law_catalog import build_laws_dict, law_ref_by_key, normalize_law

LAWS: dict[str, tuple[str, str]] = build_laws_dict()
LAW_DEFAULT: tuple[str, str] = law_ref_by_key("rule_default")


@dataclass(frozen=True)
class _Rule:
    law_key: str
    hazard_patterns: tuple[str, ...] = ()
    injury_patterns: tuple[str, ...] = ()
    unit_patterns: tuple[str, ...] = ()
    context_any: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    score: int = 10


# ── 재해유형 → 법령 (1차) ──
_INJURY_RULES: list[tuple[tuple[str, ...], str, int]] = [
    (("감전",), "rule_301", 50),
    (("추락", "떨어짐"), "rule_41", 48),
    (("화상",), "rule_97", 46),
    (("화재",), "rule_64", 46),
    (("질식", "산소결핍"), "rule_436", 50),
    (("끼임", "협착"), "rule_38", 44),
    (("절단", "베임", "찔림"), "rule_38", 40),
    (("근골격", "요통", "무리한"), "rule_663", 44),
    (("화학물질", "유해", "누출"), "rule_449", 38),
    (("폭발", "파열"), "rule_64", 44),
    (("교통", "충돌", "깔림"), "rule_76", 40),
    (("청각", "난청", "소음"), "rule_514", 48),
    (("업무상질병", "분진"), "rule_72", 42),
    (("넘어짐", "미끄"), "rule_5", 35),
    (("전도",), "rule_5", 30),
    (("실족",), "rule_5", 36),
    (("피부", "안구", "찰과상"), "rule_449", 34),
]

# ── 유해위험요인 패턴 (2차, 구체적) ──
_HAZARD_RULES: list[_Rule] = [
    _Rule("fire_fac_10", hazard_patterns=(
        r"esda|eda\b|화재탐지|가스소화|소화설비|소방펌프|스프링클러|소방시설",
    ), score=45),
    _Rule("rule_179", hazard_patterns=(
        r"전조등|시야.*차단|적재.*시야|화물.*시야|전방.*시야",
    ), unit_patterns=(r"지게차",), score=46),
    _Rule("rule_98", hazard_patterns=(
        r"과속|속도.*초과|10\s*km|제한속도|급출발|급정지",
    ), unit_patterns=(r"지게차|차량계",), score=44),
    _Rule("rule_99", hazard_patterns=(
        r"운전석.*이탈|시동키|운전위치.*이탈|키.*꽂",
    ), unit_patterns=(r"지게차",), score=44),
    _Rule("rule_183", hazard_patterns=(
        r"안전띠.*미|좌석.*안전띠|안전벨트.*미",
    ), unit_patterns=(r"지게차",), score=44),
    _Rule("rule_301", hazard_patterns=(r"충전부.*노출|충전부|활선.*노출",), score=46),
    _Rule("rule_302", hazard_patterns=(r"접지.*미|접지.*불량|분전.*접지",), score=44),
    _Rule("rule_304", hazard_patterns=(r"누전차단|누전.*차단기",), score=42),
    _Rule("rule_436", hazard_patterns=(r"밀폐|질식|산소결핍|산소부족|유해가스",), score=48),
    _Rule("rule_92", hazard_patterns=(
        r"loto|운전정지|잔류에너지|잔류압|잔압|에너지차단|전원차단|잠금",
    ), score=46),
    _Rule("rule_302", hazard_patterns=(r"접지저항|접지설비|접지\s*측정|접지\s*점검",), score=48),
    _Rule("rule_19", hazard_patterns=(
        r"오조작|작업수순|작업순서|차단기\s*조작|전환\s*운전|정전|복전|bus.?tie",
    ), score=42),
    _Rule("rule_301", hazard_patterns=(
        r"감전|충전부|활선|잔류전압|무전압|기동판넬|분전|판넬|ups|battery|밧데리|"
        r"축전지|변압기|bus.?tie|열화상|코로나|절연측정|전동기\s*절연|전압|전류|"
        r"배터리\s*설비|통신상태\s*점검|누전",
    ), exclude=(r"발전기\s*가동",), score=44),
    _Rule("rule_514", hazard_patterns=(r"소음|청각|데시벨|난청",), score=46),
    _Rule("rule_516", hazard_patterns=(r"귀마개|귀덮개|청력보호",), score=46),
    _Rule("rule_97", hazard_patterns=(r"화상|고온부|고온|증기|스팀|냉각수\s*점검.*화상",), score=44),
    _Rule("rule_63", hazard_patterns=(
        r"고소작업대|사다리|이동식\s*비계|들것|고소\s*작업",
    ), score=44),
    _Rule("rule_41", hazard_patterns=(
        r"추락|실족.*추락|낙하|고소|상부\s*정비|상부\s*작업|옥상.*추락|"
        r"냉각탑\s*상부|안전난간|핸드레일|2m\s*이상|개구부",
    ), score=42),
    _Rule("rule_38", hazard_patterns=(
        r"끼임|협착|회전체|프레스|롤러|컨베이어|기계\s*방호|출입문.*부압|부압.*출입",
    ), score=40),
    _Rule("rule_64", hazard_patterns=(r"화재|폭발|화기|인화|용접|불꽃|점화",), score=42),
    _Rule("rule_449", hazard_patterns=(r"msds|물질안전|유해.*표시|경고.*표시",), score=38),
    _Rule("rule_454", hazard_patterns=(
        r"유해\s*물질|위험물|화학물질|유류\s*누출|누출\s*확산|가스\s*누출|"
        r"약품|세척\s*액|피부\s*접촉|안구\s*접촉|피부질환",
    ), exclude=(r"유해\s*위험\s*요인",), score=38),
    _Rule("rule_663", hazard_patterns=(
        r"중량물|과중|25kg|20kg|무게중심|인양|하역|근골격",
    ), score=42),
    _Rule("rule_76", hazard_patterns=(r"지게차|후진|차량\s*충돌|충돌\s*우려",), score=38),
    _Rule("rule_94", hazard_patterns=(r"크레인|호이스트|체인블록|달기\s*운반",), score=38),
    _Rule("rule_5", hazard_patterns=(
        r"미끄|경사면|넘어짐|전도|돌출물|통로\s*적치|정리\s*정돈|정리정돈|"
        r"계단.*미끄|바닥\s*미끄|출입구.*경사|논슬립|실족|협소|불안전\s*자세",
    ), exclude=(r"추락|고소|상부|사다리|작업대|핸드레일",), score=34),
    _Rule("rule_32", hazard_patterns=(
        r"보호구\s*미\s*착용|보호구\s*미착용|안전모\s*미착|안전화\s*미|미착용.*작업",
    ), exclude=(r"감전|충전|전압|전기",), score=38),
    _Rule("rule_22", hazard_patterns=(r"통로.*미|안전통로|통행로.*미",), score=36),
    _Rule("rule_3", hazard_patterns=(r"자재.*방치|잡자재|걸림|전도.*우려",), score=34),
    _Rule("rule_663", hazard_patterns=(r"25\s*kg|20\s*kg|과중|중량물.*인력",), score=40),
    _Rule("rule_661", hazard_patterns=(r"반복.*동작|부적절.*자세|무리한.*힘|근골격",), score=38),
    _Rule("rule_44", hazard_patterns=(
        r"보호구\s*미\s*착용|보호구\s*미착용|안전모\s*미착|미착용.*작업",
    ), exclude=(r"감전|충전|전압|전기",), score=36),
    _Rule("rule_72", hazard_patterns=(r"분진|석고분|방진마스크",), score=38),
    _Rule("osh_act_41", hazard_patterns=(r"비상\s*운전|비상\s*대응|긴급\s*조치|응급\s*대피",), score=34),
]

# ── 단위작업 보조 ──
_UNIT_RULES: list[tuple[tuple[str, ...], str, int]] = [
    ((r"소방", r"esda", r"화재\s*탐지", r"감지\s*설비", r"소화"), "fire_fac_10", 24),
    ((r"loto", r"에너지\s*차단", r"운전\s*정지"), "rule_92", 18),
    ((r"접지",), "rule_302", 22),
    ((r"열화상", r"절연\s*측정", r"전기\s*실"), "rule_301", 18),
    ((r"고소", r"사다리", r"작업대"), "rule_63", 18),
]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _match_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


def _score_injury(injury: str) -> dict[str, int]:
    scores: dict[str, int] = {}
    inj = _norm(injury)
    if not inj:
        return scores
    for keys, law_key, base in _INJURY_RULES:
        if any(k in inj for k in keys):
            scores[law_key] = max(scores.get(law_key, 0), base)
    return scores


def _score_hazard(hazard: str, unit_task: str) -> dict[str, int]:
    scores: dict[str, int] = {}
    haz = _norm(hazard)
    unit = _norm(unit_task)
    if not haz and not unit:
        return scores

    for rule in _HAZARD_RULES:
        target = haz
        if rule.exclude and _match_any(target, rule.exclude):
            continue
        matched = False
        if rule.hazard_patterns and _match_any(target, rule.hazard_patterns):
            matched = True
        if rule.unit_patterns and unit and _match_any(unit, rule.unit_patterns):
            if rule.hazard_patterns and _match_any(target, rule.hazard_patterns):
                matched = True
            elif not rule.hazard_patterns:
                matched = True
        if rule.context_any and _match_any(f"{haz} {unit}", rule.context_any):
            matched = True
        if matched:
            scores[rule.law_key] = max(scores.get(rule.law_key, 0), rule.score)

    for patterns, law_key, pts in _UNIT_RULES:
        if _match_any(unit, patterns):
            scores[law_key] = max(scores.get(law_key, 0), pts)
    return scores


def _score_improvement(improvement: str, current: str) -> dict[str, int]:
    """개선대책은 보조 — hazard/injury에서 결정 안 될 때만 낮은 가중치"""
    text = _norm(f"{improvement} {current}")
    if not text:
        return {}
    scores: dict[str, int] = {}
    light_rules = [
        (("loto", "잠금", "운전정지"), "rule_92", 8),
        (("접지", "검전", "무전압"), "rule_301", 8),
        (("귀마개", "귀덮개", "소음"), "rule_516", 8),
        (("귀마개", "귀덮개"), "rule_514", 6),
        (("안전대", "추락방", "난간"), "rule_41", 8),
        (("논슬립", "미끄럼방지", "통로"), "rule_5", 6),
        (("소화", "화재감시", "화기엄금"), "rule_64", 8),
    ]
    for keys, law_key, pts in light_rules:
        if any(k in text for k in keys):
            scores[law_key] = max(scores.get(law_key, 0), pts)
    return scores


def _resolve_tie(scores: dict[str, int], hazard: str, injury: str) -> str:
    """동점 시 재해·위험 특성으로 보정"""
    haz = _norm(hazard)
    inj = _norm(injury)

    if "감전" in inj or re.search(r"감전|충전부|전압", haz):
        return "rule_301"
    if "추락" in inj or re.search(r"추락|실족.*추락|고소|상부", haz):
        return "rule_41"
    if re.search(r"미끄|경사|넘어짐|전도|실족", haz) and not re.search(r"추락|고소|상부", haz):
        return "rule_5"
    if re.search(r"소음|청각|난청", haz) or "청각" in inj:
        return "rule_514"
    if "협착" in inj or "끼임" in inj:
        return "rule_38"
    return max(scores, key=scores.get)


def infer_law(
    hazard: str = "",
    injury: str = "",
    improvement: str = "",
    current: str = "",
    unit_task: str = "",
) -> tuple[str, str]:
    """유해·위험요인·재해유형에 맞는 법적근거 반환"""
    from app.hazard_law_scenarios import match_scenario_law

    scenario = match_scenario_law(hazard, injury, unit_task)
    if scenario:
        return normalize_law(scenario[0], scenario[1])

    haz = _norm(hazard)
    unit = _norm(unit_task)

    # 작업 맥락 + 오조작 → 해당 설비 법령 우선
    if re.search(r"오조작|작업수순|작업순서", haz):
        if _match_any(unit, (r"화재", r"소방", r"감지", r"esda", r"소화")):
            return LAWS["fire_fac_10"]
        if _match_any(unit, (r"전력", r"정전", r"복전", r"bus", r"차단", r"ups", r"배터리", r"전기")):
            return LAWS["rule_19"]

    # 접지·절연저항 측정 → 접지 조항 우선
    if _match_any(unit, (r"접지", r"저항\s*측정", r"누설\s*전류", r"접지저항")):
        if re.search(r"감전|전압|절연|전류|접지", haz):
            return LAWS["rule_302"]

    scores: dict[str, int] = {}
    for src in (_score_injury(injury), _score_hazard(hazard, unit_task)):
        for k, v in src.items():
            scores[k] = max(scores.get(k, 0), v)

    # hazard/injury에서 확실한 매칭이 없을 때만 개선대책 참고
    if max(scores.values(), default=0) < 30:
        for k, v in _score_improvement(improvement, current).items():
            scores[k] = max(scores.get(k, 0), v)

    if not scores:
        return LAW_DEFAULT

    best = max(scores.values())
    top = [k for k, v in scores.items() if v == best]
    law_key = top[0] if len(top) == 1 else _resolve_tie(scores, hazard, injury)
    ref = LAWS.get(law_key, LAW_DEFAULT)
    return normalize_law(ref[0], ref[1])


def enrich_row_law(row) -> object:
    """RiskRow에 법적근거 보강 — 웹검색(우선) + 로컬 규칙(폴백)"""
    from dataclasses import replace

    from app.law_web_search import lookup_law

    if row.law != LAW_DEFAULT[0] and row.source != "JSA-PPT":
        law, url = normalize_law(row.law, row.law_url)
        return replace(row, law=law, law_url=url)

    law, url, _src = lookup_law(
        hazard=row.hazard,
        injury=row.injury,
        improvement=row.improvements,
        current=row.current,
        unit_task=row.unit_task,
    )
    law, url = normalize_law(law, url)
    return replace(row, law=law, law_url=url)
