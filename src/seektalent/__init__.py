"""seektalent package."""

from seektalent.api import MatchRunResult, run_match, run_match_async, run_match_debug, run_match_debug_async
from seektalent.config import AppSettings
from seektalent.runtime.production_contract import ProductionMatchResultV1

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

__version__ = "0.6.19"
