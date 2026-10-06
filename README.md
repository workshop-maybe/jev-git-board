# jev-board

Rank the open items on any GitHub Project board with Jev, the TypeSafe System One scorer. It checks each item against your local codebases and a goals file, and it never writes to GitHub.

```bash
jev-board 31 --owner my-org --repos ~/code/api,~/code/web --goals goals.md
```

On a terminal this opens a TUI. `--list` prints the ranking and `--json` prints it as JSON.

## Install

Needs Python 3.11+, [uv](https://docs.astral.sh/uv/) and an authenticated `gh` CLI.

```bash
uv sync
uv run jev-board --help
```

## How it ranks

Jev reads each open item once and scores reach, impact, urgency, effort and unblocks. It also says whether the work looks already shipped. Clarity is the sixth dimension, computed from facts: acceptance criteria, a `Blocked by:` line, file references that exist in your repos, and enough description to act on.

Frameworks are formulas over those scores, so switching between them costs nothing:

| Key | Framework | Formula |
|---|---|---|
| 1 | WSJF (default) | (impact + urgency + unblocks) ÷ effort |
| 2 | RICE | reach × impact × clarity ÷ effort |
| 3 | Impact / Complexity | impact ÷ effort, with quadrants |
| 4 | Custom | weighted sum you tune in the TUI |

An item that Jev calls shipped, with a commit that names it, sorts below the open work. Other "shipped" or "partly" answers get a flag but keep their rank.

### Codebase evidence

`--repos` takes local checkouts. Each one is read at its default branch (`origin/HEAD`, else `HEAD`), so your current branch and uncommitted edits don't change anything. For each item the tool:

- matches file paths the item names against tracked files
- searches the code for identifiers the item names, with `git grep`
- finds commits that reference the item, such as `api#12` anywhere or `fixes #12` in the item's own repo. Squash-merge suffixes like `(#12)` don't count, since issues and PRs share numbers.

That evidence goes to Jev with the item. It informs effort and the shipped check.

### Goals

`--goals` takes a markdown file. Each `## ` heading is a goal, with optional `outcome:` and `unblocks:` lines. Any `repo#n` ref in a goal's section names that item. See [examples/goals.md](examples/goals.md).

A named item gets full goal alignment. Jev scores every other item on how directly it advances a goal, in a separate small call. Alignment multiplies each framework's value, so goal work rises. Editing the goals file re-asks only that small question.

## Cache

Answers live in `$XDG_CACHE_HOME/jev-board/<owner>/board-<n>.json` (default `~/.cache/jev-board/`), outside any repo. A re-run asks Jev only about items whose text, codebase evidence or question wording changed. A run with no changes leaves the file untouched. `--refresh` re-asks everything.

## Configuration

Any flag can go in a TOML file passed with `--config`. Flags win over the file.

```toml
owner = "my-org"
repos = ["~/code/api", "~/code/web"]
goals = "~/notes/goals.md"
org = "A small team building a learning platform"
audience = "learners, customers and partners"
```

`--org` and `--audience` set the context Jev reads. The defaults are neutral.

## TypeSafe key

The key comes from `TYPESAFE_API_KEY`. If that is unset, the tool reads the first `TYPESAFE_API_KEY=` line of an env file: `--env-file`, then `JEV_BOARD_ENV_FILE`, then `~/.config/jev-board/env`. A run where every answer is cached needs no key.

## Read-only

The only `gh` calls are `gh project view` and `gh project item-list`. The tool never moves, labels or comments on items.

## Tests

```bash
uv run pytest
```

The tests run offline, with fakes for `gh` and Jev and throwaway git repos.
