# backend/domain/validation/adwin_config.py
"""Domain-owned ADWIN hyper-parameters (layering fix for edge_monitor).

The drift detector's config is a domain contract; application owns the detector
implementation but must not own the contract type.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AdwinConfig:
    delta: float = 0.002
    max_window: int = 10_000
    min_window: int = 10

    def __post_init__(self) -> None:
        if not 0.0 < self.delta < 1.0:
            raise ValueError("delta must be in (0, 1)")
        if self.max_window < 1:
            raise ValueError("max_window must be >= 1")
        if self.min_window < 1:
            raise ValueError("min_window must be >= 1")
        if self.min_window > self.max_window:
            raise ValueError("min_window cannot exceed max_window")
