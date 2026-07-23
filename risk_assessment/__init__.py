# risk_assessment/__init__.py
"""P-WIDE 위험성평가 — 원본 app 패키지 웹 브리지."""
from .web_bridge import (
    assess,
    list_majors,
    list_presets,
    get_preset,
    rows_to_dict,
    form_rows_to_dict,
    build_report_text,
)

__all__ = [
    "assess",
    "list_majors",
    "list_presets",
    "get_preset",
    "rows_to_dict",
    "form_rows_to_dict",
    "build_report_text",
]
