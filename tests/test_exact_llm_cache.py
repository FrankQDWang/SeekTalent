from seektalent.runtime.exact_llm_cache import (
    get_cached_json,
    put_cached_json,
    stable_cache_key,
)
from tests.settings_factory import make_settings


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
