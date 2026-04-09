from __future__ import annotations

from typing import Never

from seektalent.config import AppSettings

RUNTIME_PHASE_GATE_MESSAGE = (
    "SeekTalent v0.3 ships the phase 3 bootstrap, search execution, and ranking core, "
    "but the frontier loop and finalize stages are not available yet. run remains gated."
)


class RuntimePhaseGateError(RuntimeError):
    pass


class WorkflowRuntime:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def run(self, *, job_description: str, hiring_notes: str = "") -> Never:
        del job_description, hiring_notes
        raise RuntimePhaseGateError(RUNTIME_PHASE_GATE_MESSAGE)

    async def run_async(self, *, job_description: str, hiring_notes: str = "") -> Never:
        del job_description, hiring_notes
        raise RuntimePhaseGateError(RUNTIME_PHASE_GATE_MESSAGE)
