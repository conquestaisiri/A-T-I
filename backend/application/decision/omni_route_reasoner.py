# Shim: moved to backend/infrastructure/ai/omni_route_reasoner.py (ADR 0014, A14)
from backend.infrastructure.ai.omni_route_reasoner import *  # noqa: F401,F403,F405
from backend.infrastructure.ai.omni_route_reasoner import AiOmniRouteReasoner, OmniRouteConfig

__all__ = ["AiOmniRouteReasoner", "OmniRouteConfig"]
