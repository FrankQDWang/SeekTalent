from __future__ import annotations

from seektalent.config import AppSettings
from seektalent.models import FinalResult, FinalizeContext, ScoredCandidate
from seektalent.prompting import LoadedPrompt
from seektalent.finalize.deterministic import build_deterministic_final_result
from seektalent.tracing import ProviderUsageSnapshot


class Finalizer:
    """Compatibility adapter for the retired LLM finalizer.

    Runtime finalization is deterministic. This class remains only so older
    imports do not silently reintroduce an LLM-backed finalization path.
    """

    def __init__(self, settings: AppSettings, prompt: LoadedPrompt) -> None:
        del settings, prompt
        self.last_validator_retry_count = 0
        self.last_validator_retry_reasons: list[str] = []
        self.last_provider_usage: ProviderUsageSnapshot | None = None
        self.last_draft_output = None

    async def finalize(
        self,
        *,
        run_id: str,
        run_dir: str,
        rounds_executed: int,
        stop_reason: str,
        ranked_candidates: list[ScoredCandidate],
    ) -> FinalResult:
        self.last_validator_retry_count = 0
        self.last_validator_retry_reasons = []
        self.last_provider_usage = None
        self.last_draft_output = None
        return build_deterministic_final_result(
            FinalizeContext(
                run_id=run_id,
                run_dir=run_dir,
                rounds_executed=rounds_executed,
                stop_reason=stop_reason,
                top_candidates=ranked_candidates,
            )
        )
