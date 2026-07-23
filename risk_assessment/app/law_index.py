"""사전 구축 법령 인덱스 — 평가 시 즉시 조회 (웹검색 없음)"""

from __future__ import annotations

import json
import os
from pathlib import Path

from app.law_common import (
    extract_keywords,
    score_article,
)
from app.hazard_law_scenarios import match_scenario_law

from app.runtime_paths import DATA_DIR

INDEX_PATH = DATA_DIR / "law_article_index.json"


class LawIndex:
    def __init__(self, path: Path | None = None):
        self.path = path or INDEX_PATH
        self.data = self._load()
        self.articles: list[dict] = self.data.get("articles", [])
        self.query_cache: dict[str, dict] = self.data.get("query_cache", {})

    def _load(self) -> dict:
        if not self.path.exists():
            return {"articles": [], "query_cache": {}}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"articles": [], "query_cache": {}}

    @property
    def ready(self) -> bool:
        return bool(self.articles or self.query_cache)

    @property
    def meta(self) -> dict:
        return {
            "updated_at": self.data.get("updated_at", ""),
            "article_count": len(self.articles),
            "cache_count": len(self.query_cache),
        }

    def _cache_key(self, hazard: str, injury: str, unit_task: str) -> str:
        return "|".join([
            hazard.strip()[:80],
            injury.strip()[:30],
            unit_task.strip()[:40],
        ])

    def _lookup_cache(self, hazard: str, injury: str, unit_task: str) -> tuple[str, str] | None:
        key = self._cache_key(hazard, injury, unit_task)
        if key in self.query_cache:
            c = self.query_cache[key]
            return c.get("law", ""), c.get("url", "")
        return None

    def _lookup_articles(
        self,
        hazard: str,
        injury: str,
        keywords: list[str],
    ) -> tuple[str, str] | None:
        if not self.articles:
            return None
        best_score = 0
        best: dict | None = None
        for art in self.articles:
            title = art.get("title", "")
            art_kws = art.get("keywords", [])
            merged_kws = list(dict.fromkeys(keywords + art_kws))
            s = score_article(title, merged_kws, hazard)
            if injury and injury.split(",")[0].strip() in title:
                s += 10
            if s > best_score:
                best_score = s
                best = art
        if best and best_score >= 12:
            return best.get("law", ""), best.get("url", "")
        return None

    def lookup(
        self,
        hazard: str = "",
        injury: str = "",
        improvement: str = "",
        current: str = "",
        unit_task: str = "",
    ) -> tuple[str, str, str] | None:
        """인덱스에서 조항 찾기 — (law, url, source) 또는 None"""
        scenario = match_scenario_law(hazard, injury, unit_task)
        if scenario:
            return scenario[0], scenario[1], f"유해위험시나리오({scenario[2]})"

        if not self.ready:
            return None

        hit = self._lookup_cache(hazard, injury, unit_task)
        if hit and hit[0]:
            return hit[0], hit[1], "법령DB(캐시)"

        keywords = extract_keywords(hazard, injury, unit_task, improvement)
        hit = self._lookup_articles(hazard, injury, keywords)
        if hit and hit[0]:
            return hit[0], hit[1], "법령DB(조문)"

        return None

    def find_candidates(
        self,
        hazard: str = "",
        injury: str = "",
        improvement: str = "",
        current: str = "",
        unit_task: str = "",
        limit: int = 8,
    ) -> list[dict]:
        """유사도 순 법령 후보 (GPT 법적근거 매칭용)."""
        keywords = extract_keywords(hazard, injury, unit_task, improvement)
        scored: list[tuple[int, dict]] = []

        for art in self.articles:
            title = art.get("title", "")
            art_kws = art.get("keywords", [])
            merged = list(dict.fromkeys(keywords + art_kws))
            s = score_article(title, merged, hazard)
            if injury and injury.split(",")[0].strip() in title:
                s += 10
            if s > 0:
                scored.append((s, {
                    "law": art.get("law", ""),
                    "url": art.get("url", ""),
                    "title": title,
                    "score": s,
                }))

        scored.sort(key=lambda x: x[0], reverse=True)
        seen: set[str] = set()
        out: list[dict] = []
        for _, item in scored:
            key = item["law"]
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(item)
            if len(out) >= limit:
                break
        return out


_index: LawIndex | None = None


def get_law_index() -> LawIndex:
    global _index
    if _index is None:
        _index = LawIndex()
    return _index


def index_ready() -> bool:
    return get_law_index().ready


def lookup_from_index(
    hazard: str = "",
    injury: str = "",
    improvement: str = "",
    current: str = "",
    unit_task: str = "",
) -> tuple[str, str, str] | None:
    return get_law_index().lookup(hazard, injury, improvement, current, unit_task)


def find_law_candidates(
    hazard: str = "",
    injury: str = "",
    improvement: str = "",
    current: str = "",
    unit_task: str = "",
    limit: int = 8,
) -> list[dict]:
    return get_law_index().find_candidates(
        hazard, injury, improvement, current, unit_task, limit
    )
