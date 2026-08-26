# Shim: moved to backend/infrastructure/ai/prompt_builder.py (ADR 0014)
from backend.infrastructure.ai.prompt_builder import *  # noqa: F401,F403,F405
from backend.infrastructure.ai.prompt_builder import (  # noqa: F401
    DEFAULT_RECALL_LIMIT,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_messages,
    build_payload,
)

__all__ = [  # noqa: F405
    "build_messages",
    "build_payload",
    "SYSTEM_PROMPT",
    "PROMPT_VERSION",
    "DEFAULT_RECALL_LIMIT",
]
