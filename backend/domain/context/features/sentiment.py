# backend/domain/context/features/sentiment.py
"""External sentiment feature from GDELT + FinBERT.

Reads cached sentiment scores from the SentimentService. The service runs
independently (background task) and caches FinBERT scores on GDELT news.
This feature is a pure reader — deterministic given the cache state.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, ClassVar

from backend.domain.context.context_feature import ContextFeature
from backend.domain.context.context_snapshot import ContextSnapshot

if TYPE_CHECKING:
    from backend.application.sentiment import (
        SentimentService,  # A2 waiver: TYPE_CHECKING-only port type
    )


class SentimentFeature:
    """Sentiment score from GDELT + FinBERT (-1 to +1)."""

    name: ClassVar[str] = "sentiment"

    _service_instance: ClassVar[SentimentService | None] = None

    def __init__(self, service: SentimentService | None = None) -> None:
        self._service = service

    @staticmethod
    def compute(
        snapshot: ContextSnapshot,
        parameters: Mapping[str, Any] | None = None,
    ) -> ContextFeature:
        """Read sentiment from the global service cache.

        The service is expected to be attached to the snapshot's context
        or available as a module-level singleton. For production, wire via
        dependency injection in the feature engine.
        """
        params = parameters or {}
        symbol = params.get("symbol", "BTC").upper()

        service = SentimentFeature._service_instance
        if service is None:
            return ContextFeature(
                name=SentimentFeature.name,
                value={
                    "sentiment_score": 0.0,
                    "positive": 0.33,
                    "neutral": 0.34,
                    "negative": 0.33,
                    "article_count": 0,
                    "cache_status": "unavailable",
                },
                computation_timestamp=snapshot.end_timestamp,
                execution_time=0.0,
            )

        cached = service.get_sentiment(symbol)
        if cached is None:
            return ContextFeature(
                name=SentimentFeature.name,
                value={
                    "sentiment_score": 0.0,
                    "positive": 0.33,
                    "neutral": 0.34,
                    "negative": 0.33,
                    "article_count": 0,
                    "cache_status": "cold",
                },
                computation_timestamp=snapshot.end_timestamp,
                execution_time=0.0,
            )

        return ContextFeature(
            name=SentimentFeature.name,
            value={
                "sentiment_score": cached["sentiment_score"],
                "positive": cached["positive"],
                "neutral": cached["neutral"],
                "negative": cached["negative"],
                "article_count": cached["article_count"],
                "cache_status": "warm",
            },
            computation_timestamp=snapshot.end_timestamp,
            execution_time=0.0,
        )


# Module-level singleton for the service (set at startup)
def set_service(service: SentimentService) -> None:
    """Set the global sentiment service instance."""
    SentimentFeature._service_instance = service
