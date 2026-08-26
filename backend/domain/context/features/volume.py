# backend/domain/context/features/volume.py
"""Deterministic volume feature aggregating trade quantities."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, ClassVar

from backend.domain.context.context_feature import ContextFeature
from backend.domain.context.context_snapshot import ContextSnapshot
from backend.domain.context.features._utils import extract_volumes, lookback_slice


class VolumeFeature:
    """Total and average trade volume over a lookback window."""

    name: ClassVar[str] = "volume"

    @staticmethod
    def compute(
        snapshot: ContextSnapshot,
        parameters: Mapping[str, Any] | None = None,
    ) -> ContextFeature:
        params = parameters or {}
        lookback = int(params.get("lookback", 10))

        start = time.perf_counter()
        volumes = lookback_slice(extract_volumes(snapshot), lookback)
        if not volumes:
            raise ValueError("VolumeFeature requires at least 1 trade volume observation")

        total_volume = sum(volumes)
        average_volume = total_volume / len(volumes)
        # Most recent print vs window average: a genuine thin-participation
        # signal (total/average is trivially the trade count, not informative).
        last_volume = volumes[-1]
        volume_ratio = last_volume / average_volume if average_volume > 0 else 1.0

        return ContextFeature(
            name=VolumeFeature.name,
            value={
                "total_volume": round(total_volume, 8),
                "average_volume": round(average_volume, 8),
                "trade_count": len(volumes),
                "last_volume": round(last_volume, 8),
                "volume_ratio": round(volume_ratio, 6),
            },
            computation_timestamp=snapshot.end_timestamp,
            execution_time=time.perf_counter() - start,
        )
