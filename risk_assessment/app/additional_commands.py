"""추가 명령 1~7 — 위험성평가 결과·작업명 기반 (버튼 클릭 시에만 실행)"""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING, Callable

from app.field_content import polish_hazard, polish_improvement
from app.prompts import ADDITIONAL_COMMANDS, format_five_m_one_e
from app.safety_fetcher import SafetyDataFetcher, SafetyReport, is_safety_accident_item

if TYPE_CHECKING:
    from app.local_engine import RiskRow

WORK_CLASS_ORDER = {"운전작업": 0, "정비작업": 1, "돌발대응": 2}
PHASE_ORDER = {"작업 전": 0, "작업 중": 1, "작업 후": 2}

NEWS_SOURCES = frozenset({"뉴스(네이버)", "뉴스(연합)", "뉴스(뉴스1)", "뉴스·언론"})
PORTAL_SOURCES = frozenset({"네이버", "다음", *NEWS_SOURCES})


def run_additional_command(
    command_num: int,
    job: str,
    rows: list[RiskRow] | None,
    five_m: dict[str, str],
    assessment_context: str = "",
    major_name: str = "",
    on_status: Callable[[str], None] | None = None,
    user_question: str = "",
) -> str:
    job = (job or "미지정").strip()
    cmd = ADDITIONAL_COMMANDS.get(command_num, "기타")
    rows = list(rows or [])

    if command_num == 1:
        if on_status:
            on_status(f"『{job}』 작업표준·관련 자료 검색 중…")
        fetcher = SafetyDataFetcher()
        report = fetcher.fetch_command(1, job, major_name=major_name or None)
        body = _cmd_work_standard(job, rows, five_m, cmd)
        search = _format_search_dual(report, 8, 10)
        if search:
            body += "\n\n### 관련 자료 검색\n\n" + "\n".join(search)
        return body

    if command_num == 7:
        q = (user_question or "").strip()
        if on_status:
            on_status(f"질문『{q}』·작업『{job}』 관련 자료 검색 중…" if q else f"『{job}』 관련 자료 검색 중…")
        fetcher = SafetyDataFetcher()
        report = fetcher.fetch_command(
            7, job, major_name=major_name or None, user_question=q
        )
        return _cmd_other(job, rows, five_m, cmd, report, assessment_context, user_question=q)

    if on_status:
        on_status(f"『{job}』 관련 자료 검색 중…")

    fetcher = SafetyDataFetcher()
    report = fetcher.fetch_command(command_num, job, major_name=major_name or None)

    handlers = {
        2: _cmd_technical,
        3: _cmd_accidents_news,
        4: _cmd_government_steps,
        5: _cmd_legal_provisions,
        6: _cmd_education,
    }
    handler = handlers.get(command_num, _cmd_other)
    return handler(job, rows, five_m, cmd, report, assessment_context)


def _sorted_rows(rows: list[RiskRow]) -> list[RiskRow]:
    return sorted(
        rows,
        key=lambda r: (
            WORK_CLASS_ORDER.get(r.work_class, 9),
            PHASE_ORDER.get(r.phase, 9),
        ),
    )


def _group_steps(rows: list[RiskRow]) -> list[tuple[str, str, str, list[RiskRow]]]:
    groups: OrderedDict[tuple[str, str, str], list[RiskRow]] = OrderedDict()
    for r in _sorted_rows(rows):
        key = (r.work_class, r.phase, r.unit_task)
        groups.setdefault(key, []).append(r)
    return [(k[0], k[1], k[2], v) for k, v in groups.items()]


def _cmd_work_standard(
    job: str,
    rows: list[RiskRow],
    five_m: dict[str, str],
    cmd: str,
) -> str:
    lines = [
        f"## {cmd} — {job}",
        "",
        "### 목적",
        f"{job} 작업 시 유해·위험요인을 제거·통제하고 재해를 예방한다.",
        "",
        "### 적용범위",
        "본 작업표준은 아래 위험성평가(JSA) 결과에 따른 작업 전·중·후 모든 단계에 적용한다.",
        "",
    ]

    if five_m and any(five_m.values()):
        lines.extend(["### 5M 1E (현장 조건)", format_five_m_one_e(five_m), ""])

    if not rows:
        lines.append(
            "※ 위험성평가 행이 없어 일반 절차만 표시합니다. "
            "먼저 『위험성평가 시작』을 실행한 뒤 다시 1번을 눌러 주세요."
        )
        return "\n".join(lines)

    current_class = ""
    current_phase = ""
    step_no = 0

    for work_class, phase, unit_task, group in _group_steps(rows):
        if work_class != current_class:
            current_class = work_class
            current_phase = ""
            lines.extend(["", f"## ■ {work_class}", ""])
        if phase != current_phase:
            current_phase = phase
            lines.append(f"### {phase}")
            step_no = 0

        step_no += 1
        lines.append(f"**{step_no}. {unit_task}**")
        for r in group:
            lines.append(f"- **유해·위험:** {polish_hazard(r.hazard)}")
            lines.append(f"- **재해유형:** {r.injury}")
            if r.current.strip():
                lines.append(f"- **현행 조치:** {r.current.strip()[:200]}")
            imp = polish_improvement(r.improvements).replace("\n", " / ")
            if imp.strip():
                lines.append(f"- **준수·개선:** {imp[:300]}")
            if r.law and r.law_url:
                lines.append(f"- **법적근거:** [{r.law}]({r.law_url})")
        lines.append("")

    lines.extend([
        "### 공통 — 작업 후",
        "1. 설비·에너지 원상복구 및 LOTO 해제 확인",
        "2. 작업장 정리·5S, 기록·차기점검 사항 이관",
        "3. 이상·아차사고 발생 시 보고 및 위험성평가 개정 검토",
        "",
        "### 비상연락",
        "- 119 / 사업장 비상연락망 / 작업 책임자",
        "",
        "※ 본 작업표준은 완료된 위험성평가표의 **단위작업·단계 순서**를 따릅니다.",
    ])
    return "\n".join(lines)


def _format_search_dual(
    report: SafetyReport,
    general_max: int = 8,
    safety_max: int = 10,
    *,
    accident_only: bool = False,
) -> list[str]:
    """일반 포털(네이버·다음) + 안전 전문 사이트 결과를 구분 표시"""
    lines: list[str] = []
    if report.local_notes:
        lines.append("**프로그램 내 유사 작업·JSA**")
        lines.extend(report.local_notes[:8])
        lines.append("")

    items = [i for i in report.items if is_safety_accident_item(i)] if accident_only else report.items

    general_items = [i for i in items if i.source in PORTAL_SOURCES]
    safety_items = [i for i in items if i.source not in PORTAL_SOURCES]

    if not general_items and not safety_items:
        if accident_only:
            lines.append("_(안전·산재 사고 관련 검색 결과 없음 — 키워드를 바꿔 다시 검색하세요.)_")
        else:
            lines.append("_(검색 결과 없음 — 아래 링크에서 직접 검색하세요.)_")
        return lines

    lines.append(f"**검색 키워드:** {report.keywords}")
    if accident_only:
        lines.append("※ 시공사례·공사실적 등은 제외하고 **안전·산재 사고** 관련만 표시합니다.")

    if general_items:
        lines.append("")
        lines.append("### ■ 일반 검색 (네이버·다음 등)")
        seen: set[str] = set()
        n = 0
        for item in sorted(general_items, key=lambda x: x.score, reverse=True):
            if item.url in seen:
                continue
            seen.add(item.url)
            n += 1
            tag = f" [{item.source}]" if item.source else ""
            lines.append(f"{n}. [{item.title}]({item.url}){tag}")
            if item.snippet:
                lines.append(f"   {item.snippet[:200]}")
            if n >= general_max:
                break

    if safety_items:
        lines.append("")
        lines.append("### ■ 안전 전문 사이트")
        seen = set()
        n = 0
        for item in sorted(safety_items, key=lambda x: x.score, reverse=True):
            if item.url in seen:
                continue
            seen.add(item.url)
            n += 1
            tag = f" [{item.source}]" if item.source else ""
            note = f" — _{item.match_note}_" if item.match_note else ""
            lines.append(f"{n}. [{item.title}]({item.url}){tag}{note}")
            if item.snippet:
                lines.append(f"   {item.snippet[:200]}")
            if n >= safety_max:
                break

    if report.errors:
        lines.append("")
        lines.append("_참고:_ " + "; ".join(report.errors[:3]))
    return lines


def _format_search_block(report: SafetyReport, max_items: int = 10) -> list[str]:
    return _format_search_dual(report, general_max=max_items // 2 + 2, safety_max=max_items)


def _high_risk_summary(rows: list[RiskRow]) -> list[str]:
    from app.local_engine import risk_grade

    lines: list[str] = []
    for r in _sorted_rows(rows):
        score = r.freq_before * r.sev_before
        grade, level, _ = risk_grade(score)
        if score >= 9:
            lines.append(
                f"- [{r.phase}] {r.unit_task}: {polish_hazard(r.hazard)[:80]} "
                f"→ **{grade}** ({level})"
            )
    return lines[:12]


def _unique_laws(rows: list[RiskRow]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for r in rows:
        if r.law and r.law not in seen:
            seen.add(r.law)
            out.append((r.law, r.law_url))
    return out


def _cmd_technical(
    job: str,
    rows: list[RiskRow],
    five_m: dict[str, str],
    cmd: str,
    report: SafetyReport,
    ctx: str,
) -> str:
    lines = [f"## {cmd} — {job}", ""]

    if five_m.get("Machine"):
        lines.extend(["**관련 설비·장비:**", five_m["Machine"], ""])
    if five_m.get("Material"):
        lines.extend(["**관련 물질·에너지:**", five_m["Material"], ""])
    if five_m.get("Method"):
        lines.extend(["**작업방법·절차:**", five_m["Method"], ""])
    if five_m.get("Environment"):
        lines.extend(["**작업환경:**", five_m["Environment"], ""])

    lines.extend(_format_search_dual(report, 8, 12))
    lines.extend([
        "",
        "**추가 확인:** 설비사양서·MSDS·점검기록은 사업장 보유 자료와 대조하세요.",
        "",
        "[KOSHA 스마트검색](https://smartsearch.kosha.or.kr) · "
        "[고용노동부](https://www.moel.go.kr/index.do)",
    ])
    return "\n".join(lines)


def _cmd_accidents_news(
    job: str,
    rows: list[RiskRow],
    five_m: dict[str, str],
    cmd: str,
    report: SafetyReport,
    ctx: str,
) -> str:
    lines = [f"## {cmd} — {job}", ""]

    injuries: dict[str, int] = {}
    for r in rows:
        for part in r.injury.replace("/", ",").split(","):
            p = part.strip()
            if p:
                injuries[p] = injuries.get(p, 0) + 1
    if injuries:
        lines.append("**본 작업 평가의 재해유형**")
        for inj, cnt in sorted(injuries.items(), key=lambda x: -x[1])[:8]:
            lines.append(f"- {inj} ({cnt}건)")
        lines.append("")

    if rows:
        lines.append("**유사 위험 시나리오 (JSA)**")
        for r in _sorted_rows(rows)[:6]:
            lines.append(f"- {polish_hazard(r.hazard)[:90]} → {r.injury}")
        lines.append("")

    lines.extend(_format_search_dual(report, 10, 10, accident_only=True))
    lines.extend([
        "",
        "[산재통계(data.go.kr)](https://www.data.go.kr/data/15087461/fileData.do) · "
        "[중대재해알림e](https://labor.moel.go.kr/sasttc/main/main.do) · "
        "[네이버 뉴스](https://search.naver.com/search.naver?where=news)",
    ])
    return "\n".join(lines)


def _cmd_government_steps(
    job: str,
    rows: list[RiskRow],
    five_m: dict[str, str],
    cmd: str,
    report: SafetyReport,
    ctx: str,
) -> str:
    lines = [
        f"## {cmd} — {job}",
        "",
        "| 단계 | 『" + job + "』 적용 고려사항 |",
        "| :--- | :--- |",
        "| 1. 사전준비 | 작업표준·설비사양·기존 위험성평가·아차사고 자료 수집 |",
        "| 2. 유해·위험요인 파악 | 순회점검, 근로자 의견, 5M 1E 분석 |",
        "| 3. 위험성 추정 | 빈도·강도 매트릭스 (본 프로그램 적용) |",
        "| 4. 위험성 결정 | A~F 등급 및 허용기준 판단 |",
        "| 5. 대책 수립 | 제거→대체→공학→관리→보호구 |",
        "| 6. 기록·공유 | 평가표 유지, 교육, 개정 |",
        "",
    ]
    hi = _high_risk_summary(rows)
    if hi:
        lines.append("**본 작업 고위험 항목 (우선 개선)**")
        lines.extend(hi)
        lines.append("")
    lines.extend(_format_search_dual(report, 6, 8))
    lines.append("")
    lines.append(
        "[산업안전포털](https://www.safety-as.com) · "
        "[고용노동부](https://www.moel.go.kr/index.do)"
    )
    return "\n".join(lines)


def _cmd_legal_provisions(
    job: str,
    rows: list[RiskRow],
    five_m: dict[str, str],
    cmd: str,
    report: SafetyReport,
    ctx: str,
) -> str:
    lines = [f"## {cmd} — {job}", ""]
    laws = _unique_laws(rows)
    if laws:
        lines.append("**위험성평가표에서 도출된 법적근거**")
        lines.append("")
        lines.append("| 조항 | 링크 |")
        lines.append("| :--- | :--- |")
        for law, url in laws[:15]:
            lines.append(f"| {law} | [law.go.kr]({url}) |")
        lines.append("")
    lines.extend(_format_search_dual(report, 6, 10))
    return "\n".join(lines)


def _cmd_education(
    job: str,
    rows: list[RiskRow],
    five_m: dict[str, str],
    cmd: str,
    report: SafetyReport,
    ctx: str,
) -> str:
    lines = [
        f"## {cmd} — {job}",
        "",
        "| 교육 | 내용 | 권장 주기 |",
        "| :--- | :--- | :--- |",
        f"| 정기교육 | 산업안전보건법·{job} 작업표준 | 연 1회 이상 |",
        f"| 특별교육 | {job} 유해·위험·대책 | 작업 투입 전 |",
        "| TBM | 당일 작업·위험·대책 | 매 작업 전 |",
        "| 비상훈련 | 화재·누출·응급 | 분기 1회 권장 |",
        "",
    ]
    if rows:
        topics = list(dict.fromkeys(r.unit_task for r in _sorted_rows(rows)))[:6]
        if topics:
            lines.append("**교육 시 강조할 단위작업**")
            for t in topics:
                lines.append(f"- {t}")
            lines.append("")
    lines.extend(_format_search_dual(report, 6, 10))
    return "\n".join(lines)


def _cmd_other(
    job: str,
    rows: list[RiskRow],
    five_m: dict[str, str],
    cmd: str,
    report: SafetyReport,
    ctx: str,
    *,
    user_question: str = "",
) -> str:
    q = (user_question or "").strip()
    lines = [
        f"## {cmd} — {job}",
        "",
    ]
    if q:
        lines.extend([
            f"**질문:** {q}",
            "",
            "아래는 질문·작업명을 바탕으로 검색한 자료입니다.",
            "",
        ])
    lines.extend([
        f"**평가 작업:** {job}",
        f"**평가 항목 수:** {len(rows)}건",
        "",
    ])
    if not q:
        lines.extend([
            "**현장 추가 확인 권장**",
            "- 계절·야간·협력업체 등 특수 조건 반영 여부",
            "- 최근 아차사고·점검 지적사항 반영",
            "- 보호구 지급·착용 실태",
            "",
        ])
    hi = _high_risk_summary(rows)
    if hi:
        lines.append("**고위험 요약**")
        lines.extend(hi)
        lines.append("")
    lines.extend(_format_search_dual(report, 8 if q else 6, 10 if q else 8))
    if not q:
        lines.append("")
        lines.append("자세한 AI 상담이 필요하면 **AI 작성 모드**로 전환하세요.")
    return "\n".join(lines)
