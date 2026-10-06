---
title: sys.exit inside a Textual worker kills the whole TUI
date: 2026-10-06
category: runtime-errors
module: jev_board.tui
problem_type: runtime_error
component: frontend
symptoms:
  - "Pressing r (re-fetch) or d (duel) after a gh failure or with no TYPESAFE_API_KEY closes the TUI instead of showing an error"
  - "Terminal shows 'Task exception was never retrieved' with a SystemExit traceback"
root_cause: wrong_api
resolution_type: code_fix
severity: medium
retire_when: "Textual workers start catching BaseException, or the CLI helpers stop using sys.exit for errors. Check textual/worker.py for the except clause around the worker body."
tags: [textual, worker, sysexit, tui, error-handling]
---

# sys.exit inside a Textual worker kills the whole TUI

## Problem

The CLI helpers (`github.gh_json`, `retrieval.load_repos`, `goals.load`, `typesafe.load_key`) report fatal errors with `sys.exit(message)`, which is right for a one-shot command. The TUI calls the same helpers from `@work` workers, so a recoverable error (a flaky `gh`, a missing key while pressing d on a cache-only session) ends the whole session and loses the user's framework, weights and duel history.

## Symptoms

- The app closes on a re-fetch or duel instead of showing a notification.
- `Task exception was never retrieved` followed by a `SystemExit` traceback.

## What Didn't Work

- Relying on Textual's worker error handling. Workers catch only `Exception` (textual 8.2.8, `textual/worker.py:375`), and `SystemExit` derives from `BaseException`, so it escapes the worker untouched.
- Leaving `exit_on_error` at its default. It is `True` (`textual/_work_decorator.py:44`), so even an ordinary `Exception` from the API client exits the app.

## Solution

Mark the workers `exit_on_error=False` and catch both kinds of failure at the worker boundary, turning them into a notification (`jev_board/tui.py`, `load` and `action_duel`):

```python
@work(exclusive=True, exit_on_error=False)
async def load(self, refresh=False):
    try:
        await self._load(refresh)
    except (Exception, SystemExit) as e:  # gh, git, goals and key errors exit in the CLI
        self.notify(str(e.code if isinstance(e, SystemExit) else e), severity="error", timeout=10)
        self.sub_title = "load failed · r to retry · q to quit"
```

`SystemExit.code` carries the message passed to `sys.exit`, so the user sees the same text the CLI would print.

## Why This Works

The helpers keep their CLI contract (exit with a message), and the TUI owns the decision of what a failure means on screen. Catching at the worker boundary covers every helper the worker calls, including ones added later, without touching them.

## Prevention

- Any `@work` worker that calls code shared with the CLI catches `(Exception, SystemExit)` and sets `exit_on_error=False`.
- `tests/test_tui.py` drives `JevBoard.run_test()` with `cli.prepare` raising `SystemExit` and asserts the app is still running and a notification names the error. Removing the guard makes the test fail.
- A related Textual trap in the same file: `DataTable` passes plain `str` cells through `Text.from_markup`, so untrusted text (issue titles with `[/]` or `[link=...]`) crashes rendering or injects hyperlinks. Wrap untrusted cells in `rich.text.Text(...)`.

## Related Issues

- None yet. The repo has no remote or tracker.
