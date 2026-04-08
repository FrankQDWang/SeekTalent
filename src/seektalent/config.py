from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from seektalent.resources import DEFAULT_CTS_SPEC_NAME, package_spec_file, resolve_user_path


def load_process_env(env_file: str | Path = ".env") -> None:
    path = Path(env_file)
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SEEKTALENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    cts_base_url: str = "https://link.hewa.cn"
    cts_tenant_key: str | None = None
    cts_tenant_secret: str | None = None
    cts_timeout_seconds: float = Field(default=20.0, gt=0)
    cts_spec_path: str = DEFAULT_CTS_SPEC_NAME
    mock_cts: bool = False
    runs_dir: str = "runs"

    @property
    def project_root(self) -> Path:
        return Path.cwd()

    @property
    def spec_file(self) -> Path:
        if self.cts_spec_path == DEFAULT_CTS_SPEC_NAME:
            return package_spec_file()
        return resolve_user_path(self.cts_spec_path)

    @property
    def runs_path(self) -> Path:
        return resolve_user_path(self.runs_dir)

    def require_cts_credentials(self) -> None:
        if self.mock_cts:
            return
        if self.cts_tenant_key and self.cts_tenant_secret:
            return
        raise ValueError(
            "Real CTS mode requires SEEKTALENT_CTS_TENANT_KEY and SEEKTALENT_CTS_TENANT_SECRET."
        )

    def with_overrides(self, **overrides: object) -> "AppSettings":
        filtered = {key: value for key, value in overrides.items() if value is not None}
        return type(self).model_validate({**self.model_dump(), **filtered})
