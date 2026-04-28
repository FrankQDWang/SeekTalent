from __future__ import annotations

from pathlib import Path

from huggingface_hub import snapshot_download

from seektalent.config import AppSettings


def build_prefetch_plan(settings: AppSettings) -> list[dict[str, str]]:
    return [
        {
            "repo_id": settings.prf_span_model_name,
            "revision": settings.prf_span_model_revision,
            "kind": "span-model",
        },
        {
            "repo_id": settings.prf_span_model_name,
            "revision": settings.prf_span_tokenizer_revision,
            "kind": "span-tokenizer",
        },
        {
            "repo_id": settings.prf_embedding_model_name,
            "revision": settings.prf_embedding_model_revision,
            "kind": "embedding-model",
        },
    ]


def prefetch_sidecar_models(settings: AppSettings, cache_dir: str | Path | None = None) -> list[str]:
    cache_path = str(cache_dir) if cache_dir is not None else None
    downloads: list[str] = []
    for item in build_prefetch_plan(settings):
        downloads.append(
            snapshot_download(
                repo_id=item["repo_id"],
                revision=item["revision"],
                cache_dir=cache_path,
                local_files_only=False,
            )
        )
    return downloads
