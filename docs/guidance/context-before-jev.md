---
title: What to read for context before asking Jev
status: draft
date: 2026-10-06
---

# What to read for context before asking Jev

This guidance covers what the tool may gather about an item before it calls Jev, and the rules a new source must pass before it's added. It applies to board mode and inbox mode.

## What Jev sees today

Each item gets one base call, plus a small alignment call when there's a goals file. `scoring.state_for` builds the input:

| Part | Contents | Source |
|---|---|---|
| `board` | Board or inbox title, the `--org` line | Flags, `gh` |
| `item` | Ref, title, type, status (board column, or notification reason), labels, project fields, `Blocked by:` line, `referenced_by_count`, body (first 6,000 characters) | `gh` |
| `codebase` | Repos and tracked files the item names, commits that reference it | Local git, at each repo's default branch |
| `goals` (alignment call only) | Each goal's outcome and what it unblocks | `--goals` file |

The `--audience` text goes into the reach question, not into the input.

## Rules for any new source

A source has to pass all of these before it's added.

1. **It informs a named dimension.** Name the dimension it changes: reach, impact, urgency, effort, unblocks, shipped or alignment. A source that changes none of them adds tokens and no signal.
2. **Facts stay in code.** If code can count, date or measure it, the tool computes it and passes the result. Jev judges; it doesn't count. For example, pass `days_since_review_requested: 9`, not the timeline.
3. **Cache stability.** Everything in the input keys the cache (`scoring._hash`). A value that changes without changing the answer (timestamps, reaction counts, CI runs) re-asks Jev on every run. Such values are bucketed (age in weeks, not seconds), or kept out of the hash like `hits` in `retrieval.stable`, or left out.
4. **Read-only stays true.** Local sources read git objects at the default branch, never the working tree. A new GitHub source must be a GET or a GraphQL query, and it widens R5 in the plan, which has to say so.
5. **Budget per item.** Jev is asked once per item, so shared context is sent once for every item. Shared text, such as a repo description, stays under about 300 tokens. Per-item text is capped like the body is.
6. **Nothing secret leaves the machine.** Item text already goes to TypeSafe. Code file contents don't. Send paths, names and counts, never source files, configs or env files. A source that sends more text than the issue itself, such as comments or READMEs, is opt-in.
7. **Show where it came from.** Each fact carries its source, so Jev can weigh it and the TUI's detail pane can show it, as it does for `touches` and `commit`.
8. **Explicit beats inferred.** A flag or config value always wins over something read from context, and inferred values are announced, like the current-repo note.

## Sources

### GitHub, read-only

All of these come from the same GraphQL node the inbox lookup already fetches, so they cost no extra calls in inbox mode. Board mode would need one batched query per 50 items, the same pattern as the inbox.

| Source | Dimension | Cache effect | Recommendation |
|---|---|---|---|
| Native dependencies: `blockedBy`, `blocking`, `issueDependenciesSummary` | unblocks | Changes only when links change | **Add first.** It's a fact that today is guessed from a `Blocked by:` regex and a mention count. |
| Sub-issues and parent: `subIssues`, `subIssuesSummary`, `parent` | effort, unblocks | Stable | **Add.** Pass the counts and the parent's title, not the sub-issue bodies. |
| Milestone and due date: `milestone { title dueOn }` | urgency | Stable. Pass the due date, bucketed as days left in weeks. | **Add.** It's a hard deadline Jev now has to infer from prose. |
| Linked PRs: `closedByPullRequestsReferences` (issue), `closingIssuesReferences` (PR) | shipped | Changes when a PR merges, which is when the answer should change | **Add.** It fills the gap the plan names, teams that link issues only from PR bodies. |
| PR size: `additions`, `deletions`, `changedFiles` | effort | Changes with pushes. Bucket it. | **Add for PRs.** Effort to review is known, so Jev shouldn't guess it. |
| PR review state: `reviewDecision`, `isDraft` | urgency | Changes on review | **Add for inbox mode.** It tells a stale review request from one already approved. |
| Assignees | urgency (someone owns it, or nobody does) | Stable | Maybe. It's cheap, but its value is unclear. |
| Recent comments (last 3, capped) | urgency, shipped | Changes on every comment, so it re-asks often | **Opt-in only**, for example `--comments`. It's high signal ("this is blocking our launch"), but costly and the least stable. |
| CI status: `statusCheckRollup` | — | Changes every run | **Don't add.** It informs no dimension directly and would wreck cache stability. |
| Reactions, participants | reach (weak) | Changes constantly | **Don't add.** It's a noisy proxy that isn't worth the re-asks. |

### The local repo

Read at the default branch, the way retrieval does today.

| Source | Dimension | Cache effect | Recommendation |
|---|---|---|---|
| Repo self-description: the first section of `README.md`, or the overview in `CLAUDE.md` or `AGENTS.md` | reach, impact (what the product is and who uses it) | Keyed by that file's blob hash, so it re-asks only when that section changes | **Add as the default for `--org`** when `--org` isn't given. Distill it to one line, and announce it like the other inferred values. |
| Goals or roadmap doc in the repo (`ROADMAP.md`, `docs/goals.md`, `STRATEGY.md`) | alignment | Changes when goals change, which only re-asks alignment | **Add as a `--goals` default** only if the file parses in the goals format. Otherwise ignore it. Don't guess at free-form roadmaps. |
| Remote branches that name the item (`origin/*-12-*`, `*/12-*`) | shipped (`partly`), urgency (in progress) | Changes when branches come and go | **Add.** It's free, local and a strong "work has started" signal. Pass branch names only. |
| Release tags and `CHANGELOG.md` entries that name the item | shipped | Stable | Maybe. It overlaps with commit mentions, so add it only if shipped detection still misses. |
| Churn of the files the item names (commits to them in the last 90 days) | effort (risk) | Date-relative, so bucket it | Later. Calibrate first against items where effort was judged wrong. |
| `CODEOWNERS` | — | Stable | **Don't add.** It informs no dimension. |
| Contents of source files | — | — | **Never add.** It breaks rules 5 and 6. |

### Your own context

| Source | Dimension | Recommendation |
|---|---|---|
| Your role, for example `--me "backend lead, owns the API"` | urgency and impact, in inbox mode (what is yours to act on) | **Add for inbox mode.** "Review requested" means more from the person who owns the code. |
| Goals file, `--org`, `--audience` | Already used | Keep as is. |

## Suggested order

1. Native dependencies, milestone and due date, and linked PRs. These are facts already in the inbox's GraphQL node. They replace two regex guesses and close the shipped-detection gap. Board mode gets the same batched lookup.
2. PR size and review state for inbox mode, plus `--me`.
3. Remote branches that name the item.
4. A repo self-description as the `--org` default.
5. Opt-in comments.

Each step bumps `SCHEMA_VERSION`, which re-asks Jev about every item once. Batch the steps into as few releases as you can.

## How to tell whether a source helped

- **Ranking diff.** Rank a fixed board before and after, and read the items that moved more than five places. Each move should be explainable by the new fact.
- **Duels.** Run the TUI's duel on pairs the new source should separate. Jev's answer should agree more with the ranking, and depend less on which item is listed first.
- **Tokens.** Compare input tokens per item before and after, from the `--list` header. Note the cost of any source over 10%.
- **Cache hit rate.** Re-run with nothing changed, and the tool should ask Jev about 0 items. Then re-run a day later: only items with real activity should be re-asked.

## Open questions

- Should the repo self-description be distilled by Jev once per run, which costs tokens and a cache entry of its own, or by a fixed rule, such as the first paragraph capped at 300 characters?
- In board mode, is one GraphQL query per 50 items acceptable on every run, or should those facts be cached by `updatedAt`?
- Should `--comments` take only comments since the user's last activity (inbox mode), or the last N (board mode)?
