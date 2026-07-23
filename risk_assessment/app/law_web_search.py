"""법적근거 조회 — 사전 인덱스(즉시) → 캐시 → 로컬규칙 → (선택) 웹검색

평가 시 지연을 줄이려면 먼저 scripts/build_law_index.py 로 DB를 구축하세요.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import requests

from app.law_common import (
    ARTICLE_RE,
    RULE_ID,
    RULE_NAME,
    SKIP_HREF,
    article_url,
    confidence,
    extract_keywords,
    format_law,
    score_article,
)
from app.law_index import lookup_from_index
from app.law_lookup import LAW_DEFAULT, infer_law
from app.law_catalog import normalize_law
from app.hazard_law_scenarios import match_scenario_law

from app.runtime_paths import DATA_DIR

CACHE_PATH = DATA_DIR / "law_search_cache.json"


def _enabled() -> bool:
    return os.getenv("LAW_WEB_SEARCH", "0").strip().lower() in ("1", "true", "on", "yes")


def _law_oc() -> str:
    return os.getenv("LAW_GO_KR_OC", "").strip()


class LawWebSearcher:
    def __init__(self):
        self.cache: dict[str, dict] = self._load_cache()
        self._last_query_at = 0.0
        self.min_interval = float(os.getenv("LAW_WEB_SEARCH_INTERVAL", "0.35"))

    def _load_cache(self) -> dict:
        if CACHE_PATH.exists():
            try:
                return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_cache(self) -> None:
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            CACHE_PATH.write_text(json.dumps(self.cache, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _cache_key(self, hazard: str, injury: str, unit_task: str) -> str:
        return "|".join([
            hazard.strip()[:80],
            injury.strip()[:30],
            unit_task.strip()[:40],
        ])

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_query_at
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_query_at = time.time()

    def _law_go_ai_search(self, query: str) -> tuple[str, str] | None:
        oc = _law_oc()
        if not oc:
            return None
        self._throttle()
        url = (
            "https://www.law.go.kr/DRF/lawSearch.do"
            f"?OC={requests.utils.quote(oc)}&target=aiSearch&type=JSON&search=0"
            f"&query={requests.utils.quote(query)}&display=5"
        )
        try:
            resp = requests.get(url, timeout=15, headers={"User-Agent": "P-WIDE-Risk/3.0"})
            data = resp.json()
            if data.get("result") and "실패" in str(data.get("result", "")):
                return None
            items = data.get("AiSearch", data.get("law", []))
            if isinstance(items, dict):
                items = items.get("item", [])
            if not isinstance(items, list):
                items = [items] if items else []
            for item in items:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("법령명") or item.get("lsNm") or "")
                jo = str(item.get("조문번호") or item.get("joNo") or "")
                jo_title = str(item.get("조문제목") or item.get("joTit") or item.get("조문내용", "")[:30])
                if "산업안전" not in title and "산안" not in title:
                    continue
                num = re.sub(r"\D", "", jo) or ""
                if not num:
                    m = ARTICLE_RE.search(str(item.get("조문내용", "")))
                    if m:
                        num, jo_title = m.group(1), m.group(2)
                if num:
                    slug = re.sub(r"\s+", "", jo_title)[:20] if jo_title else ""
                    return (
                        format_law(RULE_NAME, num, jo_title or "관련 조항"),
                        article_url(RULE_ID, num, slug),
                    )
        except (requests.RequestException, json.JSONDecodeError, KeyError, TypeError):
            return None
        return None

    def _duckduckgo_search(self, query: str) -> list[dict]:
        self._throttle()
        try:
            from ddgs import DDGS
        except ImportError:
            try:
                from duckduckgo_search import DDGS
            except ImportError:
                return []
        try:
            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=10))
        except Exception:
            return []

    def _parse_web_articles(
        self,
        results: list[dict],
        keywords: list[str],
        hazard: str,
    ) -> tuple[str, str] | None:
        candidates: list[tuple[int, str, str, str]] = []

        for item in results:
            href = item.get("href") or item.get("link") or ""
            if not href or SKIP_HREF.search(href):
                continue
            if "law.go.kr" not in href:
                continue
            body = f"{item.get('title', '')} {item.get('body', '')}"
            for num, title in ARTICLE_RE.findall(body):
                if "산업안전" not in body and RULE_NAME not in body and RULE_ID not in href:
                    continue
                score = score_article(title, keywords, hazard)
                if score < 8:
                    continue
                slug = re.sub(r"\s+", "", title)[:24]
                candidates.append((score, num, title, article_url(RULE_ID, num, slug)))

            m = re.search(r"/제(\d+)조(?:\(([^)]+)\))?", href)
            if m and RULE_ID in href.replace(" ", ""):
                num = m.group(1)
                title = (m.group(2) or "").replace("%20", " ")
                score = score_article(title, keywords, hazard) if title else 8
                if score >= 8:
                    candidates.append((score, num, title or "관련 조항", href))

        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0], reverse=True)
        _, num, title, url = candidates[0]
        return format_law(RULE_NAME, num, title), url

    def _build_queries(self, keywords: list[str], hazard: str) -> list[str]:
        queries: list[str] = []
        short_kws = [k for k in keywords if len(k) <= 8 and k not in ("기타",)][:3]
        if short_kws:
            queries.append(f"site:law.go.kr {RULE_NAME} {' '.join(short_kws[:2])}")
            queries.append(f"site:law.go.kr 산업안전보건기준 {' '.join(short_kws[:2])}")
        if hazard:
            queries.append(f"site:law.go.kr {RULE_NAME} {hazard[:24]}")
        return list(dict.fromkeys(queries))

    def lookup(
        self,
        hazard: str = "",
        injury: str = "",
        improvement: str = "",
        current: str = "",
        unit_task: str = "",
    ) -> tuple[str, str, str]:
        """(법령명, URL, 출처) — 인덱스 우선, 웹검색은 선택"""
        scenario = match_scenario_law(hazard, injury, unit_task)
        if scenario:
            law, url = normalize_law(scenario[0], scenario[1])
            return law, url, f"유해위험시나리오({scenario[2]})"

        # 1) 사전 구축 법령 DB
        idx = lookup_from_index(hazard, injury, improvement, current, unit_task)
        if idx and idx[0]:
            law, url = normalize_law(idx[0], idx[1])
            return law, url, idx[2]

        key = self._cache_key(hazard, injury, unit_task)
        # 2) 이전 웹검색/평가 캐시
        if key in self.cache:
            c = self.cache[key]
            law, url = normalize_law(c["law"], c["url"])
            return law, url, c.get("source", "cache")

        keywords = extract_keywords(hazard, injury, unit_task, improvement)
        local_law, local_url = infer_law(hazard, injury, improvement, current, unit_task)

        web_law: str | None = None
        web_url: str | None = None
        source = "로컬규칙"

        # 3) (선택) 런타임 웹검색 — 기본 OFF
        if _enabled():
            queries = self._build_queries(keywords, hazard)

            if _law_oc():
                hit = self._law_go_ai_search(queries[0] if queries else hazard[:40])
                if hit:
                    web_law, web_url = hit
                    source = "법제처 API"

            if not web_law:
                for query in queries[:3]:
                    results = self._duckduckgo_search(query)
                    hit = self._parse_web_articles(results, keywords, hazard)
                    if hit:
                        web_law, web_url = hit
                        source = "웹검색(DDG)"
                        break

        if web_law and web_url:
            web_score = confidence(web_law, keywords, hazard)
            local_score = confidence(local_law, keywords, hazard)
            if web_score >= max(local_score, 10):
                law, url = web_law, web_url
            else:
                law, url = local_law, local_url
                source = "로컬규칙"
        else:
            law, url = local_law, local_url

        law, url = normalize_law(law, url)
        self.cache[key] = {"law": law, "url": url, "source": source}
        self._save_cache()
        return law, url, source


_searcher: LawWebSearcher | None = None


def lookup_law(
    hazard: str = "",
    injury: str = "",
    improvement: str = "",
    current: str = "",
    unit_task: str = "",
) -> tuple[str, str, str]:
    global _searcher
    if _searcher is None:
        _searcher = LawWebSearcher()
    return _searcher.lookup(hazard, injury, improvement, current, unit_task)
