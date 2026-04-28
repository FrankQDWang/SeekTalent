from __future__ import annotations

from pathlib import Path
import tomllib

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "src" / "seektalent"
PYPROJECT_TOML = REPO_ROOT / "pyproject.toml"
SIDECAR_ROOT = SOURCE_ROOT / "prf_sidecar"
SIDECAR_DOCKERFILE = REPO_ROOT / "docker" / "prf-model-sidecar" / "Dockerfile"
SIDECAR_COMPOSE = REPO_ROOT / "docker" / "prf-model-sidecar" / "compose.yml"
SIDECAR_ONLY_DEPENDENCIES = {
    "fastapi",
    "gliner2",
    "huggingface-hub",
    "sentence-transformers",
    "torch",
    "transformers",
    "uvicorn",
}
FORBIDDEN_IMPORT_SNIPPETS = (
    "import torch",
    "from torch",
    "import transformers",
    "from transformers",
    "import sentence_transformers",
    "from sentence_transformers",
    "import huggingface_hub",
    "from huggingface_hub",
)
FORBIDDEN_DOWNLOAD_SNIPPETS = (
    "snapshot_download(",
    ".from_pretrained(",
    "SentenceTransformer(",
)
FORBIDDEN_SIDECAR_INTERNAL_IMPORTS = (
    "from seektalent.prf_sidecar.loaders import",
    "import seektalent.prf_sidecar.loaders",
    "from seektalent.prf_sidecar.prefetch import",
    "import seektalent.prf_sidecar.prefetch",
)
FORBIDDEN_LOGGING_SNIPPETS = (
    "import logging",
    "from logging",
    "getLogger(",
    "logger.",
    "print(",
)


def _python_sources_outside_sidecar() -> list[Path]:
    return sorted(
        path
        for path in SOURCE_ROOT.rglob("*.py")
        if "prf_sidecar" not in path.parts and "__pycache__" not in path.parts
    )


def _sidecar_python_sources() -> list[Path]:
    return sorted(path for path in SIDECAR_ROOT.rglob("*.py") if "__pycache__" not in path.parts)


def _read_pyproject() -> dict[str, object]:
    with PYPROJECT_TOML.open("rb") as handle:
        return tomllib.load(handle)


def _dependency_names(entries: list[str]) -> set[str]:
    names: set[str] = set()
    for entry in entries:
        names.add(entry.split("[", 1)[0].split(">=", 1)[0].split("==", 1)[0].strip())
    return names


def test_non_sidecar_source_does_not_import_model_serving_libraries() -> None:
    offenders: list[str] = []
    for path in _python_sources_outside_sidecar():
        text = path.read_text(encoding="utf-8")
        if any(snippet in text for snippet in FORBIDDEN_IMPORT_SNIPPETS):
            offenders.append(str(path.relative_to(REPO_ROOT)))

    assert offenders == []


def test_non_sidecar_source_does_not_call_model_download_helpers() -> None:
    offenders: list[str] = []
    for path in _python_sources_outside_sidecar():
        text = path.read_text(encoding="utf-8")
        if any(snippet in text for snippet in FORBIDDEN_DOWNLOAD_SNIPPETS):
            offenders.append(str(path.relative_to(REPO_ROOT)))

    assert offenders == []


def test_non_sidecar_source_does_not_import_sidecar_loader_or_prefetch_modules() -> None:
    offenders: list[str] = []
    for path in _python_sources_outside_sidecar():
        repo_relative = str(path.relative_to(REPO_ROOT))
        if repo_relative == "src/seektalent/cli.py":
            continue
        text = path.read_text(encoding="utf-8")
        if any(snippet in text for snippet in FORBIDDEN_SIDECAR_INTERNAL_IMPORTS):
            offenders.append(repo_relative)

    assert offenders == []


def test_base_install_does_not_include_sidecar_only_dependencies() -> None:
    pyproject = _read_pyproject()
    project = pyproject["project"]
    dependencies = _dependency_names(project["dependencies"])

    assert dependencies.isdisjoint(SIDECAR_ONLY_DEPENDENCIES)


def test_prf_sidecar_extra_carries_model_serving_stack() -> None:
    pyproject = _read_pyproject()
    project = pyproject["project"]
    optional = project["optional-dependencies"]
    prf_sidecar = _dependency_names(optional["prf-sidecar"])

    assert SIDECAR_ONLY_DEPENDENCIES.issubset(prf_sidecar)


def test_sidecar_package_has_no_default_logging_surface() -> None:
    offenders: list[str] = []
    for path in _sidecar_python_sources():
        text = path.read_text(encoding="utf-8")
        if any(snippet in text for snippet in FORBIDDEN_LOGGING_SNIPPETS):
            offenders.append(str(path.relative_to(REPO_ROOT)))

    assert offenders == []


def test_sidecar_app_uses_bind_host_resolver_instead_of_hardcoded_loopback() -> None:
    app_source = (SIDECAR_ROOT / "app.py").read_text(encoding="utf-8")

    assert 'host="127.0.0.1"' not in app_source
    assert "host='127.0.0.1'" not in app_source
    assert "host=resolve_sidecar_bind_host(settings)" in app_source


def test_sidecar_dockerfile_installs_prf_sidecar_extra_and_uses_sidecar_entrypoint() -> None:
    dockerfile = SIDECAR_DOCKERFILE.read_text(encoding="utf-8")

    assert "--extra prf-sidecar" in dockerfile
    assert "seektalent-prf-sidecar" in dockerfile


def test_sidecar_compose_uses_internal_network_without_host_port_publication() -> None:
    compose = SIDECAR_COMPOSE.read_text(encoding="utf-8")

    assert "internal: true" in compose
    assert "SEEKTALENT_PRF_SIDECAR_PROFILE: docker-internal" in compose
    assert "HF_HUB_OFFLINE: \"1\"" in compose
    assert "ports:" not in compose


def test_prod_prefetch_uses_pinned_revisions(monkeypatch: pytest.MonkeyPatch) -> None:
    from seektalent.config import AppSettings
    from seektalent.prf_sidecar.prefetch import prefetch_sidecar_models

    calls: list[dict[str, object]] = []

    def fake_snapshot_download(**kwargs):
        calls.append(kwargs)
        return "/tmp/cache"

    monkeypatch.setattr("seektalent.prf_sidecar.prefetch.snapshot_download", fake_snapshot_download)
    settings = AppSettings(
        _env_file=None,  # ty: ignore[unknown-argument]
        prf_span_model_revision="rev-span",
        prf_span_tokenizer_revision="rev-tokenizer",
        prf_embedding_model_revision="rev-embed",
    )

    prefetch_sidecar_models(settings)

    assert [call["revision"] for call in calls] == ["rev-span", "rev-tokenizer", "rev-embed"]
