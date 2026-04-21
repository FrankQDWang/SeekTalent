"""seektalent package."""

from seektalent.api import MatchRunResult, run_match, run_match_async
from seektalent.config import AppSettings

__all__ = [
    "__version__",
    "AppSettings",
    "MatchRunResult",
    "run_match",
    "run_match_async",
]

__version__ = "0.4.8"
