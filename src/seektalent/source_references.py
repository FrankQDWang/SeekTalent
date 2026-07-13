from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_kind: str = Field(min_length=1)
    display_label: str = Field(min_length=1)
    url: str = Field(min_length=1)
