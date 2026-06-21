from __future__ import annotations

import importlib

from seektalent.runtime import controller_runtime, finalize_runtime


def test_controller_stage_state_is_shared_contract() -> None:
    contracts = importlib.import_module("seektalent.runtime.stage_contracts")

    assert controller_runtime.ControllerStageState is contracts.ControllerStageState
    assert set(contracts.ControllerStageState.__annotations__) == {
        "call_id",
        "call_payload",
        "prompt",
        "prompt_cache_key",
        "prompt_cache_retention",
        "artifacts",
        "started_at",
        "controller_latency_ms",
    }


def test_finalizer_stage_state_is_shared_contract() -> None:
    contracts = importlib.import_module("seektalent.runtime.stage_contracts")

    assert finalize_runtime.FinalizerStageState is contracts.FinalizerStageState
    assert set(contracts.FinalizerStageState.__annotations__) == {
        "call_id",
        "artifacts",
        "latency_ms",
    }
