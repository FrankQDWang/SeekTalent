from __future__ import annotations

import asyncio

import pytest

from seektalent import (
    PHASE1_RUNTIME_GATE_MESSAGE,
    AppSettings,
    Phase1RuntimeGateError,
    __version__,
    run_match,
    run_match_async,
)


def test_run_match_is_phase_gated() -> None:
    with pytest.raises(Phase1RuntimeGateError, match="Phase 1 only ships contracts"):
        run_match(
            job_description="Python agent engineer",
            hiring_notes="Shanghai preferred",
            settings=AppSettings(_env_file=None, mock_cts=True),
            env_file=None,
        )


def test_run_match_async_is_phase_gated() -> None:
    with pytest.raises(Phase1RuntimeGateError, match="Phase 1 only ships contracts"):
        asyncio.run(
            run_match_async(
                job_description="Python agent engineer",
                hiring_notes="Shanghai preferred",
                settings=AppSettings(_env_file=None, mock_cts=True),
                env_file=None,
            )
        )


def test_top_level_exports_remain_available() -> None:
    settings = AppSettings(_env_file=None, mock_cts=True)
    assert settings.mock_cts is True
    assert __version__ == "0.3.0a1"
    assert "CTS bridge" in PHASE1_RUNTIME_GATE_MESSAGE
