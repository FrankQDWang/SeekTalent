from __future__ import annotations

from seektalent.config import AppSettings


LOCAL_DEVELOPMENT_LIEPIN_API_TOKEN = "local-development-liepin-api-token"
LOCAL_DEVELOPMENT_LIEPIN_SECRET = "local-development"


def reject_unsafe_liepin_control_plane(settings: AppSettings) -> None:
    if settings.runtime_mode != "prod" and not settings.liepin_live_enabled:
        return
    unsafe_secrets = {
        settings.liepin_api_token,
        settings.liepin_account_binding_secret,
        settings.liepin_stream_token_secret,
    } & {LOCAL_DEVELOPMENT_LIEPIN_API_TOKEN, LOCAL_DEVELOPMENT_LIEPIN_SECRET}
    if unsafe_secrets:
        raise ValueError("Liepin control-plane secrets must be explicitly configured outside local development.")
