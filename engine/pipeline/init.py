"""Pipeline package bootstrap helpers.

The main public scoring functions live in engine.pipeline.scoring.
This file is intentionally small because the user-facing package initializer
will be decided when the remaining pipeline modules are added.
"""
from __future__ import annotations

from engine.pipeline.scoring import (  # noqa: F401
    is_oos_year_pass,
    score_full_training_members,
    score_stock_from_rolling,
)
