"""사용자 소분류(작업유형) — 등록·학습·삭제 저장"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.runtime_paths import DATA_DIR
USER_PRESETS_PATH = DATA_DIR / "user_presets.json"
BUILTIN_PATH = DATA_DIR / "work_types.json"

_RISK_ROW_KEYS = [
    "work_class",
    "phase",
    "unit_task",
    "hazard",
    "injury",
    "current",
    "freq_before",
    "sev_before",
    "improvements",
    "law",
    "law_url",
    "freq_after",
    "sev_after",
    "source",
]


def _slug(text: str, max_len: int = 36) -> str:
    s = re.sub(r"[^\w가-힣]+", "_", text.strip())
    return (s[:max_len] or "work").strip("_")


def _tokenize(name: str) -> list[str]:
    parts = re.findall(r"[\w가-힣]{2,}", name)
    return list(dict.fromkeys(parts))[:12]


def _keywords_from_rows(rows: list[Any], limit: int = 8) -> list[str]:
    kws: list[str] = []
    for row in rows:
        for text in (row.hazard, row.injury, row.unit_task):
            for p in _tokenize(text):
                if len(p) >= 2 and p not in kws:
                    kws.append(p)
                if len(kws) >= limit:
                    return kws
    return kws


def _serialize_risk_rows(rows: list[Any], *, limit: int = 30) -> list[dict]:
    """AI 모드에서 생성된 RiskRow를 JSON으로 저장."""
    out: list[dict] = []
    if not rows:
        return out
    for r in rows[:limit]:
        if isinstance(r, dict):
            d = dict(r)
        else:
            d = getattr(r, "__dict__", None) or {}
        item = {k: d.get(k, "") for k in _RISK_ROW_KEYS}
        # 숫자형 캐스팅(없으면 0으로)
        for nk in ("freq_before", "sev_before", "freq_after", "sev_after"):
            try:
                item[nk] = int(item.get(nk) or 0)
            except (TypeError, ValueError):
                item[nk] = 0
        out.append(item)
    return out


class UserPresetStore:
    def __init__(self):
        self._data = self._load()

    def _load(self) -> dict:
        if USER_PRESETS_PATH.exists():
            try:
                return json.loads(USER_PRESETS_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {"presets": [], "deleted_builtin_ids": []}

    def save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        USER_PRESETS_PATH.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @property
    def presets(self) -> list[dict]:
        return self._data.setdefault("presets", [])

    @property
    def deleted_builtin_ids(self) -> list[str]:
        return self._data.setdefault("deleted_builtin_ids", [])

    def find_user_preset(self, name: str) -> dict | None:
        name = name.strip()
        for p in self.presets:
            if p.get("name") == name:
                return p
        return None

    def upsert_preset(
        self,
        name: str,
        five_m_one_e: dict[str, str],
        major_id: str,
        sub_category: str = "",
        *,
        source: str = "user",
        rows: list[Any] | None = None,
    ) -> dict:
        name = name.strip()
        if not name:
            raise ValueError("작업명이 비어 있습니다.")

        keywords = _tokenize(name)
        if rows:
            keywords = list(dict.fromkeys(keywords + _keywords_from_rows(rows)))[:16]
            ai_rows = _serialize_risk_rows(rows)
        else:
            ai_rows = []

        existing = self.find_user_preset(name)
        if existing:
            preset = existing
            preset["five_m_one_e"] = {k: v for k, v in five_m_one_e.items() if v}
            preset["major_category"] = major_id
            preset["sub_category"] = sub_category or preset.get("sub_category", "")
            preset["keywords"] = keywords
            preset["aliases"] = list(dict.fromkeys(preset.get("aliases", []) + [name.replace(" ", "")]))
            preset["updated_at"] = datetime.now().isoformat(timespec="seconds")
            preset["source"] = source
            if ai_rows:
                preset["ai_rows"] = ai_rows
        else:
            pid = f"user_{major_id}_{_slug(name)}"
            base = [p.get("id") for p in self.presets]
            if pid in base:
                pid = f"{pid}_{len(base)}"
            preset = {
                "id": pid,
                "major_category": major_id,
                "sub_category": sub_category,
                "name": name,
                "description": f"{sub_category} - {name}" if sub_category else name,
                "keywords": keywords,
                "aliases": [name.replace(" ", ""), name],
                "five_m_one_e": {k: v for k, v in five_m_one_e.items() if v},
                "source": source,
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
            if ai_rows:
                preset["ai_rows"] = ai_rows
            self.presets.append(preset)

        self.save()
        return preset

    def remove_user_preset(self, name: str) -> bool:
        name = name.strip()
        before = len(self.presets)
        self.presets[:] = [p for p in self.presets if p.get("name") != name]
        if len(self.presets) < before:
            self.save()
            return True
        return False

    def hide_builtin(self, preset_id: str) -> None:
        if preset_id and preset_id not in self.deleted_builtin_ids:
            self.deleted_builtin_ids.append(preset_id)
            self.save()

    def is_builtin_hidden(self, preset_id: str) -> bool:
        return preset_id in self.deleted_builtin_ids
