from __future__ import annotations


def range_overlap(
    lower: int | None,
    upper: int | None,
    bucket_min: int,
    bucket_max: int | None,
) -> float:
    """Return overlap width between a requested range and a provider bucket."""
    start = max(0 if lower is None else lower, bucket_min)
    end = min(float("inf") if upper is None else upper, float("inf") if bucket_max is None else bucket_max)
    if end <= start:
        return 0.0
    return end - start
