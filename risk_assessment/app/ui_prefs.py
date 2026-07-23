"""화면 입력값 저장 — 부서명·섹션·평가담당·적용구분"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from app.runtime_paths import DATA_DIR

PREFS_PATH = DATA_DIR / "ui_prefs.json"


@dataclass
class FormPrefs:
    department: str = ""
    section: str = ""
    evaluator: str = ""
    apply_type: str = "정기평가"

    @classmethod
    def load(cls) -> FormPrefs:
        if not PREFS_PATH.exists():
            return cls()
        try:
            data = json.loads(PREFS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls()
        return cls(
            department=str(data.get("department", data.get("group", ""))).strip(),
            section=str(data.get("section", "")).strip(),
            evaluator=str(data.get("evaluator", "")).strip(),
            apply_type=str(data.get("apply_type", "정기평가")).strip() or "정기평가",
        )

    def save(self) -> None:
        PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        PREFS_PATH.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
