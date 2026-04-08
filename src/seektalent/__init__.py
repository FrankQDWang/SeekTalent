"""seektalent package."""

from seektalent.api import run_match, run_match_async
from seektalent.config import AppSettings
from seektalent.runtime import PHASE1_RUNTIME_GATE_MESSAGE, Phase1RuntimeGateError

__all__ = [
    "__version__",
    "AppSettings",
    "PHASE1_RUNTIME_GATE_MESSAGE",
    "Phase1RuntimeGateError",
    "run_match",
    "run_match_async",
]

__version__ = "0.3.0a1"
