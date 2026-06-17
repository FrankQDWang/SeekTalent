from __future__ import annotations


def main() -> None:
    from seektalent_ui.server import main as server_main

    server_main()

__all__ = ["main"]
