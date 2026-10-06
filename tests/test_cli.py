import json
from pathlib import Path

import pytest

from jev_board import cli, github, typesafe
from test_scoring import FakeClient

FIXTURES = Path(__file__).parent / "fixtures" / "github"


@pytest.fixture
def offline(monkeypatch, tmp_path):
    """Stand in for the two process boundaries: `gh` and the Jev client."""
    def gh(argv):
        name = "project_view.json" if argv[2] == "view" else "item_list.json"
        return (FIXTURES / name).read_text()
    calls = []
    monkeypatch.setattr(github, "_subprocess_run", gh)
    monkeypatch.setattr(typesafe, "AsyncTypeSafeClient", lambda: FakeClient({}, calls))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    return calls


def test_json_prints_ranked_records(offline, capsys):
    cli.main(["7", "--owner", "o", "--json"])
    rows = json.loads(capsys.readouterr().out)
    assert rows and [r["rank"] for r in rows] == list(range(1, len(rows) + 1))
    assert set(rows[0]) >= {"rank", "ref", "title", "status", "value", "norm", "shipped", "goals"}


def test_list_prints_header_then_rows(offline, capsys):
    cli.main(["7", "--owner", "o", "--list"])
    first = capsys.readouterr().out.splitlines()
    assert "asked" in first[0] and "cached" in first[0]
    cli.main(["7", "--owner", "o", "--list"])
    out = capsys.readouterr().out.splitlines()
    assert "0 asked" in out[0]
    raw = json.loads((FIXTURES / "item_list.json").read_text())["items"]
    assert len([l for l in out[2:] if l.strip()]) == len([i for i in raw if i["status"] not in github.DONE])


def test_missing_owner_exits(offline):
    with pytest.raises(SystemExit) as e:
        cli.main(["7", "--list"])
    assert "--owner" in str(e.value)


def test_non_terminal_falls_back_to_list(offline, capsys):
    cli.main(["7", "--owner", "o"])  # pytest's captured stdout is not a terminal
    assert capsys.readouterr().out.startswith("board 7")
