import subprocess
from pathlib import Path

import pytest

from jev_board import github

FIXTURES = Path(__file__).parent / "fixtures" / "github"


def fake_gh(calls):
    """A `gh` runner that records each argv and answers from the recorded fixtures."""
    def run(argv):
        calls.append(argv)
        name = "project_view.json" if argv[1:3] == ["project", "view"] else "item_list.json"
        return (FIXTURES / name).read_text()
    return run


def by_ref(items):
    return {i["ref"]: i for i in items}


def test_done_columns_dropped_by_default():
    meta, items, total = github.fetch(3, "example-org", run=fake_gh([]))
    assert meta["title"] == "Team board"
    assert total == 7
    assert {i["status"] for i in items} == {"Todo", "In Progress", "Backlog"}


def test_status_filter_keeps_named_columns():
    _, items, _ = github.fetch(3, "example-org", statuses=["Done", "Shipped"], run=fake_gh([]))
    assert sorted(i["ref"] for i in items) == ["api#8", "web#9"]


def test_item_shape_and_blocked_by():
    _, items, _ = github.fetch(3, "example-org", run=fake_gh([]))
    it = by_ref(items)["api#12"]
    assert it["repo"] == "api" and it["number"] == 12
    assert it["url"] == "https://github.com/example-org/api/issues/12"
    assert it["type"] == "Issue" and it["labels"] == ["feature"]
    assert it["fields"] == {"priority": "P1"}
    assert it["blocked_by"] == "web#7"


def test_draft_item():
    _, items, _ = github.fetch(3, "example-org", run=fake_gh([]))
    d = by_ref(items)["draft"]
    assert d["repo"] is None and d["number"] is None
    assert d["type"] == "DraftIssue"
    assert d["referenced_by"] == 0


def test_referenced_by_counts_ref_url_and_same_repo_shorthand():
    _, items, _ = github.fetch(3, "example-org", run=fake_gh([]))
    refs = by_ref(items)
    # api#5 (repo#n), web#7 (URL) and done api#8 (same-repo #12) count; #123 and web's #12 do not
    assert refs["api#12"]["referenced_by"] == 3
    assert refs["api#5"]["referenced_by"] == 1
    assert refs["api#6"]["referenced_by"] == 0


def test_runner_sees_only_read_calls():
    calls = []
    github.fetch(3, "example-org", run=fake_gh(calls))
    assert [c[:3] for c in calls] == [["gh", "project", "view"], ["gh", "project", "item-list"]]
    assert all(c[3] == "3" and "--owner" in c and c[c.index("--owner") + 1] == "example-org"
               for c in calls)
    assert all(c[-2:] == ["--format", "json"] for c in calls)


def test_gh_failure_exits_with_stderr():
    def run(argv):
        raise subprocess.CalledProcessError(1, argv, stderr="could not resolve to a ProjectV2\n")
    with pytest.raises(SystemExit) as e:
        github.gh_json("project", "view", "3", run=run)
    assert "could not resolve to a ProjectV2" in str(e.value)

