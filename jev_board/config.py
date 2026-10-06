"""
Settings for one run: flags win over an optional TOML file, which wins over defaults.

The TOML file takes the same keys as the flags (owner, repos, goals, org, audience,
framework, env_file, status, inbox, reason, unread), so a board's setup can live in one file:

    owner = "my-org"
    repos = ["~/code/api", "~/code/web"]
    goals = "~/notes/goals.md"
"""
import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ORG = "a software team"
DEFAULT_AUDIENCE = "people who use or build the product"
DEFAULT_FRAMEWORK = "wsjf"


@dataclass
class Settings:
    board: int | None
    owner: str | None
    repos: list[str] = field(default_factory=list)
    goals: str | None = None
    org: str = DEFAULT_ORG
    audience: str = DEFAULT_AUDIENCE
    framework: str = DEFAULT_FRAMEWORK
    env_file: str | None = None
    status: list[str] | None = None
    refresh: bool = False
    inbox: bool = False
    reason: list[str] | None = None
    unread: bool = False


def cache_root():
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "jev-board"


def _load_toml(path):
    try:
        with open(os.path.expanduser(path), "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        sys.exit(f"config file not found: {path}")


def _split(v):
    if isinstance(v, str):
        return [p.strip() for p in v.split(",") if p.strip()]
    return list(v or [])


def _expand(p):
    return os.path.expanduser(p) if p else p


def resolve(flags):
    """Merge parsed flags (a dict; None means not given) over the TOML file named by
    `flags["config"]`, over defaults."""
    file = _load_toml(flags["config"]) if flags.get("config") else {}
    merged = {**file, **{k: v for k, v in flags.items() if v is not None and k != "config"}}
    inbox = bool(merged.get("inbox"))
    if inbox and merged.get("board") is not None:
        sys.exit("give a board number or --inbox, not both")
    if inbox and merged.get("status"):
        sys.exit("--status picks board columns; with --inbox use --reason")
    if not inbox:
        if merged.get("board") is None:
            sys.exit("give a board number, or --inbox for your GitHub notifications")
        if merged.get("reason") or merged.get("unread"):
            sys.exit("--reason and --unread only apply with --inbox")
        if not merged.get("owner"):
            sys.exit("--owner is required (or set owner in the --config file)")
    return Settings(
        board=None if inbox else int(merged["board"]),
        owner=merged.get("owner"),
        repos=[_expand(r) for r in _split(merged.get("repos"))],
        goals=_expand(merged.get("goals")),
        org=merged.get("org") or DEFAULT_ORG,
        audience=merged.get("audience") or DEFAULT_AUDIENCE,
        framework=merged.get("framework") or DEFAULT_FRAMEWORK,
        env_file=_expand(merged.get("env_file")),
        status=_split(merged["status"]) if merged.get("status") else None,
        refresh=bool(merged.get("refresh")),
        inbox=inbox,
        reason=_split(merged["reason"]) if merged.get("reason") else None,
        unread=bool(merged.get("unread")),
    )
