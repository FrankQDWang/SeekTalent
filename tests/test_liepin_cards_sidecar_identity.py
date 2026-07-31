from __future__ import annotations

from dataclasses import dataclass

import seektalent.source_port.liepin_cards_sidecar_identity as identity_module


@dataclass(frozen=True)
class _Distribution:
    version: str
    record: str

    def read_text(self, filename: str) -> str | None:
        assert filename == "RECORD"
        return self.record


def test_installed_sidecar_identity_ignores_prefix_specific_pip_metadata(
    monkeypatch,
) -> None:
    stable_rows = (
        "seektalent/__init__.py,sha256=stable-package-hash,12\n"
        "seektalent/source_port/liepin_cards_sidecar_identity.py,"
        "sha256=stable-sidecar-hash,34\n"
    )
    first = _Distribution(
        version="0.8.0rc1",
        record=(
            stable_rows
            + "seektalent-0.8.0rc1.dist-info/direct_url.json,"
            "sha256=first-prefix,100\n"
        ),
    )
    second = _Distribution(
        version="0.8.0rc1",
        record=(
            stable_rows
            + "seektalent-0.8.0rc1.dist-info/direct_url.json,"
            "sha256=second-prefix,200\n"
        ),
    )

    monkeypatch.setattr(identity_module, "distribution", lambda _: first)
    first_identity = identity_module.liepin_cards_sidecar_identity()
    monkeypatch.setattr(identity_module, "distribution", lambda _: second)
    second_identity = identity_module.liepin_cards_sidecar_identity()

    assert first_identity == second_identity


def test_installed_sidecar_identity_changes_with_product_source_record(
    monkeypatch,
) -> None:
    first = _Distribution(
        version="0.8.0rc1",
        record="seektalent/__init__.py,sha256=first-source,12\n",
    )
    second = _Distribution(
        version="0.8.0rc1",
        record="seektalent/__init__.py,sha256=second-source,12\n",
    )

    monkeypatch.setattr(identity_module, "distribution", lambda _: first)
    first_identity = identity_module.liepin_cards_sidecar_identity()
    monkeypatch.setattr(identity_module, "distribution", lambda _: second)
    second_identity = identity_module.liepin_cards_sidecar_identity()

    assert first_identity != second_identity
