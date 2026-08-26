# backend/application/reflection/__init__.py
"""Reflection layer: turn trade outcomes into episodic memory.

Implements the "reflection should update this memory" rule from the
Constitution (Document 05). Reads closed trades from the ledger, joins the
original proposal, derives a win/loss/flat outcome, and records a bounded
MemoryEpisode so the reasoner can recall it on later decisions.
"""

from .reflection_service import ReflectionService, ReflectionStats

__all__ = ["ReflectionService", "ReflectionStats"]
