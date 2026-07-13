"""Search-space definitions for central policy parameter exploration.

The default grid is intentionally small and coarse. Policy search is a model
selection step, so a narrow space is safer than a highly flexible one while the
entity universe is still small.
"""
from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field
from typing import Iterator


@dataclass(frozen=True)
class SearchSpace:
    max_positions: list[int] = field(default_factory=lambda: [2, 3, 5])
    confidence_weight: list[float] = field(default_factory=lambda: [0.3, 0.5, 0.7])
    signal_strength_weight: list[float] = field(default_factory=lambda: [0.3, 0.5, 0.7])
    min_confidence: list[float] = field(default_factory=lambda: [0.0, 0.5, 1.0])
    confidence_metric: list[str] = field(default_factory=lambda: ["expectancy", "win_rate", "profit_factor"])
    position_sizing: list[str] = field(default_factory=lambda: ["equal", "score_weighted"])

    def grid(self) -> Iterator[dict]:
        keys = [
            "max_positions",
            "confidence_weight",
            "signal_strength_weight",
            "min_confidence",
            "confidence_metric",
            "position_sizing",
        ]
        values = [getattr(self, key) for key in keys]
        for combo in itertools.product(*values):
            yield dict(zip(keys, combo))

    def random_sample(self, n: int, *, seed: int = 0) -> list[dict]:
        all_rows = list(self.grid())
        if n <= 0 or n >= len(all_rows):
            return all_rows
        rng = random.Random(seed)
        idxs = sorted(rng.sample(range(len(all_rows)), int(n)))
        return [all_rows[i] for i in idxs]

    def count(self) -> int:
        total = 1
        for values in (
            self.max_positions,
            self.confidence_weight,
            self.signal_strength_weight,
            self.min_confidence,
            self.confidence_metric,
            self.position_sizing,
        ):
            total *= len(values)
        return total


def default_search_space() -> SearchSpace:
    return SearchSpace()
