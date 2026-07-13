from __future__ import annotations

import pytest
from pydantic import ValidationError

from seektalent.source_references import SourceReference


@pytest.mark.parametrize(
    ("raw_url", "canonical_url"),
    [
        ("https://EXAMPLE.test:443/x", "https://example.test/x"),
        ("HTTP://Example.TEST:80/x?A=1", "http://example.test/x?A=1"),
        ("https://EXAMPLE.test:8443/x", "https://example.test:8443/x"),
    ],
)
def test_source_reference_canonicalizes_provider_neutral_url_parts(
    raw_url: str,
    canonical_url: str,
) -> None:
    reference = SourceReference(
        source_kind="future_source",
        display_label="Future Source",
        url=raw_url,
    )

    assert reference.url == canonical_url


def test_source_reference_keeps_distinct_path_shapes_distinct() -> None:
    without_slash = SourceReference(
        source_kind="future_source",
        display_label="Future Source",
        url="https://example.test/x",
    )
    with_slash = SourceReference(
        source_kind="future_source",
        display_label="Future Source",
        url="https://example.test/x/",
    )

    assert without_slash.url != with_slash.url


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "/candidate/1",
        "https:///candidate/1",
        "https://user@example.test/candidate/1",
        "https://user:secret@example.test/candidate/1",
    ],
)
def test_source_reference_rejects_unsafe_or_non_absolute_urls(url: str) -> None:
    with pytest.raises(ValidationError):
        SourceReference(
            source_kind="future_source",
            display_label="Future Source",
            url=url,
        )
