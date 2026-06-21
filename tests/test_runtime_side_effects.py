from __future__ import annotations

import pytest

from seektalent.runtime.side_effects import RuntimeSideEffectPolicy
from tests.settings_factory import make_settings


def test_prod_side_effect_policy_suppresses_debug_and_learning_writes(tmp_path) -> None:
    settings = make_settings(workspace_root=str(tmp_path), runtime_mode="prod")

    policy = RuntimeSideEffectPolicy.from_settings(settings)

    assert policy.runtime_mode == "prod"
    assert policy.artifact_output_mode == "prod"
    assert policy.write_debug_artifacts is False
    assert policy.write_flywheel_learning_state is False
    assert policy.write_corpus_product_state is True


def test_prod_side_effect_policy_rejects_dev_artifact_output_override(tmp_path) -> None:
    settings = make_settings(
        workspace_root=str(tmp_path),
        runtime_mode="prod",
        runtime_artifact_output_mode="dev",
    )

    with pytest.raises(ValueError, match="prod_runtime_requires_prod_artifact_output_mode"):
        RuntimeSideEffectPolicy.from_settings(settings)


def test_dev_side_effect_policy_allows_debug_and_configured_learning_writes(tmp_path) -> None:
    settings = make_settings(workspace_root=str(tmp_path), runtime_mode="dev", enable_flywheel=True)

    policy = RuntimeSideEffectPolicy.from_settings(settings)

    assert policy.runtime_mode == "dev"
    assert policy.artifact_output_mode == "dev"
    assert policy.write_debug_artifacts is True
    assert policy.write_flywheel_learning_state is True
    assert policy.write_corpus_product_state is True


def test_dev_side_effect_policy_accepts_explicit_full_local_debug_artifacts(tmp_path) -> None:
    settings = make_settings(
        workspace_root=str(tmp_path),
        runtime_mode="dev",
        runtime_artifact_output_mode="debug_full_local",
    )

    policy = RuntimeSideEffectPolicy.from_settings(settings)

    assert policy.runtime_mode == "dev"
    assert policy.artifact_output_mode == "debug_full_local"
    assert policy.write_debug_artifacts is True
    assert policy.write_flywheel_learning_state is True
    assert policy.write_corpus_product_state is True
