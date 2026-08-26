# backend/domain/risk/__init__.py
"""Risk domain: the risk gate's verdict contract."""

from .risk_decision import RiskDecision, RiskVerdict

__all__ = ["RiskDecision", "RiskVerdict"]
