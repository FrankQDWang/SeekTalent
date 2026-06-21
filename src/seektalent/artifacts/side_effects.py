from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from seektalent.config import AppSettings
from seektalent_runtime_control.artifact_policy import (
    RuntimeArtifactOutputMode,
    RuntimeArtifactPolicy,
    normalize_artifact_output_mode,
)


@dataclass(frozen=True)
class RuntimeSideEffectPolicy:
    runtime_mode: Literal["dev", "prod"]
    artifact_output_mode: RuntimeArtifactOutputMode
    write_debug_artifacts: bool
    write_flywheel_learning_state: bool
    write_corpus_product_state: bool

    @classmethod
    def from_settings(cls, settings: AppSettings) -> "RuntimeSideEffectPolicy":
        runtime_mode = settings.runtime_mode
        artifact_output_mode = normalize_artifact_output_mode(settings.runtime_artifact_output_mode)
        if runtime_mode == "prod" and artifact_output_mode != "prod":
            raise ValueError("prod_runtime_requires_prod_artifact_output_mode")
        artifact_policy = RuntimeArtifactPolicy(artifact_output_mode)
        return cls(
            runtime_mode=runtime_mode,
            artifact_output_mode=artifact_output_mode,
            write_debug_artifacts=artifact_policy.writes_local_debug_artifacts,
            write_flywheel_learning_state=runtime_mode == "dev" and bool(settings.enable_flywheel),
            write_corpus_product_state=True,
        )
