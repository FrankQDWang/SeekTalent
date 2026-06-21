"""seektalent package."""

from importlib import import_module

from seektalent.version import __version__

__all__ = [
    "__version__",
    "AppSettings",
    "MatchRunResult",
    "ProductionMatchResultV1",
    "run_match",
    "run_match_async",
    "run_match_debug",
    "run_match_debug_async",
]

_LAZY_EXPORTS = {
    "AppSettings": ("seektalent.config", "AppSettings"),
    "MatchRunResult": ("seektalent.api", "MatchRunResult"),
    "ProductionMatchResultV1": ("seektalent.runtime.production_contract", "ProductionMatchResultV1"),
    "run_match": ("seektalent.api", "run_match"),
    "run_match_async": ("seektalent.api", "run_match_async"),
    "run_match_debug": ("seektalent.api", "run_match_debug"),
    "run_match_debug_async": ("seektalent.api", "run_match_debug_async"),
}


def __getattr__(name: str) -> object:
    try:
        module_name, export_name = _LAZY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module 'seektalent' has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), export_name)
    globals()[name] = value
    return value
