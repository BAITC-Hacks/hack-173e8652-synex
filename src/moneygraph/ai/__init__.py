"""Optional, privacy-minimizing AI assistance for MoneyGraph."""

from moneygraph.ai.base import AIProvider, AIResult, minimize_context
from moneygraph.ai.factory import build_provider

__all__ = ["AIProvider", "AIResult", "build_provider", "minimize_context"]
