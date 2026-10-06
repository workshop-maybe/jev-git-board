"""
The TypeSafe client and how its key is found.

The key comes from TYPESAFE_API_KEY, else from the first `TYPESAFE_API_KEY=` line of an
env file: --env-file, then JEV_BOARD_ENV_FILE, then ~/.config/jev-board/env. Only runs
that ask Jev need it, so ranking from cache works with no key at all.
"""
import os
import sys
from pathlib import Path

from typesafe_sdk import AsyncTypeSafeClient, Choice, Score  # noqa: F401 (re-exported)

PARALLEL = 8  # concurrent requests


def env_file_path(explicit=None):
    return (explicit or os.environ.get("JEV_BOARD_ENV_FILE")
            or str(Path.home() / ".config" / "jev-board" / "env"))


def load_key(env_file=None):
    if os.environ.get("TYPESAFE_API_KEY"):
        return
    path = env_file_path(env_file)
    try:
        with open(path) as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                if k.strip() == "TYPESAFE_API_KEY" and v.strip():
                    os.environ["TYPESAFE_API_KEY"] = v.strip().strip("'\"")
                    return
    except FileNotFoundError:
        pass
    sys.exit(f"TYPESAFE_API_KEY not found in the environment or in {path}")
