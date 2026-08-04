from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from seektalent.source_port.liepin_details_request_artifacts import (
    read_liepin_details_request_artifact,
    write_liepin_details_request_artifact,
)
from seektalent.source_port.wire_primitives import canonical_json_bytes
from tests.test_liepin_cards_source_operation import _details_request


class SimulatedProcessDeath(BaseException):
    pass


@pytest.mark.parametrize(
    ("fault_point", "final_exists"),
    (
        ("after_temporary_created", False),
        ("after_temporary_written", False),
        ("after_temporary_fsynced", False),
        ("after_final_replaced", True),
        ("after_directory_fsynced", True),
    ),
)
def test_request_artifact_process_death_leaves_absent_or_canonical_final(
    tmp_path: Path,
    fault_point: str,
    final_exists: bool,
) -> None:
    request = _details_request()
    payload = canonical_json_bytes(request.model_dump(mode="json"))
    digest = sha256(payload).hexdigest()

    def fail(point: str) -> None:
        if point == fault_point:
            raise SimulatedProcessDeath(point)

    with pytest.raises(SimulatedProcessDeath, match=fault_point):
        write_liepin_details_request_artifact(
            tmp_path,
            request,
            fault_injector=fail,
        )

    final_path = tmp_path / f"{digest}.json"
    assert final_path.exists() is final_exists
    if final_exists:
        assert final_path.read_bytes() == payload
    assert list(tmp_path.glob("*.tmp")) == []


def test_request_artifact_repairs_truncated_digest_without_touching_other_digest(
    tmp_path: Path,
) -> None:
    first = _details_request(card_ref="70", rank=1)
    second = _details_request(
        card_ref="71",
        rank=2,
        provider_candidate_key_hash="b" * 64,
        expected_provider_candidate_key_hash="b" * 64,
    )
    first_payload = canonical_json_bytes(first.model_dump(mode="json"))
    first_digest = sha256(first_payload).hexdigest()
    first_path = tmp_path / f"{first_digest}.json"
    first_path.write_bytes(b"truncated-final")
    second_write = write_liepin_details_request_artifact(tmp_path, second)
    second_path = tmp_path / f"{second_write.artifact_hash}.json"
    second_before = second_path.read_bytes()

    repaired = write_liepin_details_request_artifact(tmp_path, first)

    assert repaired.published is True
    assert repaired.payload_size_bytes == len(first_payload)
    assert first_path.read_bytes() == first_payload
    assert second_path.read_bytes() == second_before
    assert read_liepin_details_request_artifact(
        tmp_path,
        repaired.artifact_ref,
        expected_hash=repaired.artifact_hash,
    ) == first
    assert list(tmp_path.glob("*.tmp")) == []
