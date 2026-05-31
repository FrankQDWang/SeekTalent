from pathlib import Path

from seektalent.cache.exact_llm_cache import (
    clear_exact_llm_cache,
    get_cached_json,
    put_cached_json,
    stable_cache_key,
)
from tests.settings_factory import make_settings


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_non_runtime_modules_import_shared_cache_path() -> None:
    offenders = []
    for source_path in sorted((PROJECT_ROOT / "src").rglob("*.py")):
        relative_path = source_path.relative_to(PROJECT_ROOT).as_posix()
        if relative_path.startswith("src/seektalent/runtime/"):
            continue
        if "seektalent.runtime.exact_llm_cache" in source_path.read_text(encoding="utf-8"):
            offenders.append(relative_path)

    assert offenders == []


def test_stable_cache_key_hashes_sorted_json_parts() -> None:
    left = stable_cache_key({"b": 2, "a": 1})
    right = stable_cache_key({"a": 1, "b": 2})

    assert left == right
    assert len(left) == 64


def test_exact_cache_round_trips_json_payload(tmp_path) -> None:
    settings = make_settings(llm_cache_dir=str(tmp_path / "cache"))
    payload = {"foo": "bar", "count": 2}

    got = get_cached_json(settings, namespace="scoring", key="k")
    assert got is None

    put_cached_json(settings, namespace="scoring", key="k", payload=payload)

    got = get_cached_json(settings, namespace="scoring", key="k")
    assert got == payload


def test_exact_cache_keeps_namespaces_separate(tmp_path) -> None:
    settings = make_settings(llm_cache_dir=str(tmp_path / "cache"))

    put_cached_json(settings, namespace="requirements", key="shared", payload={"who": "me"})
    put_cached_json(settings, namespace="scoring", key="shared", payload={"who": "other"})

    assert get_cached_json(settings, namespace="requirements", key="shared") == {"who": "me"}
    assert get_cached_json(settings, namespace="scoring", key="shared") == {"who": "other"}


def test_clear_exact_llm_cache_removes_sqlite_file(tmp_path) -> None:
    settings = make_settings(llm_cache_dir=str(tmp_path / "cache"))

    put_cached_json(settings, namespace="scoring", key="k", payload={"value": 1})
    assert get_cached_json(settings, namespace="scoring", key="k") == {"value": 1}

    clear_exact_llm_cache(settings)

    assert get_cached_json(settings, namespace="scoring", key="k") is None


def test_exact_cache_uses_workspace_root_for_relative_cache_dir(tmp_path) -> None:
    settings = make_settings(
        workspace_root=str(tmp_path),
        llm_cache_dir=".seektalent/cache",
    )

    put_cached_json(settings, namespace="scoring", key="k", payload={"value": 1})

    assert (tmp_path / ".seektalent" / "cache" / "exact_llm_cache.sqlite3").exists()
