from __future__ import annotations

import os

import pytest


# Test bodies may opt in explicitly; only credentials inherited from the host process are removed.
for credential_name in ("SEEKTALENT_TEXT_LLM_API_KEY", "SEEKTALENT_DOMI_JWT"):
    os.environ.pop(credential_name, None)


@pytest.fixture(autouse=True)
def route_liepin_browser_primitive_tests(request, monkeypatch):
    if request.module.__name__ not in {
        "tests.test_liepin_opencli_browser",
        "tests.test_liepin_opencli_city_filter",
    }:
        return
    from seektalent.providers.liepin.liepin_site_adapter import (
        LiepinSiteAdapter,
    )

    def execute_browser_primitive(site, **kwargs):
        envelope, structured = (
            site._execute_liepin_cards_sidecar_effect(**kwargs)
        )
        if structured is not None:
            site._remote_structured_cards[kwargs["source_run_id"]] = (
                structured
            )
        return envelope

    monkeypatch.setattr(
        LiepinSiteAdapter,
        "search_liepin_cards",
        execute_browser_primitive,
    )
