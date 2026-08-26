# backend/application/ai/__init__.py
"""AI reasoning adapters (PydanticAI, OmniRoute, etc.).

Heavy integrations (pydantic-ai, openai) are loaded lazily so that importing
``backend.application.ai`` does not require optional dependencies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.application.ai.pydantic_ai_reasoner import (
        PydanticAIConfig,
        PydanticAIReasoner,
    )

__all__ = [
    "PydanticAIReasoner",
    "PydanticAIConfig",
]


def __getattr__(name: str) -> object:  # noqa: ANN001
    if name in __all__:
        from backend.application.ai.pydantic_ai_reasoner import (
            PydanticAIConfig,
            PydanticAIReasoner,
        )

        _exports = {
            "PydanticAIReasoner": PydanticAIReasoner,
            "PydanticAIConfig": PydanticAIConfig,
        }
        return _exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
