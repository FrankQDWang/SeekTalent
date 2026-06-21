from __future__ import annotations

import argparse

from seektalent.resources import read_env_example_template, resolve_user_path
from seektalent.version import __version__


def init_command(args: argparse.Namespace) -> int:
    env_path = resolve_user_path(args.env_file)
    if env_path.exists() and not args.force:
        raise ValueError(f"{env_path} already exists. Use --force to overwrite it.")
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(read_env_example_template(), encoding="utf-8")
    print(f"Wrote env template to {env_path}")
    return 0


def version_command(args: argparse.Namespace) -> int:
    del args
    print(__version__)
    return 0


def update_command(args: argparse.Namespace) -> int:
    del args
    print(f"Current version: {__version__}")
    print("Upgrade with pip: pip install -U seektalent")
    print(f"Install this exact version: pip install -U seektalent=={__version__}")
    print("Upgrade with pipx: pipx upgrade seektalent")
    print("This command prints upgrade instructions only. It does not modify your environment.")
    return 0
