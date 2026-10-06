"""
jev-board: rank a GitHub Project board's open items with Jev, read-only to GitHub.

    jev-board 31 --owner my-org --repos ~/code/api,~/code/web --goals goals.md

Opens the TUI on a terminal; --list or --json print the ranking instead. Answers are
cached under ~/.cache/jev-board/, so switching framework costs nothing and a re-run asks
Jev only about items whose text or codebase evidence changed.
"""
import argparse
import asyncio
import json
import sys

from jev_board import cache, config, github, goals, retrieval, scoring


def parse(argv=None):
    ap = argparse.ArgumentParser(prog="jev-board", description=__doc__.strip().split("\n")[0])
    ap.add_argument("board", type=int, help="GitHub Project number")
    ap.add_argument("--owner", help="org or user that owns the project")
    ap.add_argument("--repos", help="comma-separated local checkouts to search for evidence")
    ap.add_argument("--goals", help="markdown goals file (see examples/goals.md)")
    ap.add_argument("--status", action="append", help="only these columns (repeatable)")
    ap.add_argument("--framework", choices=scoring.FRAMEWORKS)
    ap.add_argument("--org", help="one line describing the organization, for Jev")
    ap.add_argument("--audience", help="who counts toward reach, e.g. 'customers and partners'")
    ap.add_argument("--config", help="TOML file with any of the options above")
    ap.add_argument("--env-file", dest="env_file", help="file holding TYPESAFE_API_KEY=...")
    ap.add_argument("--refresh", action="store_true", default=None, help="re-ask Jev even for unchanged items")
    out = ap.add_mutually_exclusive_group()
    out.add_argument("--list", action="store_true", help="print the ranking")
    out.add_argument("--json", action="store_true", help="print the ranking as JSON")
    a = ap.parse_args(argv)
    given = {k: v for k, v in vars(a).items() if k not in ("list", "json")}
    return config.resolve(given), a


def prepare(s):
    """The blocking inputs: board items, codebases and goals. Returns (meta, items, total, repos, goals, ctx)."""
    meta, items, total = github.fetch(s.board, s.owner, s.status)
    repos = retrieval.load_repos(s.repos)
    g = goals.load(s.goals) if s.goals else None
    return meta, items, total, repos, g, context(s, meta)


def load(s):
    """Prepare and assess. Returns (meta, items, total, stats)."""
    meta, items, total, repos, g, ctx = prepare(s)
    stats = asyncio.run(scoring.assess(items, ctx, repos, g, s.refresh))
    return meta, items, total, stats


def context(s, meta):
    return scoring.Context(owner=s.owner, board=s.board, title=meta.get("title", ""), org=s.org,
                           audience=s.audience, cache_path=cache.path_for(s.owner, s.board),
                           env_file=s.env_file)


def flags(it):
    return ("✓" if scoring.sinks(it) else "~" if it["shipped"] != "open" else " ") + \
        ("★" if it["goals"] else " ")


def print_list(s, meta, items, total, stats):
    ranked = scoring.rank(items, s.framework)
    cached = len(items) - stats["asked"]
    print(f"board {s.board} · {meta.get('title', '')} · {len(items)} open of {total} · "
          f"{stats['asked']} asked, {stats['aligned']} aligned ({stats['tokens']} in tokens), "
          f"{cached} cached · {scoring.FRAMEWORKS[s.framework][0]}\n")
    for it in ranked:
        dims = " ".join(f"{scoring.SHORT[k]}{v:.1f}" for k, v in it["norm"].items())
        print(f"{it['rank']:>3}. {it['rel']:.2f} {flags(it)} {dims}  {it['ref']:<24} {it['title'][:60]}")


def print_json(s, items):
    keys = ("rank", "ref", "title", "status", "value", "norm", "shipped", "goals")
    print(json.dumps([{k: it[k] for k in keys} for it in scoring.rank(items, s.framework)], indent=1))


def main(argv=None):
    s, a = parse(argv)
    if not (a.list or a.json) and sys.stdout.isatty():
        from jev_board.tui import run
        run(s)
        return
    meta, items, total, stats = load(s)
    if stats["failed"]:
        print(f"warning: Jev failed on {stats['failed']} item(s), left out of the ranking: {stats['error']}",
              file=sys.stderr)
    if a.json:
        print_json(s, items)
    else:
        print_list(s, meta, items, total, stats)
