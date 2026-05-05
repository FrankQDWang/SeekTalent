from __future__ import annotations

from pathlib import Path


FORBIDDEN_TERMS = (
    "legacy_regex",
    "sidecar_span",
    "prf_v1_5_mode",
    "prf_model_backend",
    "prf_sidecar",
    "span_extractors",
    "span_models",
    "proposal_runtime",
)

ALLOWED_PATH_PARTS = {
    "src/seektalent/legacy_artifacts.py",
    "src/seektalent/config.py",
    "tests/test_evaluation.py",
    "tests/test_llm_provider_config.py",
    "tests/test_prf_cleanup_import_graph.py",
}

FORBIDDEN_CONFIG_RUNTIME_PATTERNS = (
    "prf_probe_proposal_backend:",
    "self.prf_probe_proposal_backend",
    "settings.prf_probe_proposal_backend",
    "prf_v1_5_mode:",
    "self.prf_v1_5_mode",
    "settings.prf_v1_5_mode",
)


def _is_allowed(path: Path) -> bool:
    normalized = path.as_posix()
    return any(part in normalized for part in ALLOWED_PATH_PARTS)


def test_removed_prf_backends_are_not_imported_by_active_code() -> None:
    roots = [Path("src/seektalent"), Path("tests")]
    offenders: list[str] = []
    for root in roots:
        for path in root.rglob("*.py"):
            if path.is_dir() or _is_allowed(path):
                continue
            text = path.read_text(encoding="utf-8")
            for term in FORBIDDEN_TERMS:
                if term in text:
                    offenders.append(f"{path}:{term}")

    assert offenders == []


def test_removed_prf_terms_in_config_are_limited_to_migration_scanner() -> None:
    text = Path("src/seektalent/config.py").read_text(encoding="utf-8")

    assert "REMOVED_PRF_ENV_KEYS" in text
    for pattern in FORBIDDEN_CONFIG_RUNTIME_PATTERNS:
        assert pattern not in text
