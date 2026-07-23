# risk_assessment/__init__.py
"""P-WIDE 위험성평가 웹 엔진 (데스크톱 배포판 데이터 기반)."""
from .engine import RiskAssessmentEngine, get_engine

__all__ = ["RiskAssessmentEngine", "get_engine"]
