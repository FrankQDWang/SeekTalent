from seektalent.runtime.public_notes import runtime_note_facts_from_events


def test_runtime_note_facts_extract_safe_public_runtime_counts() -> None:
    facts, numbers = runtime_note_facts_from_events(
        [
            {
                "eventName": "runtime_round_source_result",
                "payload": {
                    "schemaVersion": "runtime_public_event_v1",
                    "stage": "source_result",
                    "roundNo": 2,
                    "sourceKind": "liepin",
                    "status": "blocked",
                    "safeReasonCode": "source_risk_or_verification_required",
                    "counts": {"roundReturned": 7, "roundIdentities": 6},
                },
            },
            {
                "eventName": "runtime_finalization_completed",
                "payload": {
                    "schemaVersion": "runtime_public_event_v1",
                    "stage": "finalization",
                    "roundNo": None,
                    "sourceKind": None,
                    "status": "completed",
                    "counts": {"selectedIdentityCount": 10},
                },
            },
        ]
    )

    assert "runtime_source_result_round_2=seen" in facts
    assert "runtime_source_result_round_2_source=liepin" in facts
    assert "runtime_source_result_round_2_status=blocked" in facts
    assert "runtime_source_result_round_2_reason=source_risk_or_verification_required" in facts
    assert "runtime_source_result_round_2_roundReturned=7" in facts
    assert "runtime_source_result_round_2_roundIdentities=6" in facts
    assert "runtime_finalization=seen" in facts
    assert "runtime_finalization_selectedIdentityCount=10" in facts
    assert set(numbers) >= {2, 7, 6, 10}


def test_runtime_note_facts_ignore_technical_or_unknown_payload_keys() -> None:
    facts, numbers = runtime_note_facts_from_events(
        [
            {
                "eventName": "runtime_round_source_result",
                "payload": {
                    "schemaVersion": "runtime_public_event_v1",
                    "stage": "source_result",
                    "roundNo": 1,
                    "sourceKind": "cts",
                    "status": "completed",
                    "runtimeRunId": "secret-run-id",
                    "artifactPath": "/tmp/private.json",
                    "counts": {"roundReturned": 3, "secretCount": 99},
                },
            }
        ]
    )

    serialized = " ".join(facts)
    assert "secret-run-id" not in serialized
    assert "/tmp/private" not in serialized
    assert "secretCount" not in serialized
    assert numbers == [1, 3]
