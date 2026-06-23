from __future__ import annotations

from pathlib import Path

from seektalent.config import AppSettings, classify_local_data_root, evaluate_local_data_root_policy


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_PRODUCT_DOCS = (
    "README.md",
    "docs/cli.md",
    "docs/configuration.md",
)


def _local_product_docs_text() -> str:
    return "\n".join((PROJECT_ROOT / path).read_text(encoding="utf-8") for path in LOCAL_PRODUCT_DOCS)


def test_local_product_docs_use_required_vocabulary() -> None:
    docs = _local_product_docs_text().lower()

    for phrase in (
        "local-first",
        "local recruiter workbench",
        "cli",
        "local workbench",
        "not a hosted recruiting saas",
    ):
        assert phrase in docs


def test_local_product_docs_reject_old_product_framing() -> None:
    docs = _local_product_docs_text().lower()

    for phrase in (
        "minimal local web ui is secondary",
        "throwaway debug surface",
        "hosted recruiting saas dashboard",
    ):
        assert phrase not in docs


def test_repo_root_is_risky_data_root(tmp_path: Path) -> None:
    marker = tmp_path / "pyproject.toml"
    marker.write_text("[project]\nname='seektalent'\n", encoding="utf-8")

    posture = classify_local_data_root(tmp_path)

    assert posture.status == "risky"
    assert posture.reason_code == "repo_root"


def test_child_of_repo_root_is_risky_data_root(tmp_path: Path) -> None:
    marker = tmp_path / "pyproject.toml"
    marker.write_text("[project]\nname='seektalent'\n", encoding="utf-8")
    data_root = tmp_path / ".seektalent"
    data_root.mkdir()

    posture = classify_local_data_root(data_root)

    assert posture.status == "risky"
    assert posture.reason_code == "inside_repo"


def test_home_seektalent_data_root_is_safe() -> None:
    posture = classify_local_data_root(Path.home() / ".seektalent")

    assert posture.status == "safe"
    assert posture.reason_code == "user_data_root"


def test_child_of_home_seektalent_data_root_is_safe() -> None:
    posture = classify_local_data_root(Path.home() / ".seektalent" / "artifacts")

    assert posture.status == "safe"
    assert posture.reason_code == "user_data_root"


def test_custom_dot_seektalent_root_is_unknown_not_safe(tmp_path: Path) -> None:
    data_root = tmp_path / ".seektalent"
    data_root.mkdir()

    posture = classify_local_data_root(data_root)

    assert posture.status == "unknown"
    assert posture.reason_code == "custom_path"


def test_sync_folder_data_root_is_risky(tmp_path: Path) -> None:
    data_root = tmp_path / "Dropbox" / ".seektalent"
    data_root.mkdir(parents=True)

    posture = classify_local_data_root(data_root)

    assert posture.status == "risky"
    assert posture.reason_code == "sync_folder"


def test_company_onedrive_variant_is_risky(tmp_path: Path) -> None:
    data_root = tmp_path / "OneDrive - Company" / ".seektalent"
    data_root.mkdir(parents=True)

    posture = classify_local_data_root(data_root)

    assert posture.status == "risky"
    assert posture.reason_code == "sync_folder"


def test_sync_folder_classifier_does_not_match_substrings(tmp_path: Path) -> None:
    data_root = tmp_path / "boxcar" / ".seektalent"
    data_root.mkdir(parents=True)

    posture = classify_local_data_root(data_root)

    assert posture.status == "unknown"
    assert posture.reason_code == "custom_path"


def test_repo_data_root_is_dev_warning(tmp_path: Path) -> None:
    marker = tmp_path / "pyproject.toml"
    marker.write_text("[project]\nname='seektalent'\n", encoding="utf-8")

    policy = evaluate_local_data_root_policy(tmp_path, runtime_mode="dev", packaged=False)

    assert policy.status == "warning"
    assert policy.reason_code == "repo_root"


def test_repo_data_root_is_prod_error(tmp_path: Path) -> None:
    marker = tmp_path / "pyproject.toml"
    marker.write_text("[project]\nname='seektalent'\n", encoding="utf-8")

    policy = evaluate_local_data_root_policy(tmp_path, runtime_mode="prod", packaged=False)

    assert policy.status == "error"
    assert policy.reason_code == "repo_root"


def test_repo_data_root_is_packaged_error(tmp_path: Path) -> None:
    marker = tmp_path / "pyproject.toml"
    marker.write_text("[project]\nname='seektalent'\n", encoding="utf-8")

    policy = evaluate_local_data_root_policy(tmp_path, runtime_mode="dev", packaged=True)

    assert policy.status == "error"
    assert policy.reason_code == "repo_root"


def test_home_data_root_policy_is_safe() -> None:
    policy = evaluate_local_data_root_policy(Path.home() / ".seektalent", runtime_mode="prod", packaged=True)

    assert policy.status == "safe"
    assert policy.reason_code == "user_data_root"


def test_empty_local_path_settings_use_runtime_defaults(tmp_path: Path) -> None:
    settings = AppSettings(
        _env_file=None,
        workspace_root=str(tmp_path),
        artifacts_dir="",
        runs_dir="",
        llm_cache_dir="",
    )

    assert settings.artifacts_dir == "artifacts"
    assert settings.runs_dir == "runs"
    assert settings.llm_cache_dir == ".seektalent/cache"
    assert settings.artifacts_path == tmp_path / "artifacts"
    assert settings.runs_path == tmp_path / "runs"
    assert settings.llm_cache_path == tmp_path / ".seektalent" / "cache"
