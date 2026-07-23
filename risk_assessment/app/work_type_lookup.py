"""작업 표준명 기반 5M 1E 자동 검색·매칭"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from app.runtime_paths import DATA_DIR
from app.work_type_store import BUILTIN_PATH, UserPresetStore

if TYPE_CHECKING:
    from app.local_engine import RiskRow

GENERIC_TEMPLATE = {
    "Man": "{job} 작업 교육 이수자, 숙련 작업자 배치, 2인 1조·신호수 운영",
    "Machine": "{job} 관련 설비·공구·측정기, 정상 가동·점검 상태",
    "Material": "작업 중 취급 자재·에너지·유해·위험물질(해당 시 MSDS 확인)",
    "Method": "작업표준·안전작업허가·TBM 후 단계별 작업, 비상중지 절차",
    "Management": "작업 전·중·후 점검, 순회점검, 아차사고·재해 이력 반영",
    "Environment": "작업장 조명·통로·기상·분진·협소공간 등 현장 여건",
}


@dataclass
class MatchResult:
    preset: dict
    score: float
    reason: str


class WorkTypeLookup:
    def __init__(self):
        self._store = UserPresetStore()
        self._builtin_data = self._load_builtin()
        self.major_categories: list[dict] = self._builtin_data.get(
            "major_categories",
            [{"id": "civil", "name": "토건"}, {"id": "mechanical", "name": "기계"}, {"id": "electrical", "name": "전기"}],
        )
        self._builtin_presets: list[dict] = self._builtin_data.get("presets", [])
        self.reload()

    def _load_builtin(self) -> dict:
        for name in ("work_types.json", "presets.json"):
            path = DATA_DIR / name
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        if BUILTIN_PATH.exists():
            return json.loads(BUILTIN_PATH.read_text(encoding="utf-8"))
        return {"major_categories": [], "presets": []}

    def reload(self) -> None:
        """내장·사용자 소분류 다시 병합."""
        hidden = set(self._store.deleted_builtin_ids)
        builtin = [
            {**p, "source": "builtin"}
            for p in self._builtin_presets
            if p.get("id") not in hidden
        ]
        user = [{**p, "source": p.get("source", "user")} for p in self._store.presets]
        by_name: dict[str, dict] = {}
        for p in builtin + user:
            by_name[p["name"]] = p
        self.presets = list(by_name.values())

    def _load(self) -> dict:
        """하위 호환."""
        return self._builtin_data

    def major_names(self) -> list[str]:
        return [m["name"] for m in self.major_categories]

    def major_id(self, name: str) -> str | None:
        for m in self.major_categories:
            if m["name"] == name:
                return m["id"]
        return None

    def major_name_by_id(self, major_id: str) -> str:
        for m in self.major_categories:
            if m["id"] == major_id:
                return m["name"]
        return major_id

    def list_presets(self, major_name: str | None = None) -> list[dict]:
        if not major_name:
            return self.presets
        mid = self.major_id(major_name)
        if not mid:
            return self.presets
        return [p for p in self.presets if p.get("major_category") == mid]

    def preset_names(self, major_name: str | None = None) -> list[str]:
        return [p["name"] for p in self.list_presets(major_name)]

    def get_by_name(self, name: str) -> dict | None:
        name = name.strip()
        for p in self.presets:
            if p["name"] == name:
                return p
        return None

    def get_by_name_in_major(self, name: str, major_name: str | None) -> dict | None:
        """특정 대분류 안의 소분류만 조회."""
        name = name.strip()
        if not name:
            return None
        mid = self.major_id(major_name) if major_name else None
        for p in self.presets:
            if p.get("name") != name:
                continue
            if mid is None or p.get("major_category") == mid:
                return p
        return None

    def is_deletable(self, preset: dict) -> bool:
        return preset.get("source") in ("user", "learned", "document")

    def find_learned_preset(self, job_name: str) -> dict | None:
        """문서·AI 학습(ai_rows) 소분류 — 작업명·파일명·별칭 매칭."""
        from pathlib import Path

        job = job_name.strip()
        if not job:
            return None

        preset = self.get_by_name(job)
        if preset and preset.get("ai_rows"):
            return preset

        stem = Path(job).stem
        for p in self.presets:
            if not p.get("ai_rows"):
                continue
            if p.get("name") in (job, stem):
                return p
            learned = p.get("learned_from", "")
            if learned and Path(learned).stem in (job, stem):
                return p
            aliases = p.get("aliases") or []
            if job in aliases or stem in aliases:
                return p

        match = self.best_match(job, min_score=70.0)
        if match and match.preset.get("ai_rows"):
            return match.preset
        match2 = self.best_match(stem, min_score=70.0) if stem != job else None
        if match2 and match2.preset.get("ai_rows"):
            return match2.preset
        return None

    def enrich_five_m_one_e_from_similar(
        self,
        job_name: str,
        major_name: str | None = None,
        extracted: dict[str, str] | None = None,
        rows: list | None = None,
    ) -> tuple[dict[str, str], str]:
        """문서 추출 5M1E + 유사 소분류·위험행 키워드로 빈 항목 자동 보완."""
        fields = tuple(GENERIC_TEMPLATE.keys())
        merged = {k: (extracted or {}).get(k, "").strip() for k in fields}
        doc_filled = sum(1 for k in fields if merged[k])

        context_parts = [job_name.strip()]
        for item in rows or []:
            if isinstance(item, dict):
                d = item
            else:
                d = getattr(item, "__dict__", None) or {}
            for key in ("unit_task", "hazard", "improvements", "current", "injury"):
                v = str(d.get(key, "")).strip()
                if v and len(v) >= 2:
                    context_parts.append(v)
        context = " ".join(context_parts)

        ref_names: list[str] = []
        if doc_filled < len(fields):
            hits = self.search(context, major_name, limit=5)
            if not hits or hits[0].score < 25:
                hits = self.search(job_name, major_name, limit=5)
            for hit in hits:
                name = hit.preset.get("name", "")
                for field in fields:
                    if merged[field]:
                        continue
                    val = (hit.preset.get("five_m_one_e") or {}).get(field, "").strip()
                    if val:
                        merged[field] = val
                if name and name not in ref_names:
                    ref_names.append(name)

        missing = [k for k in fields if not merged[k]]
        if missing:
            generic = self.generate_generic(job_name)
            for k in missing:
                merged[k] = generic.get(k, "")

        for k in fields:
            doc_val = (extracted or {}).get(k, "").strip()
            if doc_val:
                merged[k] = doc_val

        if doc_filled >= len(fields):
            note = "문서에서 5M1E 추출"
        elif ref_names:
            note = f"유사 작업유형 참고 ({', '.join(ref_names[:3])})"
        else:
            note = "유사 유형·기본 템플릿으로 5M1E 생성"
        return merged, note

    def import_document_learn(
        self,
        job_name: str,
        five_m_one_e: dict[str, str],
        major_name: str,
        sub_category: str = "",
        rows: list | None = None,
        *,
        source_file: str = "",
        sheet_title: str = "",
        allow_update: bool = False,
    ) -> dict:
        """문서에서 추출한 작업·5M1E·평가행을 소분류에 학습 저장."""
        from pathlib import Path

        job = job_name.strip()
        if not job and source_file:
            job = Path(source_file).stem.strip()
        if not job:
            raise ValueError("작업명이 비어 있습니다.")

        mid = self.major_id(major_name)
        if not mid:
            raise ValueError(f"대분류 '{major_name}'를 찾을 수 없습니다. 대분류를 먼저 선택해 주세요.")

        existing_user = self._store.find_user_preset(job)
        if existing_user and existing_user.get("major_category") == mid and not allow_update:
            raise ValueError(
                f"『{job}』은(가) 이미 『{major_name}』 대분류에 등록되어 있습니다. "
                "'기존 작업 업데이트'를 선택하세요."
            )
        if existing_user and existing_user.get("major_category") != mid and not allow_update:
            other_major = self.major_name_by_id(existing_user.get("major_category", ""))
            raise ValueError(
                f"『{job}』은(가) 『{other_major or '다른'}』 대분류에 있습니다.\n"
                f"『{major_name}』 대분류로 옮기려면 '기존 작업 업데이트'를 선택하세요."
            )

        five_m, infer_note = self.enrich_five_m_one_e_from_similar(
            job, major_name, five_m_one_e, rows
        )
        preset = self._store.upsert_preset(
            job,
            five_m,
            mid,
            sub_category,
            source="document",
            rows=rows,
        )
        preset["five_m_infer_note"] = infer_note
        extra_aliases = [job, job.replace(" ", "")]
        if source_file:
            preset["learned_from"] = source_file
            extra_aliases.append(Path(source_file).name)
        if sheet_title:
            preset["excel_sheet"] = sheet_title
            extra_aliases.append(sheet_title)
        preset["aliases"] = list(dict.fromkeys((preset.get("aliases") or []) + extra_aliases))
        self._store.save()
        self.reload()
        return preset

    def add_preset(
        self,
        name: str,
        five_m_one_e: dict[str, str],
        major_name: str,
        sub_category: str = "",
        *,
        source: str = "user",
        rows: list[RiskRow] | None = None,
    ) -> dict:
        mid = self.major_id(major_name) or "civil"
        preset = self._store.upsert_preset(
            name, five_m_one_e, mid, sub_category, source=source, rows=rows
        )
        self.reload()
        return preset

    def learn_from_assessment(
        self,
        job_name: str,
        five_m_one_e: dict[str, str],
        major_name: str,
        sub_category: str = "",
        rows: list[RiskRow] | None = None,
    ) -> dict | None:
        """평가 완료 후 소분류에 자동 반영(학습)."""
        job = job_name.strip()
        if not job:
            return None
        return self.add_preset(
            job,
            five_m_one_e,
            major_name,
            sub_category,
            source="learned",
            rows=rows,
        )

    def delete_preset(self, name: str) -> tuple[bool, str]:
        """소분류 삭제 — 사용자/학습 항목 제거, 내장 항목은 숨김."""
        name = name.strip()
        if not name:
            return False, "삭제할 작업명을 선택하세요."

        preset = self.get_by_name(name)
        if not preset:
            return False, f"『{name}』을(를) 찾을 수 없습니다."

        if preset.get("source") == "builtin":
            pid = preset.get("id", "")
            if pid:
                self._store.hide_builtin(pid)
                self.reload()
                return True, f"『{name}』 목록에서 숨겼습니다. (내장 데이터는 유지)"
            return False, "삭제할 수 없는 항목입니다."

        if self._store.remove_user_preset(name):
            self.reload()
            return True, f"『{name}』을(를) 삭제했습니다."
        return False, "삭제에 실패했습니다."

    def move_preset_major(self, name: str, target_major_name: str) -> tuple[bool, str]:
        """선택한 소분류 작업을 다른 대분류로 이동."""
        from datetime import datetime

        name = name.strip()
        preset = self.get_by_name(name)
        if not preset:
            return False, f"『{name}』을(를) 찾을 수 없습니다."

        target_id = self.major_id(target_major_name)
        if not target_id:
            return False, f"대분류『{target_major_name}』를 확인해 주세요."

        current_major = self.major_name_by_id(preset.get("major_category", ""))
        if current_major == target_major_name:
            return False, f"이미『{target_major_name}』대분류에 있습니다."

        five_m = dict(preset.get("five_m_one_e", {}))
        sub = preset.get("sub_category", "")
        source = preset.get("source", "builtin")

        if source == "builtin":
            pid = preset.get("id", "")
            if pid:
                self._store.hide_builtin(pid)
            self.add_preset(name, five_m, target_major_name, sub, source="user")
        else:
            user_p = self._store.find_user_preset(name)
            if user_p:
                user_p["major_category"] = target_id
                user_p["updated_at"] = datetime.now().isoformat(timespec="seconds")
                self._store.save()
                self.reload()
            else:
                self.add_preset(name, five_m, target_major_name, sub, source=source)

        return True, f"『{name}』을(를)『{target_major_name}』대분류로 이동했습니다."

    def is_new_subcategory_name(self, name: str) -> bool:
        """소분류 목록에 없는 신규 작업명인지."""
        return self.get_by_name(name.strip()) is None

    def search(self, query: str, major_name: str | None = None, limit: int = 15) -> list[MatchResult]:
        query = query.strip()
        pool = self.list_presets(major_name)
        if not query:
            return []
        q = query.lower()
        q_tokens = set(re.findall(r"[\w가-힣]+", q))
        results: list[MatchResult] = []

        for p in pool:
            score, reason = self._score_preset(p, q, q_tokens)
            if score > 0:
                results.append(MatchResult(p, score, reason))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    def best_match(self, query: str, major_name: str | None = None, min_score: float = 8.0) -> MatchResult | None:
        hits = self.search(query, major_name, limit=1)
        if hits and hits[0].score >= min_score:
            return hits[0]
        return None

    def _score_preset(self, preset: dict, q: str, q_tokens: set[str]) -> tuple[float, str]:
        name = preset.get("name", "")
        name_l = name.lower()
        if name_l == q:
            return 100.0, "작업명 일치"
        if q in name_l or name_l in q:
            return 85.0, "작업명 유사"

        for alias in preset.get("aliases", []):
            al = alias.lower()
            if al == q:
                return 95.0, f"별칭 일치 ({alias})"
            if q in al or al in q:
                return 80.0, f"별칭 유사 ({alias})"

        keywords = preset.get("keywords", [])
        kw_hit = [k for k in keywords if k.lower() in q or any(k.lower() in t for t in q_tokens)]
        if kw_hit:
            return 50.0 + len(kw_hit) * 8, f"키워드 ({', '.join(kw_hit[:3])})"

        name_tokens = set(re.findall(r"[\w가-힣]+", name_l))
        overlap = q_tokens & name_tokens
        if overlap:
            return 30.0 + len(overlap) * 5, f"단어 일치 ({', '.join(list(overlap)[:3])})"

        return 0.0, ""

    def generate_generic(self, job_name: str) -> dict[str, str]:
        job = job_name.strip() or "일반 작업"
        base = {k: v.format(job=job) for k, v in GENERIC_TEMPLATE.items()}
        hits = self.search(job, limit=3)
        if not hits:
            return base

        merged = dict(base)
        for field in ("Man", "Machine", "Material", "Method", "Management", "Environment"):
            parts = [merged[field]]
            for hit in hits:
                val = hit.preset.get("five_m_one_e", {}).get(field, "")
                if val and val not in parts[0]:
                    parts.append(val)
            merged[field] = " / ".join(dict.fromkeys(parts))
        return merged

    def auto_fill(
        self, job_name: str, major_name: str | None = None
    ) -> tuple[dict[str, str], str, dict | None]:
        job = job_name.strip()
        if not job:
            return {}, "작업(표준)명을 입력해 주세요.", None

        match = self.best_match(job, major_name)
        if match:
            return (
                dict(match.preset.get("five_m_one_e", {})),
                f"『{match.preset['name']}』 매칭 ({match.reason})",
                match.preset,
            )

        weak = self.search(job, major_name, limit=1)
        if weak and weak[0].score >= 40:
            p = weak[0].preset
            return (
                dict(p.get("five_m_one_e", {})),
                f"『{p['name']}』 유사 매칭 ({weak[0].reason}) — 필요 시 수정하세요.",
                p,
            )

        if major_name:
            pool = self.list_presets(major_name)
            if pool:
                p = pool[0]
                return (
                    dict(p.get("five_m_one_e", {})),
                    f"『{job}』 — {major_name} 대표 유형 참고 입력. 수정하세요.",
                    None,
                )

        return (
            self.generate_generic(job),
            f"『{job}』 기본 5M1E 생성 — 현장 조건에 맞게 수정하세요.",
            None,
        )
