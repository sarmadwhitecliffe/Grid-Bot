"""
Risk management module for bot_v2.

Components:
- CapitalManager: Single capital tracking with mode awareness
- AdaptiveIntegration: Wrapper around adaptive_risk_manager.py
"""

from .adaptive_integration import AdaptiveRiskIntegration
from .capital_manager import CapitalManager

__all__ = ["CapitalManager", "AdaptiveRiskIntegration"]
