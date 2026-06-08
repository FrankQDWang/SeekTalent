from __future__ import annotations

import random
import time

from seektalent.providers.liepin.opencli_browser_automation import OpenCliBrowserAutomation
from seektalent.providers.liepin.opencli_browser_contracts import (
    LIEPIN_RECRUITER_SEARCH_URL,
    OpenCliBrowserConfig,
    OpenCliBrowserError,
    OpenCliBrowserPolicy,
    OpenCliBrowserResult,
    default_liepin_opencli_policy,
)
from seektalent.providers.liepin.opencli_runtime import (
    BlankChromeWindowCloser,
    ChromeWindowCounter,
    CurrentChromeTabOpener,
    OpenCliCommandRunner,
)
from seektalent.providers.liepin.liepin_site_adapter import (
    LiepinSiteAdapter,
    build_observation,
    bucket_text,
    classify_liepin_state,
    extract_allowed_click_refs,
    extract_known_modal_close_ref,
    extract_liepin_card_summaries,
    extract_liepin_search_input_ref,
)


class OpenCliBrowserRunner(LiepinSiteAdapter):
    def __init__(
        self,
        *,
        config: OpenCliBrowserConfig,
        commands: OpenCliCommandRunner | None = None,
        window_counter: ChromeWindowCounter | None = None,
        blank_window_closer: BlankChromeWindowCloser | None = None,
        current_tab_opener: CurrentChromeTabOpener | None = None,
    ) -> None:
        super().__init__(
            config=config,
            automation=OpenCliBrowserAutomation(
                config=config,
                commands=commands,
                window_counter=window_counter,
                blank_window_closer=blank_window_closer,
                current_tab_opener=current_tab_opener,
            ),
        )


__all__ = [
    "LIEPIN_RECRUITER_SEARCH_URL",
    "OpenCliBrowserConfig",
    "OpenCliBrowserError",
    "OpenCliBrowserPolicy",
    "OpenCliBrowserResult",
    "OpenCliBrowserRunner",
    "LiepinSiteAdapter",
    "build_observation",
    "bucket_text",
    "classify_liepin_state",
    "default_liepin_opencli_policy",
    "extract_allowed_click_refs",
    "extract_known_modal_close_ref",
    "extract_liepin_card_summaries",
    "extract_liepin_search_input_ref",
    "random",
    "time",
]
