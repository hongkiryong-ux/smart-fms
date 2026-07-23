"""안전 전문 사이트 자료 수집 — 작업(표준)명 기준 유사 검색 + 링크 리포트"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from html import unescape
from typing import Optional, Callable
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from app.prompts import COMMAND_SEARCH_TOPIC, SAFETY_SOURCES, SAFETY_SOURCE_SEARCH

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
TIMEOUT = 12
RULE_PAGE = "https://www.law.go.kr/법령/산업안전보건기준에%20관한%20규칙"
RULE_ID = "산업안전보건기준에관한규칙"

KOSHA_CATEGORIES = {
    "1": "산업안전보건법",
    "2": "산업안전보건법 시행령",
    "3": "산업안전보건법 시행규칙",
    "4": "산업안전보건기준에 관한 규칙",
    "5": "고시·안내서",
    "6": "미디어·자료",
    "7": "판례·해석",
    "8": "중대재해처벌법",
    "9": "중대재해처벌법 시행령",
    "10": "기타",
    "11": "기타",
}

_GENERIC = frozenset(
    "작업 운전 점검 정비 및 설비 일반 표준 안전 보건 위험성 평가".split()
)

# 3번 — 안전·산재 사고사례만 통과 (시공사례·공사실적 등 제외)
_NON_ACCIDENT_KEYWORDS = (
    "시공사례", "시공 사례", "공사사례", "시공실적", "납품실적", "포트폴리오",
    "레퍼런스", "reference", "우수사례", "준공", "신축공사", "리모델링",
    "인테리어", "건축사례", "공사현장", "납품사례", "적용사례", "설치사례",
    "시공정보", "공법소개", "시공사진", "공사 실적", "시공 사진", "공사사진",
    "construction case", "project case", "case study", "납품 현장",
)

_ACCIDENT_KEYWORDS = (
    "산재", "산업재해", "안전사고", "중대재해", "재해", "사망", "부상", "사상",
    "추락", "끼임", "감전", "화재", "폭발", "재해사례", "아차사고", "중대사고",
    "사고 발생", "사고조사", "재발방지", "사고 원인", "안전사고사례", "작업중사고",
    "중대재해알림", "재해통계", "사고보도", "사고 경위", "사고 예방", "재해 예방",
    "산업안전사고", "안전사고 사례", "사고사례", "사고 사례",
)

_ACCIDENT_TRUSTED_SOURCES = frozenset({
    "중대재해알림e", "뉴스(네이버)", "뉴스(연합)", "뉴스(뉴스1)", "뉴스·언론",
})


def _text_has_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    return any(kw in text for kw in keywords)


def is_safety_accident_item(item: SafetyItem) -> bool:
    """안전·산재 사고 관련 결과만 True (시공사례 등 일반 사례 제외)"""
    blob = f"{item.title} {item.snippet} {item.url}"
    if _text_has_keyword(blob, _NON_ACCIDENT_KEYWORDS):
        if not _text_has_keyword(blob, _ACCIDENT_KEYWORDS):
            return False
    if item.source in _ACCIDENT_TRUSTED_SOURCES:
        return _text_has_keyword(blob, _ACCIDENT_KEYWORDS) or "사고" in blob or "산재" in blob
    return _text_has_keyword(blob, _ACCIDENT_KEYWORDS)


@dataclass
class SafetyItem:
    source: str
    title: str
    url: str
    snippet: str = ""
    match_note: str = ""
    score: float = 0.0


@dataclass
class SafetyReport:
    job_name: str
    keywords: str
    search_terms: list[str] = field(default_factory=list)
    items: list[SafetyItem] = field(default_factory=list)
    local_notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    fetched_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M"))

    def to_text(self, max_chars: int = 12000) -> str:
        return self.format_report()[:max_chars]

    def format_report(self) -> str:
        lines: list[str] = [
            "═" * 58,
            "  안전자료 수집 결과",
            f"  작업(표준)명 : {self.job_name or '(미입력)'}",
            f"  검색 키워드 : {self.keywords}",
        ]
        if self.search_terms:
            lines.append(f"  검색어 후보 : {', '.join(self.search_terms[:4])}")
        lines.extend([
            f"  수집 시각   : {self.fetched_at}",
            "═" * 58,
            "",
        ])

        if self.local_notes:
            lines.append("■ 프로그램 내 유사 작업 · JSA 참고")
            lines.extend(self.local_notes)
            lines.append("")

        grouped: dict[str, list[SafetyItem]] = {}
        for item in self.items:
            grouped.setdefault(item.source, []).append(item)

        section = 1
        for source_name, _ in SAFETY_SOURCES:
            bucket = grouped.get(source_name, [])
            if not bucket:
                continue
            lines.append(f"■ {section}. {source_name}")
            search_url = next((u for n, u in SAFETY_SOURCE_SEARCH if n == source_name), "")
            if search_url:
                lines.append(f"  🔗 검색/바로가기: {search_url.format(q=quote(self.keywords))}")
            lines.append("")
            for i, it in enumerate(bucket[:8], 1):
                note = f"  ({it.match_note})" if it.match_note else ""
                lines.append(f"  [{i}] {it.title}{note}")
                if it.snippet:
                    lines.append(f"      {it.snippet[:220]}")
                lines.append(f"      🔗 {it.url}")
                lines.append("")
            section += 1

        extra_sources = [s for s in grouped if s not in {n for n, _ in SAFETY_SOURCES}]
        for src in extra_sources:
            lines.append(f"■ {section}. {src}")
            for i, it in enumerate(grouped[src][:6], 1):
                lines.append(f"  [{i}] {it.title}")
                if it.snippet:
                    lines.append(f"      {it.snippet[:200]}")
                lines.append(f"      🔗 {it.url}")
            lines.append("")
            section += 1

        lines.append("■ 참고 사이트 전체 목록")
        for name, url in SAFETY_SOURCES:
            lines.append(f"  • {name}")
            lines.append(f"    {url}")
        lines.append("")

        if self.errors:
            lines.append("■ 수집 참고 (일부 사이트 접속 제한)")
            for err in self.errors:
                lines.append(f"  - {err}")
            lines.append("")

        lines.append("─" * 58)
        lines.append("※ 위 링크는 공식 사이트 검색·조문 페이지입니다. 최신 내용은 각 사이트에서 확인하세요.")
        return "\n".join(lines)


class SafetyDataFetcher:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def fetch_all(self, job_name: str, major_name: str | None = None) -> SafetyReport:
        job = (job_name or "").strip()
        terms = self._search_terms(job)
        keywords = terms[0] if terms else "위험성평가 산업안전"
        report = SafetyReport(job_name=job, keywords=keywords, search_terms=terms)

        report.local_notes = self._search_local(job, major_name)

        jobs = [
            ("안전보건공단", self._fetch_kosha),
            ("산업안전보건기준에 관한 규칙", self._fetch_rule_articles),
            ("법무부 안전관련법규", self._fetch_law_portal),
            ("고용노동부", self._fetch_moel),
            ("중대재해처벌법 관련", self._fetch_koshahub),
            ("위험성평가(KRAS)", self._fetch_kras),
            ("중대재해알림e", self._fetch_sasttc),
        ]
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(fn, terms, job): src for src, fn in jobs}
            for fut in as_completed(futures):
                src = futures[fut]
                try:
                    items, err = fut.result()
                    report.items.extend(items)
                    if err:
                        report.errors.append(f"{src}: {err}")
                except Exception as exc:
                    report.errors.append(f"{src}: {exc}")

        report.items.sort(key=lambda x: x.score, reverse=True)
        return report

    def fetch_command(
        self,
        command_num: int,
        job_name: str,
        major_name: str | None = None,
        *,
        user_question: str = "",
    ) -> SafetyReport:
        """추가 명령 1~7 — 작업명 맞춤 검색 (버튼 클릭 시)"""
        job = (job_name or "").strip()
        base_terms = self._search_terms(job)
        pk = self._primary_keyword(base_terms)
        uq = (user_question or "").strip()
        topic = COMMAND_SEARCH_TOPIC.get(command_num, "")

        extra: list[str] = []
        if command_num == 1:
            extra = [
                f"{job} {topic}".strip(),
                f"{job} 작업표준", f"{job} 안전작업", f"{pk} JSA", f"{pk} SOP",
            ]
        elif command_num == 2:
            extra = [
                f"{job} {topic}".strip(),
                f"{job} 기술자료", f"{job} 기술표준", f"{job} 작업표준",
                f"{pk} MSDS", f"{pk} 설비",
            ]
        elif command_num == 3:
            extra = [
                f"{job} {topic}".strip(),
                f"{job} 산재", f"{job} 안전사고", f"{job} 산업재해",
                f"{pk} 재해", f"{pk} 중대재해",
            ]
        elif command_num == 4:
            extra = [
                f"{job} {topic}".strip(),
                f"{job} 위험성평가", "위험성평가 진행단계", f"{pk} JSA",
            ]
        elif command_num == 5:
            extra = [
                f"{job} {topic}".strip(),
                f"{pk} 산업안전보건", f"{job} 산안법", f"{pk} 안전보건기준",
            ]
        elif command_num == 6:
            extra = [
                f"{job} {topic}".strip(),
                f"{job} 안전교육", f"{pk} 특별교육", "고용노동부 산업안전 교육",
            ]
        elif command_num == 7:
            if uq:
                extra = [uq, f"{job} {uq}".strip(), f"{pk} {uq}".strip(), f"{uq} 산업안전"]
            else:
                extra = [f"{job} 안전", f"{pk} 산업안전", f"{job} 위험성"]

        terms = list(dict.fromkeys(base_terms + [t for t in extra if t.strip()]))[:12]
        search_query = uq or (f"{job} {topic}".strip() if topic and job else "")
        keywords = search_query or (terms[0] if terms else (job or "위험성평가"))
        report = SafetyReport(job_name=job, keywords=keywords, search_terms=terms)

        if command_num in (1, 2, 3, 4, 5, 6, 7):
            report.local_notes = self._search_local(job, major_name)

        jobs: list[tuple[str, Callable]] = []
        portal_topic = topic or uq or "산업안전"
        if command_num == 3:
            portal_topic = "안전사고 산재"

        if command_num in range(1, 7):
            accident_only = command_num == 3
            pt = portal_topic
            jobs.extend([
                ("네이버", lambda t=pt, ao=accident_only: self._fetch_naver_web(job, t, accident_only=ao)),
                ("다음", lambda t=pt, ao=accident_only: self._fetch_daum_web(job, t, accident_only=ao)),
            ])
            if command_num == 3:
                jobs.append(
                    ("뉴스·언론", lambda: self._fetch_news_accidents(terms, job)),
                )

        if command_num == 1:
            jobs.extend([
                ("안전보건공단", lambda: self._fetch_kosha(terms, job)),
                ("고용노동부", lambda: self._fetch_moel(terms, job)),
            ])
        elif command_num == 2:
            jobs.extend([
                ("안전보건공단", lambda: self._fetch_kosha(terms, job)),
                ("산업안전보건기준에 관한 규칙", lambda: self._fetch_rule_articles(terms, job)),
                ("법무부 안전관련법규", lambda: self._fetch_law_portal(terms, job)),
                ("고용노동부", lambda: self._fetch_moel(terms, job)),
            ])
        elif command_num == 3:
            jobs.extend([
                ("중대재해알림e", lambda: self._fetch_sasttc(terms, job)),
                ("고용노동부", lambda: self._fetch_moel(terms, job)),
                ("안전보건공단", lambda: self._fetch_kosha(terms, job)),
            ])
        elif command_num == 4:
            jobs.extend([
                ("위험성평가(산업안전포털)", lambda: self._fetch_kras(terms, job)),
                ("고용노동부", lambda: self._fetch_moel(terms, job)),
                ("안전보건공단", lambda: self._fetch_kosha(terms, job)),
            ])
        elif command_num == 5:
            jobs.extend([
                ("산업안전보건기준에 관한 규칙", lambda: self._fetch_rule_articles(terms, job)),
                ("법무부 안전관련법규", lambda: self._fetch_law_portal(terms, job)),
                ("중대재해처벌법 관련", lambda: self._fetch_koshahub(terms, job)),
            ])
        elif command_num == 6:
            jobs.extend([
                ("고용노동부", lambda: self._fetch_moel(terms, job)),
                ("안전보건공단", lambda: self._fetch_kosha(terms, job)),
            ])
        elif command_num == 7:
            jobs = [
                ("안전보건공단", lambda: self._fetch_kosha(terms, job)),
                ("고용노동부", lambda: self._fetch_moel(terms, job)),
                ("중대재해알림e", lambda: self._fetch_sasttc(terms, job)),
            ]
            if uq:
                jobs.extend([
                    ("뉴스·언론", lambda: self._fetch_news_accidents(terms, job)),
                    ("산업안전보건기준에 관한 규칙", lambda: self._fetch_rule_articles(terms, job)),
                    ("법무부 안전관련법규", lambda: self._fetch_law_portal(terms, job)),
                ])
        else:
            jobs = [
                ("안전보건공단", lambda: self._fetch_kosha(terms, job)),
                ("고용노동부", lambda: self._fetch_moel(terms, job)),
                ("중대재해알림e", lambda: self._fetch_sasttc(terms, job)),
            ]

        with ThreadPoolExecutor(max_workers=min(6, len(jobs) or 1)) as pool:
            futures = {pool.submit(fn): src for src, fn in jobs}
            for fut in as_completed(futures):
                src = futures[fut]
                try:
                    items, err = fut.result()
                    report.items.extend(items)
                    if err:
                        report.errors.append(f"{src}: {err}")
                except Exception as exc:
                    report.errors.append(f"{src}: {exc}")

        report.items.sort(key=lambda x: x.score, reverse=True)
        if command_num == 3:
            report.items = self._filter_accident_items(report.items)
        return report

    def _filter_accident_items(self, items: list[SafetyItem]) -> list[SafetyItem]:
        return [i for i in items if is_safety_accident_item(i)]

    def _search_terms(self, job: str) -> list[str]:
        if not job:
            return ["위험성평가", "산업안전"]
        tokens = [t for t in re.findall(r"[\w가-힣]{2,}", job) if t not in _GENERIC]
        terms: list[str] = []
        if job:
            terms.append(job)
        if tokens:
            terms.append(tokens[0])
            if len(tokens) >= 2:
                terms.append(" ".join(tokens[:2]))
        return list(dict.fromkeys(terms))[:4]

    def _primary_keyword(self, terms: list[str]) -> str:
        if not terms:
            return "위험성평가"
        if len(terms) >= 2:
            return terms[1]
        return terms[0]

    def _search_local(self, job: str, major_name: str | None) -> list[str]:
        if not job:
            return ["  (작업명 미입력 — 아래는 키워드 기반 일반 검색 결과입니다.)"]

        notes: list[str] = []
        try:
            from app.work_type_lookup import WorkTypeLookup

            hits = WorkTypeLookup().search(job, major_name, limit=5)
            if hits:
                notes.append("  [소분류·작업표준 유사 매칭]")
                for h in hits[:5]:
                    notes.append(f"  ★ 『{h.preset.get('name', '')}』 — {h.reason} (점수 {h.score:.0f})")
                    fm = h.preset.get("five_m_one_e", {})
                    if fm.get("Method"):
                        notes.append(f"     Method: {fm['Method'][:100]}")
        except Exception:
            pass

        try:
            from app.field_content import match_jsa_ppt_job, _load_jsa_ppt_library

            key = match_jsa_ppt_job(job)
            library = _load_jsa_ppt_library()
            if key and key in library:
                notes.append(f"  [JSA 라이브러리] 『{key}』 ({len(library[key])}건)")
                for it in library[key][:4]:
                    haz = it.get("hazard", "")[:90]
                    inj = it.get("injury", "")
                    notes.append(f"  · {it.get('unit_task', '')[:30]} → {haz}")
                    if inj:
                        notes.append(f"    재해유형: {inj}")
        except Exception:
            pass

        if not notes:
            notes.append("  (프로그램 내 직접 일치 JSA 없음 — 외부 사이트 검색 결과를 참고하세요.)")
        return notes

    def _get(self, url: str, **kwargs) -> Optional[str]:
        try:
            resp = self.session.get(url, timeout=TIMEOUT, **kwargs)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text
        except requests.RequestException:
            return None

    def _get_json(self, url: str, params: dict | None = None) -> dict | None:
        try:
            resp = self.session.get(url, params=params, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") == "ERROR":
                return None
            return data
        except (requests.RequestException, ValueError):
            return None

    def _strip_html(self, text: str) -> str:
        text = unescape(re.sub(r"<[^>]+>", "", text or ""))
        return re.sub(r"\s+", " ", text).strip()

    def _article_url(self, title: str) -> str | None:
        m = re.search(r"제(\d+)조", title)
        if m:
            slug = re.sub(r"\s+", "", title.split("(")[0])[:24]
            return f"https://www.law.go.kr/법령/{RULE_ID}/제{m.group(1)}조({slug})"
        return None

    def _score_match(self, text: str, job: str, keywords: str) -> float:
        blob = text.lower()
        score = 0.0
        if job and job.lower() in blob:
            score += 40
        for tok in keywords.split():
            if len(tok) >= 2 and tok.lower() in blob:
                score += 12
        return score

    def _fetch_kosha(self, terms: list[str], job: str) -> tuple[list[SafetyItem], str | None]:
        for q in terms:
            items = self._kosha_query(q, job)
            if items:
                return items, None
        url = SAFETY_SOURCE_SEARCH[1][1].format(q=quote(self._primary_keyword(terms)))
        return [], "KOSHA 검색 결과 없음 — 검색 링크 참고"

    def _kosha_query(self, query: str, job: str) -> list[SafetyItem]:
        base = "https://smartsearch.kosha.or.kr"
        self._get(base)
        params = {
            "type": "detail",
            "category": "1,2,3,4,5,6,7,8,9,10,11",
            "searchValue": query,
            "limit": 12,
            "skip": 0,
            "sort": "asc",
            "stopword": "",
            "booleanyn": "1",
            "btnId": "srchAllBtn",
            "exinternal_code": "1",
        }
        data = self._get_json(f"{base}/wz/api/kosha/srch/smartSearch", params=params)
        if not data or "data" not in data:
            return []

        items: list[SafetyItem] = []
        seen: set[str] = set()
        payload = data["data"]
        if not payload.get("total"):
            return []

        for row in payload.get("result", [])[:15]:
            title = self._strip_html(row.get("title", ""))
            if not title or title in seen:
                continue
            seen.add(title)
            content = self._strip_html(row.get("highlight_content") or row.get("content", ""))
            cat = KOSHA_CATEGORIES.get(str(row.get("category", "")), "법령·자료")
            url = self._article_url(title) or f"{base}/?searchValue={quote(query)}"
            score = self._score_match(f"{title} {content}", job, query) + float(row.get("score", 0) or 0) * 0.1
            items.append(SafetyItem(
                source="안전보건공단",
                title=f"{title} ({cat})",
                url=url,
                snippet=content[:240],
                match_note="스마트검색",
                score=score,
            ))

        for row in payload.get("total_media", [])[:5]:
            title = self._strip_html(row.get("title", ""))
            if not title or title in seen:
                continue
            seen.add(title)
            paths = row.get("filepath") or []
            url = paths[0] if paths else f"{base}/?searchValue={quote(query)}"
            if isinstance(url, list):
                url = url[0] if url else base
            content = self._strip_html(row.get("highlight_content") or row.get("content", ""))
            score = self._score_match(f"{title} {content}", job, query)
            items.append(SafetyItem(
                source="안전보건공단",
                title=f"[자료] {title}",
                url=url,
                snippet=content[:200],
                match_note="미디어·OPL",
                score=score,
            ))

        items.sort(key=lambda x: x.score, reverse=True)
        return items[:10]

    def _fetch_rule_articles(self, terms: list[str], job: str) -> tuple[list[SafetyItem], str | None]:
        keywords = self._primary_keyword(terms)
        html = self._get(RULE_PAGE)
        if not html:
            return [], "법제처 페이지 접속 실패"

        text = BeautifulSoup(html, "lxml").get_text("\n")
        kws = list(dict.fromkeys([k for t in terms for k in t.split() if len(k) >= 2]))[:6]

        items: list[SafetyItem] = []
        for m in re.finditer(r"(제\d+조(?:\([^)]+\))?)\s*(.{0,120})", text):
            article = m.group(1)
            body = m.group(2)
            block = f"{article} {body}"
            if not any(k in block for k in kws):
                continue
            num = re.search(r"제(\d+)조", article)
            if not num:
                continue
            title = article.strip()
            slug = re.sub(r"\s+", "", title.split("(")[0])[:20]
            url = f"https://www.law.go.kr/법령/{RULE_ID}/제{num.group(1)}조({slug})"
            score = self._score_match(block, job, keywords)
            if score < 8:
                continue
            items.append(SafetyItem(
                source="산업안전보건기준에 관한 규칙",
                title=title,
                url=url,
                snippet=body.strip()[:200],
                match_note="조문 키워드 일치",
                score=score,
            ))
            if len(items) >= 8:
                break

        if not items:
            url = SAFETY_SOURCE_SEARCH[6][1].format(q=quote(keywords))
            items.append(SafetyItem(
                source="산업안전보건기준에 관한 규칙",
                title=f"『{keywords}』 관련 조문 검색",
                url=url,
                snippet="법제처에서 산업안전보건기준에 관한 규칙 전문을 확인하세요.",
                score=5,
            ))
        return items, None

    def _fetch_law_portal(self, terms: list[str], job: str) -> tuple[list[SafetyItem], str | None]:
        keywords = self._primary_keyword(terms)
        items: list[SafetyItem] = []
        for q in dict.fromkeys([keywords, f"산업안전보건 {keywords}"]):
            found = self._web_search_site("law.go.kr", q, job, "법무부 안전관련법규")
            items.extend(found[:4])
        if not items:
            url = f"https://www.law.go.kr/lsSc.do?menuId=1&subMenuId=15&tabMenuId=81&query={quote(keywords)}"
            items.append(SafetyItem(
                source="법무부 안전관련법규",
                title=f"법령 통합검색: {keywords}",
                url=url,
                snippet="국가법령정보센터에서 관련 법령·시행령·시행규칙을 검색합니다.",
                score=10,
            ))
        return items[:6], None

    def _fetch_moel(self, terms: list[str], job: str) -> tuple[list[SafetyItem], str | None]:
        keywords = self._primary_keyword(terms)
        items = self._web_search_site("moel.go.kr", f"산업안전 {keywords}", job, "고용노동부")
        if not items:
            url = f"https://www.moel.go.kr/info/lawinfo/law/LawSearch.do?searchKeyword={quote(keywords)}"
            items.append(SafetyItem(
                source="고용노동부",
                title=f"고용노동부 법령·고시 검색: {keywords}",
                url=url,
                snippet="고용노동부 산업안전보건 관련 법령정보·보도자료를 확인하세요.",
                score=8,
            ))
        return items[:5], None

    def _fetch_koshahub(self, terms: list[str], job: str) -> tuple[list[SafetyItem], str | None]:
        keywords = self._primary_keyword(terms)
        items = self._web_search_site("koshahub.or.kr", f"중대재해 {keywords}", job, "중대재해처벌법 관련")
        url = f"https://www.koshahub.or.kr/?is_keyword={quote(keywords)}"
        if not items:
            items.append(SafetyItem(
                source="중대재해처벌법 관련",
                title=f"KOSHA Hub 검색: {keywords}",
                url=url,
                snippet="중대재해처벌법·안전보건 관련 자료·Q&A를 검색합니다.",
                score=8,
            ))
        else:
            items.insert(0, SafetyItem(
                source="중대재해처벌법 관련",
                title=f"KOSHA Hub — {keywords}",
                url=url,
                snippet="중대재해처벌법 관련 자료 허브",
                score=15,
            ))
        return items[:6], None

    def _fetch_kras(self, terms: list[str], job: str) -> tuple[list[SafetyItem], str | None]:
        keywords = self._primary_keyword(terms)
        display = job or keywords
        portal = "https://www.safety-as.com"
        items = [
            SafetyItem(
                source="위험성평가(산업안전포털)",
                title=f"산업안전포털 위험성평가 — '{display}'",
                url=portal,
                snippet=(
                    "한국산업안전보건공단 산업안전포털에서 동종·유사 작업의 "
                    "위험성평가·TBM·아차사고 관리를 조회할 수 있습니다."
                ),
                match_note="KRAS 이관 포털",
                score=20,
            ),
            SafetyItem(
                source="위험성평가(산업안전포털)",
                title="위험성평가 시스템 안내",
                url="https://www.kosha.or.kr/kosha/business/bbsItem.do?menuId=554",
                snippet="위험성평가 이용 방법·매뉴얼",
                score=5,
            ),
        ]
        return items, None

    def _fetch_sasttc(self, terms: list[str], job: str) -> tuple[list[SafetyItem], str | None]:
        keywords = self._primary_keyword(terms)
        items = self._web_search_site("labor.moel.go.kr", f"중대재해 {keywords}", job, "중대재해알림e")
        base = "https://labor.moel.go.kr/sasttc/main/main.do"
        if not items:
            items.append(SafetyItem(
                source="중대재해알림e",
                title=f"중대재해알림e — {keywords} 관련 사고·법규",
                url=base,
                snippet="중대재해 사고 정보·법규 위반 알림 서비스 (고용노동부)",
                score=10,
            ))
        else:
            items.insert(0, SafetyItem(
                source="중대재해알림e",
                title="중대재해알림e 바로가기",
                url=base,
                snippet="중대재해 사고·법규 정보 통합 알림",
                score=12,
            ))
        return items[:5], None

    def _portal_query(self, job: str, topic: str) -> str:
        parts = [p for p in (job, topic) if p and p.strip()]
        return " ".join(parts).strip() or "산업안전"

    def _parse_portal_links(
        self,
        soup: BeautifulSoup,
        job: str,
        query: str,
        source: str,
        *,
        selectors: tuple[str, ...],
        max_items: int = 8,
    ) -> list[SafetyItem]:
        items: list[SafetyItem] = []
        seen: set[str] = set()
        for sel in selectors:
            for a in soup.select(sel):
                href = (a.get("href") or "").strip()
                title = self._strip_html(a.get_text())
                if not href or not title or href in seen:
                    continue
                if href.startswith("/") or not href.startswith("http"):
                    continue
                seen.add(href)
                score = self._score_match(title, job, query)
                items.append(SafetyItem(
                    source=source,
                    title=title[:120],
                    url=href,
                    snippet="",
                    match_note="포털검색",
                    score=score,
                ))
                if len(items) >= max_items:
                    break
            if items:
                break
        items.sort(key=lambda x: x.score, reverse=True)
        return items

    def _fetch_naver_web(
        self,
        job: str,
        topic: str,
        *,
        accident_only: bool = False,
    ) -> tuple[list[SafetyItem], str | None]:
        """네이버 통합검색 — 작업명·주제 기본 검색"""
        query = self._portal_query(job, topic)
        if accident_only:
            query = f"{query} 산재 안전사고"
        url = f"https://search.naver.com/search.naver?where={'news' if accident_only else 'webkr'}&sm=tab_jum&query={quote(query)}"
        items: list[SafetyItem] = []
        html = self._get(url)
        if html:
            soup = BeautifulSoup(html, "html.parser")
            items = self._parse_portal_links(
                soup, job, query, "네이버",
                selectors=(
                    "a.link_tit",
                    "a.title_link",
                    "a.api_txt_lines.total_tit",
                    "div.total_wrap a.link_tit",
                ),
            )
        if not items:
            ddg_q = f"{query} 산재 OR 안전사고" if accident_only else query
            items = self._web_search_ddg(
                f"{ddg_q} site:naver.com OR site:blog.naver.com OR site:news.naver.com",
                job, "네이버", max_results=6,
            )
        if not items:
            items.append(SafetyItem(
                source="네이버",
                title=f"네이버 {'뉴스' if accident_only else ''}검색: {query}",
                url=url,
                snippet="네이버에서 작업 관련 산재·안전사고 자료를 검색합니다." if accident_only
                else "네이버에서 작업명·주제 관련 자료를 검색합니다.",
                match_note="검색 링크",
                score=8,
            ))
        if accident_only:
            items = self._filter_accident_items(items)
        return items[:10], None

    def _fetch_daum_web(
        self,
        job: str,
        topic: str,
        *,
        accident_only: bool = False,
    ) -> tuple[list[SafetyItem], str | None]:
        """다음 통합검색 — 작업명·주제 기본 검색"""
        query = self._portal_query(job, topic)
        if accident_only:
            query = f"{query} 산재 안전사고"
        url = f"https://search.daum.net/search?w={'news' if accident_only else 'tot'}&q={quote(query)}"
        items: list[SafetyItem] = []
        html = self._get(url)
        if html:
            soup = BeautifulSoup(html, "html.parser")
            items = self._parse_portal_links(
                soup, job, query, "다음",
                selectors=(
                    "a.f_link_b",
                    "a.tit_main",
                    "c-title a",
                    "div.wrap_cont a.link_tit",
                ),
            )
        if not items:
            ddg_q = f"{query} 산재 OR 안전사고" if accident_only else query
            items = self._web_search_ddg(
                f"{ddg_q} site:daum.net OR site:tistory.com OR site:v.daum.net",
                job, "다음", max_results=6,
            )
        if not items:
            items.append(SafetyItem(
                source="다음",
                title=f"다음 {'뉴스' if accident_only else ''}검색: {query}",
                url=url,
                snippet="다음에서 작업 관련 산재·안전사고 자료를 검색합니다." if accident_only
                else "다음에서 작업명·주제 관련 자료를 검색합니다.",
                match_note="검색 링크",
                score=8,
            ))
        if accident_only:
            items = self._filter_accident_items(items)
        return items[:10], None

    def _fetch_news_accidents(
        self,
        terms: list[str],
        job: str,
    ) -> tuple[list[SafetyItem], str | None]:
        """뉴스·언론 — 산재·안전사고 보도 검색"""
        pk = self._primary_keyword(terms)
        items: list[SafetyItem] = []
        queries = [
            (f"site:news.naver.com {job} 산재", "뉴스(네이버)"),
            (f"site:news.naver.com {job} 안전사고", "뉴스(네이버)"),
            (f"site:yna.co.kr {job} 산업재해", "뉴스(연합)"),
            (f"site:news1.kr {job} 산재", "뉴스(뉴스1)"),
            (f"{job} 안전사고 산재", "뉴스·언론"),
            (f"{pk} 중대재해 사고", "뉴스·언론"),
        ]
        seen: set[str] = set()
        for query, label in queries:
            for item in self._web_search_ddg(query, job, label, max_results=4):
                if item.url in seen:
                    continue
                seen.add(item.url)
                items.append(item)
        if not items:
            url = f"https://search.naver.com/search.naver?where=news&query={quote(f'{job} 산재')}"
            items.append(SafetyItem(
                source="뉴스(네이버)",
                title=f"네이버 뉴스 검색: {job} 산재",
                url=url,
                snippet="네이버 뉴스에서 작업 관련 산재·안전사고 보도를 검색합니다.",
                match_note="검색 링크",
                score=12,
            ))
        items.sort(key=lambda x: x.score, reverse=True)
        return items[:12], None

    def _web_search_ddg(
        self,
        query: str,
        job: str,
        source_label: str,
        *,
        max_results: int = 6,
    ) -> list[SafetyItem]:
        try:
            from ddgs import DDGS
        except ImportError:
            try:
                from duckduckgo_search import DDGS
            except ImportError:
                return []

        items: list[SafetyItem] = []
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
        except Exception:
            return []

        for row in results:
            href = row.get("href") or row.get("link") or ""
            title = (row.get("title") or "").strip()
            body = (row.get("body") or "").strip()
            if not href or not title:
                continue
            score = self._score_match(f"{title} {body}", job, query)
            items.append(SafetyItem(
                source=source_label,
                title=title,
                url=href,
                snippet=body[:220],
                match_note="웹검색",
                score=score,
            ))
        items.sort(key=lambda x: x.score, reverse=True)
        return items

    def _web_search_site(
        self,
        site: str,
        query: str,
        job: str,
        source_label: str,
    ) -> list[SafetyItem]:
        try:
            from ddgs import DDGS
        except ImportError:
            try:
                from duckduckgo_search import DDGS
            except ImportError:
                return []

        items: list[SafetyItem] = []
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(f"site:{site} {query}", max_results=6))
        except Exception:
            return []

        for row in results:
            href = row.get("href") or row.get("link") or ""
            title = (row.get("title") or "").strip()
            body = (row.get("body") or "").strip()
            if not href or not title:
                continue
            score = self._score_match(f"{title} {body}", job, query)
            items.append(SafetyItem(
                source=source_label,
                title=title,
                url=href,
                snippet=body[:220],
                match_note="웹검색 유사",
                score=score,
            ))
        items.sort(key=lambda x: x.score, reverse=True)
        return items
