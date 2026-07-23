# risk_assessment/engine.py
"""P-WIDE 위험성평가 — 로컬 JSA/법령 DB 매칭 엔진."""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
PROMPTS = ROOT / "prompts"

GRADE_LABELS = {
    1: "무시가능",
    2: "경미",
    3: "보통",
    4: "중대",
    5: "치명적",
}


def _load_json(name: str) -> Any:
    path = DATA / name
    if not path.exists():
        return {} if name.endswith(".json") else None
    return json.loads(path.read_text(encoding="utf-8"))


def risk_grade(freq: int, sev: int) -> int:
    """빈도×강도 → 위험등급(1~5)."""
    f = max(1, min(5, int(freq or 1)))
    s = max(1, min(5, int(sev or 1)))
    score = f * s
    if score <= 3:
        return 1
    if score <= 6:
        return 2
    if score <= 10:
        return 3
    if score <= 16:
        return 4
    return 5


def grade_label(grade: int) -> str:
    return GRADE_LABELS.get(int(grade), str(grade))


class RiskAssessmentEngine:
    def __init__(self) -> None:
        self.work_types = _load_json("work_types.json") or {}
        self.presets = (_load_json("presets.json") or {}).get("presets", [])
        user = _load_json("user_presets.json") or {}
        self.user_presets = user.get("presets", [])
        self.scenarios = _load_json("jsa_scenarios.json") or []
        self.maint = (_load_json("jsa_maintenance_library.json") or {}).get("jobs", {})
        self.ppt = (_load_json("jsa_ppt_library.json") or {}).get("jobs", {})
        self.dc = (_load_json("jsa_datacenter_library.json") or {}).get("jobs", {})
        self.hazard_laws = (_load_json("hazard_law_scenarios.json") or {}).get(
            "scenarios", []
        )
        self.law_catalog = _load_json("law_article_catalog.json") or {}
        self.law_index = _load_json("law_article_index.json") or {}

    def major_categories(self) -> list[dict]:
        return list(self.work_types.get("major_categories") or [])

    def all_presets(self) -> list[dict]:
        merged = list(self.presets) + list(self.user_presets)
        # work_types presets are richer
        for p in self.work_types.get("presets") or []:
            merged.append(p)
        # dedupe by id
        seen: set[str] = set()
        out = []
        for p in merged:
            pid = str(p.get("id") or p.get("name") or "")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            out.append(p)
        return out

    def presets_by_major(self, major_id: str = "") -> list[dict]:
        items = self.all_presets()
        if not major_id:
            return items
        return [p for p in items if p.get("major_category") == major_id or not p.get("major_category")]

    def find_preset(self, preset_id: str) -> dict | None:
        for p in self.all_presets():
            if str(p.get("id")) == str(preset_id):
                return p
        return None

    def _corpus(self, work_name: str, five_m: dict[str, str]) -> str:
        parts = [work_name or ""]
        for k in ("Man", "Machine", "Material", "Method", "Management", "Environment"):
            parts.append(five_m.get(k) or "")
        return " ".join(parts)

    def _keyword_score(self, text: str, keywords: list[str]) -> int:
        t = text.lower()
        score = 0
        for kw in keywords or []:
            if not kw:
                continue
            if str(kw).lower() in t:
                score += 2
            # partial token
            for tok in re.split(r"[\s·,/]+", str(kw)):
                if len(tok) >= 2 and tok.lower() in t:
                    score += 1
        return score

    def _job_library_hits(self, work_name: str) -> list[dict]:
        """정비/PPT/데이터센터 라이브러리에서 작업명 유사 매칭."""
        name = (work_name or "").strip()
        if not name:
            return []
        pools: list[tuple[str, dict]] = []
        for label, jobs in (
            ("정비", self.maint),
            ("PPT", self.ppt),
            ("데이터센터", self.dc),
        ):
            for job_name, rows in (jobs or {}).items():
                score = 0
                if name in job_name or job_name in name:
                    score = 10
                else:
                    for tok in re.split(r"[\s/·,\-]+", name):
                        if len(tok) >= 2 and tok in job_name:
                            score += 2
                if score >= 4:
                    pools.append((job_name, {"source": label, "score": score, "rows": rows}))
        pools.sort(key=lambda x: x[1]["score"], reverse=True)
        out: list[dict] = []
        for job_name, meta in pools[:3]:
            for row in meta["rows"][:12]:
                freq_b, sev_b = 3, 3
                if "밀폐" in job_name or "감전" in (row.get("hazard") or ""):
                    sev_b = 4
                if "추락" in (row.get("hazard") or "") or "고소" in job_name:
                    sev_b = 4
                out.append(
                    {
                        "work_class": "정비작업",
                        "phase": row.get("phase") or "작업 중",
                        "unit_task": row.get("unit_task") or job_name,
                        "hazard": (row.get("hazard") or "").lstrip("- ").strip(),
                        "injury": self._guess_injury(row.get("hazard") or ""),
                        "current": "작업표준·TBM·보호구 착용",
                        "freq_before": freq_b,
                        "sev_before": sev_b,
                        "improvements": (row.get("improvement") or row.get("improvements") or "")
                        .lstrip("- ")
                        .strip(),
                        "law": "",
                        "law_url": "",
                        "freq_after": max(1, freq_b - 1),
                        "sev_after": max(1, sev_b - 1),
                        "_score": meta["score"],
                        "_source": meta["source"],
                    }
                )
        return out

    def _guess_injury(self, hazard: str) -> str:
        mapping = [
            (("감전", "전기"), "감전"),
            (("추락", "낙하", "떨어"), "떨어짐"),
            (("협착", "끼임"), "끼임"),
            (("충돌", "부딪"), "부딪힘"),
            (("전도", "넘어"), "넘어짐"),
            (("화재", "폭발"), "화재·폭발"),
            (("절단", "베임"), "베임"),
            (("중독", "질식", "가스"), "중독·질식"),
            (("근골격", "과부하", "피로"), "근골격계"),
        ]
        for keys, label in mapping:
            if any(k in hazard for k in keys):
                return label
        return "기타"

    def _match_law(self, hazard: str, unit_task: str, injury: str) -> tuple[str, str]:
        text = f"{hazard} {unit_task} {injury}"
        best = None
        best_score = 0
        for sc in self.hazard_laws:
            score = 0
            for pat in sc.get("patterns") or []:
                try:
                    if re.search(pat, text):
                        score += 3
                except re.error:
                    if pat in text:
                        score += 2
            for pat in sc.get("injury_patterns") or []:
                if pat in text:
                    score += 1
            for pat in sc.get("unit_patterns") or []:
                if pat in text:
                    score += 1
            score += int(sc.get("priority") or 0) // 50
            if score > best_score:
                best_score = score
                best = sc
        if not best or best_score < 2:
            return ("산업안전보건기준에 관한 규칙 제4조 (사업주의 의무)",
                    "https://www.law.go.kr/법령/산업안전보건기준에관한규칙/제4조")
        law_key = str(best.get("law_key") or "")
        art_no = law_key.replace("rule_", "")
        articles = (self.law_catalog or {}).get("articles") or {}
        title = articles.get(art_no) or articles.get(str(art_no)) or best.get("situation") or ""
        law_name = f"산업안전보건기준에 관한 규칙 제{art_no}조"
        if title:
            law_name = f"{law_name} ({title})"
        url = f"https://www.law.go.kr/법령/산업안전보건기준에관한규칙/제{art_no}조"
        # enrich from index if present
        idx = self.law_index.get("articles") if isinstance(self.law_index, dict) else None
        if isinstance(idx, dict) and art_no in idx:
            info = idx[art_no]
            if isinstance(info, dict):
                if info.get("title"):
                    law_name = f"산업안전보건기준에 관한 규칙 제{art_no}조 ({info['title']})"
                if info.get("url"):
                    url = info["url"]
        return law_name, url

    def assess_local(
        self,
        work_name: str,
        five_m: dict[str, str],
        max_rows: int = 18,
    ) -> list[dict]:
        text = self._corpus(work_name, five_m)
        scored: list[dict] = []

        for sc in self.scenarios:
            score = self._keyword_score(text, sc.get("keywords") or [])
            if score <= 0:
                continue
            row = dict(sc)
            row["_score"] = score
            scored.append(row)

        scored.extend(self._job_library_hits(work_name))

        # 기본 시나리오가 너무 적으면 작업명 키워드로 라이브러리 전체 약식 추가
        if len(scored) < 6:
            for token in re.split(r"[\s/·,\-]+", work_name or ""):
                if len(token) < 2:
                    continue
                for lib in (self.maint, self.ppt, self.dc):
                    for job_name, rows in (lib or {}).items():
                        if token in job_name:
                            for row in rows[:4]:
                                scored.append(
                                    {
                                        "work_class": "정비작업",
                                        "phase": row.get("phase") or "작업 중",
                                        "unit_task": row.get("unit_task") or job_name,
                                        "hazard": (row.get("hazard") or "").lstrip("- ").strip(),
                                        "injury": self._guess_injury(row.get("hazard") or ""),
                                        "current": "작업표준·TBM·보호구",
                                        "freq_before": 3,
                                        "sev_before": 3,
                                        "improvements": (
                                            row.get("improvement") or ""
                                        ).lstrip("- ").strip(),
                                        "law": "",
                                        "law_url": "",
                                        "freq_after": 2,
                                        "sev_after": 2,
                                        "_score": 2,
                                    }
                                )

        scored.sort(key=lambda r: int(r.get("_score") or 0), reverse=True)

        # dedupe by hazard+phase
        seen: set[str] = set()
        rows: list[dict] = []
        for raw in scored:
            key = f"{raw.get('phase')}|{raw.get('hazard')}"
            if key in seen:
                continue
            seen.add(key)
            fb = int(raw.get("freq_before") or 3)
            sb = int(raw.get("sev_before") or 3)
            fa = int(raw.get("freq_after") or max(1, fb - 1))
            sa = int(raw.get("sev_after") or max(1, sb - 1))
            law = raw.get("law") or ""
            law_url = raw.get("law_url") or ""
            if not law:
                law, law_url = self._match_law(
                    raw.get("hazard") or "",
                    raw.get("unit_task") or "",
                    raw.get("injury") or "",
                )
            gb = risk_grade(fb, sb)
            ga = risk_grade(fa, sa)
            rows.append(
                {
                    "work_class": raw.get("work_class") or "작업",
                    "phase": raw.get("phase") or "작업 중",
                    "unit_task": raw.get("unit_task") or work_name,
                    "hazard": raw.get("hazard") or "",
                    "injury": raw.get("injury") or self._guess_injury(raw.get("hazard") or ""),
                    "current": raw.get("current") or "작업표준 준수",
                    "freq_before": fb,
                    "sev_before": sb,
                    "grade_before": gb,
                    "grade_before_label": grade_label(gb),
                    "improvements": raw.get("improvements") or "",
                    "law": law,
                    "law_url": law_url,
                    "freq_after": fa,
                    "sev_after": sa,
                    "grade_after": ga,
                    "grade_after_label": grade_label(ga),
                }
            )
            if len(rows) >= max_rows:
                break

        if not rows:
            # fallback minimal rows
            for phase, hazard, injury in (
                ("작업 전", "작업 전 위험요인 미파악·교육 미실시", "기타"),
                ("작업 중", f"{work_name or '해당'} 작업 중 충돌·협착·넘어짐", "부딪힘"),
                ("작업 후", "정리정돈 미흡으로 전도·낙하", "넘어짐"),
            ):
                gb = risk_grade(3, 3)
                ga = risk_grade(2, 2)
                law, url = self._match_law(hazard, work_name, injury)
                rows.append(
                    {
                        "work_class": "점검/정비",
                        "phase": phase,
                        "unit_task": work_name or "일반작업",
                        "hazard": hazard,
                        "injury": injury,
                        "current": "TBM, 보호구, 작업표준",
                        "freq_before": 3,
                        "sev_before": 3,
                        "grade_before": gb,
                        "grade_before_label": grade_label(gb),
                        "improvements": "[관리적] 작업 전 위험성평가·TBM 강화\n[보호구] 안전모·안전화·장갑",
                        "law": law,
                        "law_url": url,
                        "freq_after": 2,
                        "sev_after": 2,
                        "grade_after": ga,
                        "grade_after_label": grade_label(ga),
                    }
                )
        return rows

    def assess_ai(
        self,
        work_name: str,
        five_m: dict[str, str],
    ) -> list[dict] | None:
        """OpenAI API가 있으면 AI 평가, 없으면 None."""
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            return None
        try:
            import urllib.request

            prompt_path = PROMPTS / "condition1_web_assessment.txt"
            system = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else (
                "산업안전 JSA 위험성평가 전문가로서 마크다운 표만 출력한다."
            )
            user_msg = (
                f"작업명: {work_name}\n"
                f"Man: {five_m.get('Man','')}\n"
                f"Machine: {five_m.get('Machine','')}\n"
                f"Material: {five_m.get('Material','')}\n"
                f"Method: {five_m.get('Method','')}\n"
                f"Management: {five_m.get('Management','')}\n"
                f"Environment: {five_m.get('Environment','')}\n"
            )
            model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
            body = json.dumps(
                {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_msg},
                    ],
                    "temperature": 0.2,
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                "https://api.openai.com/v1/chat/completions",
                data=body,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            return self._parse_markdown_table(content) or None
        except Exception as e:
            print(f"[risk] AI assess failed: {e}", flush=True)
            return None

    def _parse_markdown_table(self, md: str) -> list[dict]:
        rows: list[dict] = []
        for line in (md or "").splitlines():
            line = line.strip()
            if not line.startswith("|"):
                continue
            cols = [c.strip() for c in line.strip("|").split("|")]
            if len(cols) < 6:
                continue
            if set(cols[0]) <= {"-", ":"} or "유해" in cols[0] or "단계" in cols[0]:
                continue
            # flexible column mapping
            hazard = cols[2] if len(cols) > 2 else cols[0]
            injury = cols[3] if len(cols) > 3 else ""
            try:
                freq_b = int(re.sub(r"\D", "", cols[4]) or "3")
            except ValueError:
                freq_b = 3
            try:
                sev_b = int(re.sub(r"\D", "", cols[5]) or "3")
            except ValueError:
                sev_b = 3
            improvements = cols[6] if len(cols) > 6 else ""
            law = cols[7] if len(cols) > 7 else ""
            law_url = ""
            m = re.search(r"https?://\S+", law)
            if m:
                law_url = m.group(0).rstrip(")")
            gb = risk_grade(freq_b, sev_b)
            ga = risk_grade(max(1, freq_b - 1), max(1, sev_b - 1))
            rows.append(
                {
                    "work_class": cols[0],
                    "phase": cols[1] if len(cols) > 1 else "작업 중",
                    "unit_task": cols[1] if len(cols) > 1 else "",
                    "hazard": hazard,
                    "injury": injury,
                    "current": "작업표준·TBM",
                    "freq_before": freq_b,
                    "sev_before": sev_b,
                    "grade_before": gb,
                    "grade_before_label": grade_label(gb),
                    "improvements": improvements,
                    "law": law,
                    "law_url": law_url,
                    "freq_after": max(1, freq_b - 1),
                    "sev_after": max(1, sev_b - 1),
                    "grade_after": ga,
                    "grade_after_label": grade_label(ga),
                }
            )
        return rows[:20]

    def assess(
        self,
        work_name: str,
        five_m: dict[str, str],
        use_ai: bool = False,
    ) -> tuple[list[dict], str]:
        mode = "local"
        rows = None
        if use_ai:
            rows = self.assess_ai(work_name, five_m)
            if rows:
                mode = "ai"
        if not rows:
            rows = self.assess_local(work_name, five_m)
            mode = "local"
        return rows, mode


@lru_cache(maxsize=1)
def get_engine() -> RiskAssessmentEngine:
    return RiskAssessmentEngine()
