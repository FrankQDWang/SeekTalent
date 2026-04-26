from __future__ import annotations

from dataclasses import dataclass

from seektalent.config import AppSettings
from seektalent.core.retrieval.service import RetrievalService


@dataclass
class RetrievalRuntime:
    settings: AppSettings
    retrieval_service: RetrievalService
