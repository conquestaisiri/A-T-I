# backend/domain/memory/__init__.py
"""Episodic memory domain: one durable market outcome per episode.

Follows the Constitution Document 05 memory model (Hermes-style bounded
memory): ATI remembers *market outcomes*, not conversations. Each
:class:`MemoryEpisode` is one decision and its realised result, stored behind
the ``MemoryStore`` port.
"""

from .episode import MemoryEpisode, MemoryOutcome

__all__ = ["MemoryEpisode", "MemoryOutcome"]
