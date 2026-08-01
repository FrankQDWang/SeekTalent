from __future__ import annotations


class BrowserLaneBusyError(RuntimeError):
    """A browser lane is active and cannot accept another effect."""
