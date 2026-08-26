# Shim: moved to backend/infrastructure/ai/smart_fallback_reasoner.py (ADR 0014, A14)
from backend.infrastructure.ai.smart_fallback_reasoner import *  # noqa: F401,F403,F405
from backend.infrastructure.ai.smart_fallback_reasoner import (  # noqa: F401
    OmegaConfig,
    SmartFallbackReasoner,
    _circuit_open,
    _next_key,
    _ProviderHealth,
    _ProviderSpec,
    _ranked_providers,
    _record_failure,
    _record_success,
)

__all__ = [
    "SmartFallbackReasoner",
    "OmegaConfig",
    "_ProviderSpec",
    "_ProviderHealth",
    "_ranked_providers",
    "_circuit_open",
    "_record_failure",
    "_record_success",
    "_next_key",
]
