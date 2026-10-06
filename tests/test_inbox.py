import json
import re
import subprocess

import pytest

from jev_board import cache, cli, config, github, typesafe
from test_scoring import FakeClient


def thread(tid, owner, repo, number, kind="Issue", reason="mention", unread=False, title=None):
    path = "pulls" if kind == "PullRequest" else "issues"
    return {"id": tid, "reason": reason, "unread": unread, "updated_at": "2026-10-01T00:00:00Z",
            "subject": {"type": kind, "title": title or f"{repo} {number}",
                        "url": f"https://api.github.com/repos/{owner}/{repo}/{path}/{number}"},
            "repository": {"name": repo, "full_name": f"{owner}/{repo}", "owner": {"login": owner}}}


THREADS = [
    thread("1", "acme", "api", 12, reason="review_requested", kind="PullRequest", unread=True),
    thread("2", "acme", "api", 5),
    thread("3", "acme", "web", 7, reason="assign"),
    thread("4", "acme", "api", 8, reason="author"),        # closed
    thread("5", "other", "lib", 3, reason="subscribed"),
    thread("6", "acme", "gone", 1),                        # repo deleted: GraphQL returns null
    {**thread("7", "acme", "api", 0), "subject": {"type": "Release", "title": "v1",
                                                   "url": "https://api.github.com/repos/acme/api/releases/99"}},
    {**thread("8", "acme", "api", 0), "subject": {"type": "CheckSuite", "title": "CI", "url": None}},
]

NODES = {
    "acme/api#12": {"__typename": "PullRequest", "state": "OPEN", "isDraft": True,
                    "url": "https://github.com/acme/api/pull/12", "author": {"login": "ana"},
                    "labels": {"nodes": [{"name": "feature"}]},
                    "body": "Adds export.\n\n**Blocked by:** web#7"},
    "acme/api#5": {"__typename": "Issue", "state": "OPEN", "url": "https://github.com/acme/api/issues/5",
                   "author": {"login": "ben"}, "labels": {"nodes": []}, "body": "Needs api#12 first."},
    "acme/web#7": {"__typename": "Issue", "state": "OPEN", "url": "https://github.com/acme/web/issues/7",
                   "author": None, "labels": {"nodes": []}, "body": "See #12"},  # web's #12, not api's
    "acme/api#8": {"__typename": "Issue", "state": "CLOSED", "url": "https://github.com/acme/api/issues/8",
                   "author": {"login": "ana"}, "labels": {"nodes": []}, "body": "Follow-up to #12"},
    "other/lib#3": {"__typename": "Issue", "state": "OPEN", "url": "https://github.com/other/lib/issues/3",
                    "author": {"login": "cy"}, "labels": {"nodes": []}, "body": ""},
}


def fake_gh(calls, threads=THREADS, nodes=NODES):
    """A `gh api` runner over the fixtures above. GraphQL answers from the variables it
    was passed; a missing repo comes back null with an error, as GitHub does."""
    def run(argv):
        calls.append(argv)
        assert argv[:2] == ["gh", "api"]
        if argv[2] == "user":
            return json.dumps({"login": "me"})
        if argv[2].startswith("notifications"):
            unread_only = "all=true" not in argv[2]
            return json.dumps([t for t in threads if t["unread"] or not unread_only])
        assert argv[2] == "graphql"
        vals = dict(a.split("=", 1) for a in argv[3:] if "=" in a and not a.startswith("query="))
        data, errors = {}, []
        for j in range(len([k for k in vals if k.startswith("n")])):
            key = f"{vals[f'o{j}']}/{vals[f'r{j}']}#{vals[f'n{j}']}"
            if key in nodes:
                data[f"t{j}"] = {"issueOrPullRequest": nodes[key]}
            else:
                data[f"t{j}"] = None
                errors.append({"type": "NOT_FOUND", "path": [f"t{j}"]})
        out = json.dumps({"data": data, **({"errors": errors} if errors else {})})
        if errors:
            raise subprocess.CalledProcessError(1, argv, output=out, stderr="gh: Could not resolve\n")
        return out
    return run


def by_ref(items):
    return {i["ref"]: i for i in items}


def test_keeps_open_issues_and_prs_only():
    meta, items, total = github.fetch_inbox(run=fake_gh([]))
    assert meta == {"title": "me's inbox", "login": "me"}
    assert total == len(THREADS)
    # closed api#8, deleted gone#1, the release and the check suite are dropped
    assert sorted(by_ref(items)) == ["api#12", "api#5", "lib#3", "web#7"]


def test_item_shape():
    _, items, _ = github.fetch_inbox(run=fake_gh([]))
    it = by_ref(items)["api#12"]
    assert it["id"] == "1" and it["repo"] == "api" and it["number"] == 12
    assert it["type"] == "PullRequest" and it["status"] == "review_requested"
    assert it["url"] == "https://github.com/acme/api/pull/12"
    assert it["labels"] == ["feature"]
    assert it["fields"] == {"repository": "acme/api", "author": "ana", "draft": True}
    assert it["blocked_by"] == "web#7"
    assert by_ref(items)["web#7"]["fields"] == {"repository": "acme/web"}  # no author, no draft


def test_referenced_by_counts_other_threads_including_closed():
    _, items, _ = github.fetch_inbox(run=fake_gh([]))
    # api#5 (repo#n) and closed api#8 (same-repo #12) count; web#7's #12 is web's own
    assert by_ref(items)["api#12"]["referenced_by"] == 2
    assert by_ref(items)["web#7"]["referenced_by"] == 1


def test_reason_owner_and_unread_filters():
    _, items, _ = github.fetch_inbox(reasons=["review_requested", "assign"], run=fake_gh([]))
    assert sorted(by_ref(items)) == ["api#12", "web#7"]
    _, items, _ = github.fetch_inbox(owner="OTHER", run=fake_gh([]))
    assert sorted(by_ref(items)) == ["lib#3"]
    calls = []
    _, items, total = github.fetch_inbox(unread=True, run=fake_gh(calls))
    assert sorted(by_ref(items)) == ["api#12"] and total == 1
    assert "all=true" not in calls[1][2]


def test_only_read_calls():
    calls = []
    github.fetch_inbox(run=fake_gh(calls))
    assert [c[2].split("?")[0] for c in calls] == ["user", "notifications", "graphql"]
    for c in calls:
        assert not {"-X", "--method", "--input"} & set(c)
        if c[2] != "graphql":
            assert not {"-f", "-F", "--field", "--raw-field"} & set(c)
    query = next(a for a in calls[2] if a.startswith("query="))
    assert query.startswith("query=query(") and "mutation" not in query


def test_lookups_are_batched(monkeypatch):
    monkeypatch.setattr(github, "CHUNK", 2)
    calls = []
    _, items, _ = github.fetch_inbox(run=fake_gh(calls))
    assert len([c for c in calls if c[2] == "graphql"]) == 3  # six issue/PR threads, two per query
    assert len(items) == 4


def test_repo_names_go_as_variables_not_query_text():
    calls = []
    github.fetch_inbox(owner="acme", run=fake_gh(calls))
    query = next(a for a in calls[2] if a.startswith("query="))
    assert "acme" not in query and not re.search(r"\bapi\b", query)
    assert "-F" in calls[2] and "n0=12" in calls[2]


def test_paginated_output_with_several_documents():
    def run(argv):
        if argv[2] == "user":
            return '{"login": "me"}'
        if argv[2].startswith("notifications"):
            return json.dumps(THREADS[:2]) + "\n" + json.dumps(THREADS[2:3])
        return fake_gh([])(argv)
    _, items, total = github.fetch_inbox(run=run)
    assert total == 3 and sorted(by_ref(items)) == ["api#12", "api#5", "web#7"]


def test_graphql_failure_without_data_exits():
    def run(argv):
        if argv[2] == "graphql":
            raise subprocess.CalledProcessError(1, argv, output="", stderr="HTTP 502\n")
        return fake_gh([])(argv)
    with pytest.raises(SystemExit) as e:
        github.fetch_inbox(run=run)
    assert "HTTP 502" in str(e.value)


# ── settings ──

def test_inbox_needs_no_board_or_owner():
    s = config.resolve({"inbox": True, "reason": "review_requested, mention"})
    assert s.inbox and s.board is None and s.owner is None
    assert s.reason == ["review_requested", "mention"]


@pytest.mark.parametrize("flags, msg", [
    ({"inbox": True, "board": 3}, "not both"),
    ({"inbox": True, "status": ["Todo"]}, "--reason"),
    ({"board": 3, "owner": "o", "reason": ["mention"]}, "only apply with --inbox"),
    ({"board": 3, "owner": "o", "unread": True}, "only apply with --inbox"),
    ({"owner": "o"}, "--inbox"),
])
def test_inbox_flag_conflicts_exit(flags, msg):
    with pytest.raises(SystemExit) as e:
        config.resolve(flags)
    assert msg in str(e.value)


def test_inbox_cache_cannot_collide_with_a_board():
    assert cache.inbox_path("me", "/c") != cache.path_for("inbox", "me", "/c")
    assert str(cache.inbox_path("me", "/c")) == "/c/@inbox/me.json"


# ── end to end ──

@pytest.fixture
def offline(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(github, "_subprocess_run", fake_gh([]))
    monkeypatch.setattr(typesafe, "AsyncTypeSafeClient", lambda: FakeClient({}, calls))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    return tmp_path


def test_cli_ranks_the_inbox_and_caches_per_user(offline, capsys):
    cli.main(["--inbox", "--list"])
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("inbox · me's inbox · 4 open of 8")
    assert (offline / "jev-board" / "@inbox" / "me.json").exists()
    cli.main(["--inbox", "--json"])
    rows = json.loads(capsys.readouterr().out)
    assert {r["ref"] for r in rows} == {"api#12", "api#5", "lib#3", "web#7"}
    assert {r["status"] for r in rows} == {"review_requested", "mention", "assign", "subscribed"}
