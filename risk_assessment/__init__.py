# risk_assessment/__init__.py
"""P-WIDE 위험성평가 — 원본 app 패키지 웹 브리지."""
from .web_bridge import (
    assess,
    ai_key_masked,
    ai_ready,
    list_majors,
    list_presets,
    get_preset,
    learn_documents,
    register_assessment_locally,
    rows_to_dict,
    form_rows_to_dict,
    build_report_text,
    save_openai_settings,
)

__all__ = [
    "assess",
    "ai_key_masked",
    "ai_ready",
    "list_majors",
    "list_presets",
    "get_preset",
    "learn_documents",
    "register_assessment_locally",
    "rows_to_dict",
    "form_rows_to_dict",
    "build_report_text",
    "save_openai_settings",
]
