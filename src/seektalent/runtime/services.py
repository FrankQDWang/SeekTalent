from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from seektalent.config import AppSettings
from seektalent.candidate_feedback.llm_prf import LLMPRFExtractor
from seektalent.controller import ReActController
from seektalent.corpus.store import CorpusStore
from seektalent.core.retrieval.service import RetrievalService
from seektalent.finalize.finalizer import Finalizer
from seektalent.prompting import LoadedPrompt
from seektalent.reflection.critic import ReflectionCritic
from seektalent.requirements import RequirementExtractor
from seektalent.resume_quality import ResumeQualityCommenter
from seektalent.runtime.retrieval_runtime import RetrievalRuntime
from seektalent.scoring.scorer import ResumeScorer


@dataclass
class RuntimeServices:
    requirement_extractor: RequirementExtractor
    controller: ReActController
    resume_scorer: ResumeScorer
    resume_quality_commenter: ResumeQualityCommenter
    reflection_critic: ReflectionCritic
    finalizer: Finalizer
    llm_prf_extractor: LLMPRFExtractor
    retrieval_runtime: RetrievalRuntime
    retrieval_service: RetrievalService
    corpus_store: CorpusStore


def build_runtime_services(
    *,
    settings: AppSettings,
    prompt_map: Mapping[str, LoadedPrompt],
    retrieval_service: RetrievalService,
) -> RuntimeServices:
    return RuntimeServices(
        requirement_extractor=RequirementExtractor(
            settings,
            prompt_map["requirements"],
            repair_prompt=prompt_map["repair_requirements"],
        ),
        controller=ReActController(
            settings,
            prompt_map["controller"],
            repair_prompt=prompt_map["repair_controller"],
        ),
        resume_scorer=ResumeScorer(settings, prompt_map["scoring"]),
        resume_quality_commenter=ResumeQualityCommenter(settings, prompt_map["tui_summary"]),
        reflection_critic=ReflectionCritic(
            settings,
            prompt_map["reflection"],
            repair_prompt=prompt_map["repair_reflection"],
        ),
        finalizer=Finalizer(settings, prompt_map["finalize"]),
        llm_prf_extractor=LLMPRFExtractor(settings, prompt_map["prf_probe_phrase_proposal"]),
        retrieval_runtime=RetrievalRuntime(
            settings=settings,
            retrieval_service=retrieval_service,
        ),
        retrieval_service=retrieval_service,
        corpus_store=CorpusStore(settings.corpus_path),
    )
