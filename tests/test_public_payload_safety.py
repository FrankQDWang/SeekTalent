from __future__ import annotations

from importlib import import_module

import pytest


_MISSING = object()


def _public_payload_safety_api(name: str):
    try:
        module = import_module("seektalent.public_payload_safety")
    except ModuleNotFoundError as exc:
        if exc.name == "seektalent.public_payload_safety":
            return _MISSING
        raise
    return getattr(module, name)


@pytest.mark.parametrize(
    ("value", "max_length", "expected"),
    [
        ("  AI engineer  ", 80, "AI engineer"),
        ("abcdefgh", 4, "abcd"),
        ("SHOULD_NOT_RENDER", 80, None),
        ("INTERNAL_PROVIDER_REFERENCE", 80, None),
        ("resume https://provider.example/private", 80, None),
        ("Authorization: Bearer private-token", 80, None),
        ("debug Bearer private-token", 80, None),
        ("secret=private-token", 80, None),
        ("apikey=private-token", 80, None),
        ("api_key=private-token", 80, None),
        ("api-key=private-token", 80, None),
        ("api key rotation plan", 80, "api key rotation plan"),
        ("X-API-Key: private-token", 80, None),
        ("cookie=private-token", 80, None),
        ("password=private-token", 80, None),
        ("OpenCLI browser diagnostic", 80, None),
        ("CDP target diagnostic", 80, None),
        (42, 80, None),
    ],
)
def test_public_text_applies_the_shared_public_payload_policy(
    value: object,
    max_length: int,
    expected: str | None,
) -> None:
    public_text = _public_payload_safety_api("public_text")

    assert public_text is not _MISSING
    assert public_text(value, max_length=max_length) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("cts", "cts"),
        ("internal_referrals", "internal_referrals"),
        ("INTERNAL_PROVIDER_REFERENCE", None),
        ("source/with/path", None),
        ("OpenCLI", None),
        ("Bearer_private-token", None),
    ],
)
def test_public_source_identifier_applies_text_policy_before_identifier_shape(
    value: object,
    expected: str | None,
) -> None:
    public_source_identifier = _public_payload_safety_api("public_source_identifier")

    assert public_source_identifier is not _MISSING
    assert public_source_identifier(value) == expected
