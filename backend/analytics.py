"""PostHog client lifecycle helpers for the FastAPI application."""

import atexit
from typing import Optional

from posthog import Posthog

from config import get_settings

posthog_client: Optional[Posthog] = None


def initialize_posthog() -> Optional[Posthog]:
    """Create the process-wide PostHog client when analytics is configured."""
    global posthog_client

    settings = get_settings()
    missing_variable = next(
        (
            variable
            for variable, value in (
                ("POSTHOG_PROJECT_TOKEN", settings.posthog_project_token),
                ("POSTHOG_HOST", settings.posthog_host),
            )
            if not value
        ),
        None,
    )
    if missing_variable:
        if settings.debug:
            raise RuntimeError(
                f"{missing_variable} variable required by PostHog is missing or "
                f"un-configured, this causes events to be silently missed. This error "
                f"stops appearing once {missing_variable} is configured"
            )
        return None

    posthog_client = Posthog(
        project_api_key=settings.posthog_project_token,
        host=settings.posthog_host,
        enable_exception_autocapture=True,
    )
    atexit.register(posthog_client.shutdown)
    return posthog_client


def get_posthog_client() -> Optional[Posthog]:
    """Return the process-wide PostHog client for route instrumentation."""
    return posthog_client


def flush_posthog() -> None:
    """Flush buffered events during FastAPI shutdown."""
    if posthog_client is not None:
        posthog_client.flush()
