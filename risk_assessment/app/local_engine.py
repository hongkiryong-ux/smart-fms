"""API 없이 동작하는 로컬 JSA 위험성평가 엔진"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from app.field_content import build_field_rows, clean_improvement_text, ensure_work_phases, polish_hazard, polish_improvement
from app.law_lookup import LAW_DEFAULT
from app.prompts import HAZARD_CHECKLIST, format_five_m_one_e
from app.risk_scoring import normalize_risk_rows

from app.runtime_paths import APP_ROOT, DATA_DIR

BASE_DIR = APP_ROOT

GRADE_TABLE = [
    (range(16, 21), "A급", "허용 불가 위험성", "즉시 작업 중지 (즉시 개선 필요)"),
    (range(15, 16), "B급", "중대한 위험성", "긴급 임시 대책 후 작업, 정비/보수 전 개선"),
    (range(9, 13), "C급", "상당한 위험성", "정비·보수기간 전 안전보건대책 수립 및 개선"),
    (range(8, 9), "D급", "경미한 위험성", "관리적 대책 필요 (표지, 작업표준 등)"),
    (range(4, 7), "E급", "미미한 위험성", "안전정보 제공 및 주기적 교육 필요"),
    (range(1, 4), "F급", "무시될 수 있는 위험성", "현재 대책 유지"),
]

HAZARD_KEYWORDS: dict[str, list[str]] = {
    "끼임": ["프레스", "금형", "롤러", "컨베이어", "기계"],
    "떨어짐": ["고소", "비계", "사다리", "인양", "크레인", "낙하"],
    "넘어짐": ["경사", "미끄럼", "우천", "통로", "바닥"],
    "감전": ["전기", "배선", "판넬", "감전", "전원"],
    "부딪힘": ["지게차", "후진", "차량", "이동", "충돌"],
    "맞음": ["낙하", "날림", "비산", "인양", "공구"],
    "폭발·파열": ["압력", "가스", "용기", "드럼", "보일러"],
    "깔림": ["지게차", "중량물", "차량", "롤"],
    "화재": ["인화", "용접", "절단", "불꽃", "화기"],
    "화학물질누출": ["누출", "위험물", "MSDS", "유해", "화학"],
    "교통": ["지게차", "차량", "운행", "도로", "후진"],
    "산소결핍": ["밀폐", "맨홀", "탱크", "질식", "가스"],
    "절단": ["절단", "원형톱", "칼", "날"],
    "베임": ["날", "절단", "예리"],
    "찔림": ["철근", "돌출", "뾰족"],
}


@dataclass
class RiskRow:
    work_class: str
    phase: str
    unit_task: str
    hazard: str
    injury: str
    current: str
    freq_before: int
    sev_before: int
    improvements: str
    law: str
    law_url: str
    freq_after: int
    sev_after: int
    source: str = ""

    @property
    def score_before(self) -> int:
        return self.freq_before * self.sev_before

    @property
    def score_after(self) -> int:
        return self.freq_after * self.sev_after


def risk_grade(score: int) -> tuple[str, str, str]:
    for rng, grade, level, action in GRADE_TABLE:
        if score in rng:
            return grade, level, action
    return "F급", "무시될 수 있는 위험성", "현재 대책 유지"


def format_risk_cell(freq: int, sev: int) -> str:
    score = freq * sev
    grade, level, _ = risk_grade(score)
    return f"빈도: {freq} | 강도: {sev} | 점수: {score} | 등급: {grade}({level})"


def _load_scenarios() -> list[dict]:
    path = DATA_DIR / "jsa_scenarios.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _context_text(job: str, five_m: dict[str, str]) -> str:
    parts = [job] + [v for v in five_m.values() if v]
    return " ".join(parts).lower()


def _match_scenarios(context: str, scenarios: list[dict]) -> list[RiskRow]:
    rows: list[RiskRow] = []
    seen: set[tuple] = set()
    for s in scenarios:
        if not any(kw.lower() in context for kw in s.get("keywords", [])):
            continue
        key = (s["work_class"], s["phase"], s["unit_task"])
        if key in seen:
            continue
        seen.add(key)
        rows.append(RiskRow(
            work_class=s["work_class"],
            phase=s["phase"],
            unit_task=s["unit_task"],
            hazard=s["hazard"],
            injury=s["injury"],
            current=s["current"],
            freq_before=s["freq_before"],
            sev_before=s["sev_before"],
            improvements=s["improvements"],
            law=s["law"],
            law_url=s["law_url"],
            freq_after=s["freq_after"],
            sev_after=s["sev_after"],
            source=s.get("source", ""),
        ))
    return rows


def _generic_rows(job: str, context: str) -> list[RiskRow]:
    """키워드 매칭 시나리오가 없을 때 기본 평가 항목 생성"""
    rows = []
    base_tasks = [
        (
            "운전작업", "작업 전", "현장이동·작업준비",
            "작업 착수 전 안전보호구 미착용 상태에서 작업 시 안전사고 발생 우려",
            "넘어짐, 부딪힘",
            "보호구 지급만 되어 있고 착용 확인·관리가 미흡함",
            3, 1,
            "작업별 적정 보호구 착용 확인 후 작업을 시작할 것",
        ),
        (
            "운전작업", "작업 중", f"{job} 본작업",
            "본작업 수행 중 예상치 못한 유해·위험요인 노출 우려",
            "다양",
            "작업표준·위험요인 안내 미흡, 작업구역 통제 미실시",
            3, 3,
            "TBM 실시, 작업표준 준수, 작업구역 출입 통제할 것",
        ),
        (
            "운전작업", "작업 후", "작업장 정리·복구",
            "작업 종료 후 정리정돈 미실시로 통로 적치·넘어짐 재해 발생 우려",
            "넘어짐, 부딪힘",
            "공구·자재 방치, 작업통로 미확보",
            3, 2,
            "작업 종료 후 작업구역 정리정돈 실시 및 통로 확보할 것",
        ),
        (
            "정비작업", "작업 전", "작업 전 점검·LOTO",
            "잔류에너지·잔류압 미해제 상태에서 정비 착수 시 재해 발생 우려",
            "끼임, 감전",
            "LOTO·점검표 미실시",
            3, 3,
            "LOTO 6단계 실시 및 잔류에너지 해제 확인 후 작업할 것",
        ),
        (
            "정비작업", "작업 중", f"{job} 보수·정비",
            "정비 작업 중 공구·부품 낙하로 하부 작업자 피격 우려",
            "맞음, 떨어짐",
            "작업구역 통제·낙하방지 조치 미흡",
            3, 2,
            "작업구역 출입 통제 및 공구·부품 결속·낙하방지 조치할 것",
        ),
        (
            "돌발대응", "작업 중", f"{job} 비상대응",
            "화재·누출·부상자 등 비상상황 발생 시 2차 피해 확대 우려",
            "화재, 화학물질, 중대재해",
            "비상연락망·대응절차 미숙지",
            2, 4,
            "비상연락망 비치, 비상대응 훈련 실시 및 즉시 보고체계 운영할 것",
        ),
    ]
    for wc, ph, task, haz, inj, cur, f, s, imp in base_tasks:
        rows.append(RiskRow(
            work_class=wc, phase=ph, unit_task=task, hazard=haz, injury=inj,
            current=cur, freq_before=f, sev_before=s,
            improvements=imp,
            law="산업안전보건기준에 관한 규칙 제4조 (사업주의 의무)",
            law_url="https://www.law.go.kr/법령/산업안전보건기준에관한규칙/제4조",
            freq_after=max(1, f - 1), sev_after=max(1, s - 1),
        ))
    return rows


def _hazard_checklist_report(context: str) -> str:
    lines = ["## ■ 필수 위험점 검토 결과\n"]
    for hazard in HAZARD_CHECKLIST:
        kws = HAZARD_KEYWORDS.get(hazard, [hazard[:2]])
        matched = any(kw.lower() in context for kw in kws)
        mark = "✓ 해당" if matched else "○ 잠재(일반 검토)"
        lines.append(f"- {hazard}: {mark}")
    return "\n".join(lines)


def _sort_rows(rows: list[RiskRow]) -> list[RiskRow]:
    order_class = {"운전작업": 0, "정비작업": 1, "돌발대응": 2}
    order_phase = {"작업 전": 0, "작업 중": 1, "작업 후": 2}
    return sorted(rows, key=lambda r: (order_class.get(r.work_class, 9), order_phase.get(r.phase, 9)))


def _sort_rows_preserve_steps(rows: list[RiskRow]) -> list[RiskRow]:
    """문서 학습 행 — 작업단계(phase·unit_task) 순서 유지."""
    order_phase = {"작업 전": 0, "작업 중": 1, "작업 후": 2}
    return sorted(
        rows,
        key=lambda r: (order_phase.get(r.phase, 9), r.unit_task or ""),
    )


def _table_markdown(rows: list[RiskRow]) -> str:
    header = (
        "| 작업 분류 | JSA 단계 | 단위 작업 | 유해·위험요인 | 재해 유형 | "
        "현재 안전대책 | 위험성(개선 전) | 개선대책 | 법적 근거 | 위험성(개선 후) |\n"
        "| :---: | :---: | :--- | :--- | :---: | :--- | :---: | :--- | :--- | :---: |"
    )
    body_lines = []
    for r in rows:
        law_cell = f"[{r.law}]({r.law_url})"
        if r.source:
            law_cell += f" [출처: {r.source}]"
        imp = polish_improvement(r.improvements).replace("\n", "<br>")
        body_lines.append(
            f"| {r.work_class} | {r.phase} | {r.unit_task} | {polish_hazard(r.hazard)} | {r.injury} | "
            f"{r.current} | {format_risk_cell(r.freq_before, r.sev_before)} | {imp} | "
            f"{law_cell} | {format_risk_cell(r.freq_after, r.sev_after)} |"
        )
    return header + "\n" + "\n".join(body_lines)


def _summary(rows: list[RiskRow]) -> str:
    high = [r for r in rows if r.score_before >= 12]
    lines = ["## ■ 종합 의견\n"]
    if high:
        lines.append(f"- **즉시 개선 필요(A~C급) 항목: {len(high)}건**")
        for r in high[:5]:
            g, _, _ = risk_grade(r.score_before)
            lines.append(f"  - [{g}] {r.unit_task}: {r.hazard}")
    else:
        lines.append("- A~C급 고위험 항목 없음. 지속적 관리 필요.")
    improved = sum(1 for r in rows if r.score_after < r.score_before)
    lines.append(f"- 개선대책 적용 시 {improved}/{len(rows)}건 위험성 점수 하향 예상")
    lines.append("- 본 보고서는 **로컬 전용 모드**로 생성되었으며, 현장 특성에 맞게 보완·승인 후 사용하세요.")
    return "\n".join(lines)


def _apply_laws(rows: list[RiskRow]) -> list[RiskRow]:
    """동일 유해·위험요인은 웹검색 1회만 수행 (캐시)"""
    from dataclasses import replace

    from app.law_catalog import normalize_law
    from app.law_web_search import lookup_law

    cache: dict[tuple, tuple[str, str]] = {}
    out: list[RiskRow] = []
    for row in rows:
        if row.law != LAW_DEFAULT[0] and row.source != "JSA-PPT":
            law, url = normalize_law(row.law, row.law_url)
            out.append(replace(row, law=law, law_url=url))
            continue
        sig = (row.hazard[:80], row.injury[:30], row.unit_task[:40])
        if sig not in cache:
            law, url, _ = lookup_law(
                hazard=row.hazard,
                injury=row.injury,
                improvement=row.improvements,
                current=row.current,
                unit_task=row.unit_task,
            )
            cache[sig] = (law, url)
        law, url = normalize_law(*cache[sig])
        out.append(replace(row, law=law, law_url=url))
    return out


class LocalAssessmentEngine:
    """API 없이 JSA 위험성평가 보고서 생성"""

    def build_rows(
        self,
        job_name: str,
        five_m_one_e: dict[str, str],
    ) -> list[RiskRow]:
        job = job_name.strip() or "일반 작업"
        context = _context_text(job, five_m_one_e)

        # 소분류·문서 학습(ai_rows)이 있으면 학습 내용 그대로 재사용
        try:
            from app.work_type_lookup import WorkTypeLookup

            lookup = WorkTypeLookup()
            preset = lookup.find_learned_preset(job)
            ai_rows = (preset or {}).get("ai_rows") or []
            if ai_rows:
                learned_rows: list[RiskRow] = []
                for d in ai_rows:
                    if not isinstance(d, dict):
                        continue
                    learned_rows.append(
                        RiskRow(
                            work_class=str(d.get("work_class", "")),
                            phase=str(d.get("phase", "")),
                            unit_task=str(d.get("unit_task", "")),
                            hazard=str(d.get("hazard", "")),
                            injury=str(d.get("injury", "")),
                            current=str(d.get("current", "")),
                            freq_before=int(d.get("freq_before") or 0),
                            sev_before=int(d.get("sev_before") or 0),
                            improvements=str(d.get("improvements", "")),
                            law=str(d.get("law", LAW_DEFAULT[0])),
                            law_url=str(d.get("law_url", "")),
                            freq_after=int(d.get("freq_after") or 0),
                            sev_after=int(d.get("sev_after") or 0),
                            source=str(d.get("source") or "DOC-LEARN"),
                        )
                    )
                if learned_rows:
                    is_document = (preset or {}).get("source") == "document" or any(
                        str(d.get("source", "")).startswith("DOC") for d in ai_rows[:3]
                    )
                    if is_document:
                        return normalize_risk_rows(_sort_rows_preserve_steps(learned_rows))
                    return normalize_risk_rows(
                        _sort_rows(_apply_laws(ensure_work_phases(learned_rows, job)))
                    )
        except Exception:
            # 학습 데이터 파싱 실패 시 기존 로직으로 폴백
            pass

        field_rows = build_field_rows(job, five_m_one_e, context)
        if field_rows:
            if any(r.source == "JSA-PPT" for r in field_rows):
                return normalize_risk_rows(_sort_rows(_apply_laws(ensure_work_phases(field_rows, job))))
            scenarios = _load_scenarios()
            extra = _match_scenarios(context, scenarios)
            merged = ensure_work_phases(_dedupe_rows(field_rows + extra), job)
            return normalize_risk_rows(_sort_rows(_apply_laws(merged)))

        scenarios = _load_scenarios()
        rows = _match_scenarios(context, scenarios)
        if len(rows) < 6:
            rows.extend(_generic_rows(job, context))
            rows = _sort_rows(_dedupe_rows(rows))
        return normalize_risk_rows(_apply_laws(ensure_work_phases(rows, job)))

    def generate(
        self,
        job_name: str,
        five_m_one_e: dict[str, str],
        safety_context: str = "",
        on_chunk: Optional[Callable[[str], None]] = None,
        text_formatter: Optional[Callable[..., str]] = None,
        **format_kwargs,
    ) -> str:
        job = job_name.strip() or "일반 작업"
        context = _context_text(job, five_m_one_e)
        rows = self.build_rows(job_name, five_m_one_e)

        if text_formatter:
            full = text_formatter(rows=rows, job_name=job, **format_kwargs)
        else:
            sections = [
                f"# JSA 기반 위험성평가 보고서\n",
                f"**생성일시:** {datetime.now():%Y-%m-%d %H:%M:%S}  ",
                f"**평가방식:** JSA (빈도-강도법)  ",
                f"**생성모드:** 로컬 전용 (API 불필요)\n",
                f"## ■ 평가 대상\n**작업/설비명:** {job}\n",
                "## ■ 5M 1E 입력 요약\n",
                format_five_m_one_e(five_m_one_e) or "(직접 입력 없음 — 작업명 기준 평가)",
                "\n",
                _hazard_checklist_report(context),
                "\n",
            ]
            if safety_context.strip():
                sections.extend(["## ■ 참고 자료 (안전 사이트 수집)\n", safety_context[:2500], "\n"])
            sections.extend([
                "## ■ JSA 위험성평가표\n",
                _table_markdown(rows),
                "\n",
                _summary(rows),
                "\n\n---\n② 추가 명령(1~7)을 사용하거나 『다른위험성평가』를 선택하세요.\n",
            ])
            full = "\n".join(sections)

        if on_chunk:
            chunk_size = 400
            for i in range(0, len(full), chunk_size):
                on_chunk(full[i : i + chunk_size])
        return full

    def generate_additional(
        self,
        command_num: int,
        job_name: str,
        assessment_context: str,
        five_m_one_e: dict[str, str] | None = None,
        rows: list[RiskRow] | None = None,
        major_name: str = "",
        on_chunk: Optional[Callable[[str], None]] = None,
        on_status: Optional[Callable[[str], None]] = None,
        user_question: str = "",
    ) -> str:
        from app.additional_commands import run_additional_command

        def status(msg: str) -> None:
            if on_status:
                on_status(msg)

        result = run_additional_command(
            command_num,
            job_name,
            rows,
            five_m_one_e or {},
            assessment_context,
            major_name=major_name,
            on_status=status,
            user_question=user_question,
        )
        if on_chunk:
            for i in range(0, len(result), 400):
                on_chunk(result[i : i + 400])
        return result


def _dedupe_rows(rows: list[RiskRow]) -> list[RiskRow]:
    seen: set[tuple] = set()
    out: list[RiskRow] = []
    for r in rows:
        key = (r.work_class, r.phase, r.unit_task)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out
