from __future__ import annotations

from typing import Never

from seektalent.config import AppSettings

PHASE1_RUNTIME_GATE_MESSAGE = (
    "SeekTalent v0.3 Phase 1 only ships contracts and the CTS bridge. "
    "run is gated until Phase 2+ runtime implementation lands."
)


class Phase1RuntimeGateError(RuntimeError):
    pass


class WorkflowRuntime:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def run(self, *, job_description: str, hiring_notes: str = "") -> Never:
        del job_description, hiring_notes
        raise Phase1RuntimeGateError(PHASE1_RUNTIME_GATE_MESSAGE)

    async def run_async(self, *, job_description: str, hiring_notes: str = "") -> Never:
        del job_description, hiring_notes
        raise Phase1RuntimeGateError(PHASE1_RUNTIME_GATE_MESSAGE)
