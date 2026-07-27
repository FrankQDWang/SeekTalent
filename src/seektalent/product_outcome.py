"""Canonical main-owned product outcome vocabulary."""

from __future__ import annotations

from typing import Literal


ProductOutcome = Literal[
    "succeeded_with_results",
    "succeeded_empty",
    "degraded_with_results",
    "needs_attention",
    "failed",
    "cancelled",
]

PRODUCT_OUTCOMES: tuple[ProductOutcome, ...] = (
    "succeeded_with_results",
    "succeeded_empty",
    "degraded_with_results",
    "needs_attention",
    "failed",
    "cancelled",
)
