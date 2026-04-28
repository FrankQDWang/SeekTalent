from __future__ import annotations

import json
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SidecarDependencyManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sidecar_image_digest: str
    python_lockfile_hash: str
    torch_version: str
    transformers_version: str
    sentence_transformers_version: str | None = None
    gliner_runtime_version: str
    span_model_name: str
    span_model_commit: str
    span_tokenizer_commit: str
    embedding_model_name: str
    embedding_model_commit: str
    remote_code_policy: Literal["disabled", "approved_baked_code"]
    remote_code_commit: str | None = None
    license_status: Literal["approved", "blocked"]
    embedding_normalization: bool
    embedding_dimension: int = Field(gt=0)
    dtype: Literal["float32", "float16", "bfloat16"]
    max_input_tokens: int = Field(gt=0)

    def compute_hash(self) -> str:
        payload = self.model_dump(mode="json", exclude_none=False)
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return sha256(blob.encode("utf-8")).hexdigest()


class SpanExtractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    texts: list[str] = Field(min_length=1)
    labels: list[str] = Field(min_length=1)
    schema_version: str
    model_name: str
    model_revision: str


class SpanExtractRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_text_index: int = Field(ge=0)
    surface: str = Field(min_length=1)
    label: str
    score: float = Field(ge=0.0, le=1.0)
    model_start_char: int | None = Field(default=None, ge=0)
    model_end_char: int | None = Field(default=None, ge=0)
    alignment_hint_only: bool = True


class SpanExtractResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["prf-sidecar-span-v1"]
    model_name: str
    model_revision: str
    rows: list[SpanExtractRow]


class EmbedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    phrases: list[str] = Field(min_length=1)
    model_name: str
    model_revision: str


class EmbedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["prf-sidecar-embed-v1"]
    model_name: str
    model_revision: str
    embedding_dimension: int = Field(gt=0)
    normalized: bool
    pooling: str
    dtype: Literal["float32", "float16", "bfloat16"]
    max_input_tokens: int = Field(gt=0)
    truncation: bool
    vectors: list[list[float]]
