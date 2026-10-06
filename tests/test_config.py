import os
from pathlib import Path

import pytest

from jev_board import config, typesafe


# ── settings ──

def test_flag_overrides_toml(tmp_path):
    toml = tmp_path / "jev.toml"
    toml.write_text('owner = "from-file"\naudience = "file audience"\nrepos = ["a", "b"]\n')
    s = config.resolve({"board": 5, "owner": "from-flag", "config": str(toml)})
    assert s.owner == "from-flag"
    assert s.audience == "file audience"
    assert s.repos == ["a", "b"]


def test_comma_separated_repos_flag_splits():
    s = config.resolve({"board": 5, "owner": "o", "repos": "x, y,,z"})
    assert s.repos == ["x", "y", "z"]


def test_defaults_are_neutral():
    s = config.resolve({"board": 1, "owner": "o"})
    assert s.framework == "wsjf"
    assert s.audience == config.DEFAULT_AUDIENCE
    assert s.goals is None and s.repos == []


def test_missing_toml_exits(tmp_path):
    with pytest.raises(SystemExit) as e:
        config.resolve({"board": 1, "owner": "o", "config": str(tmp_path / "nope.toml")})
    assert "nope.toml" in str(e.value)


def test_missing_owner_exits():
    with pytest.raises(SystemExit) as e:
        config.resolve({"board": 1})
    assert "--owner" in str(e.value)


def test_cache_root_follows_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "x"))
    assert config.cache_root() == tmp_path / "x" / "jev-board"
    monkeypatch.delenv("XDG_CACHE_HOME")
    assert config.cache_root() == Path.home() / ".cache" / "jev-board"


# ── key lookup ──

def test_key_in_environment_reads_no_file(monkeypatch, tmp_path):
    monkeypatch.setenv("TYPESAFE_API_KEY", "already-set")
    typesafe.load_key(str(tmp_path / "does-not-exist"))
    assert os.environ["TYPESAFE_API_KEY"] == "already-set"


def test_key_from_env_file_unquoted(monkeypatch, tmp_path):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    env = tmp_path / "env"
    env.write_text('OTHER=1\nTYPESAFE_API_KEY="fake-value"\n')
    typesafe.load_key(str(env))
    assert os.environ["TYPESAFE_API_KEY"] == "fake-value"


def test_env_file_path_precedence(monkeypatch):
    monkeypatch.setenv("JEV_BOARD_ENV_FILE", "/from/env")
    assert typesafe.env_file_path("/explicit") == "/explicit"
    assert typesafe.env_file_path(None) == "/from/env"
    monkeypatch.delenv("JEV_BOARD_ENV_FILE")
    assert typesafe.env_file_path(None) == str(Path.home() / ".config" / "jev-board" / "env")


def test_missing_key_names_path_and_leaks_nothing(monkeypatch, tmp_path):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    env = tmp_path / "env"
    env.write_text("SOMETHING_SECRET=hunter2\n")
    with pytest.raises(SystemExit) as e:
        typesafe.load_key(str(env))
    msg = str(e.value)
    assert str(env) in msg
    assert "hunter2" not in msg


# ── the current repo ──

HERE = {"root": "/code/api", "owner": "acme"}


def test_current_repo_fills_repos_and_owner():
    s = config.resolve({"board": 1}, here=HERE)
    assert s.repos == ["/code/api"] and s.owner == "acme"
    assert s.inferred == {"repos": ["/code/api"], "owner": "acme"}


def test_flags_and_file_win_over_the_current_repo(tmp_path):
    s = config.resolve({"board": 1, "owner": "mine", "repos": "x"}, here=HERE)
    assert s.repos == ["x"] and s.owner == "mine" and s.inferred == {}
    toml = tmp_path / "jev.toml"
    toml.write_text('owner = "file-org"\nrepos = []\n')
    s = config.resolve({"board": 1, "config": str(toml)}, here=HERE)
    assert s.repos == [] and s.owner == "file-org" and s.inferred == {}


def test_empty_repos_flag_turns_the_default_off():
    s = config.resolve({"board": 1, "repos": ""}, here=HERE)
    assert s.repos == [] and "repos" not in s.inferred


def test_inbox_takes_repos_but_never_the_owner_filter():
    s = config.resolve({"inbox": True}, here=HERE)
    assert s.repos == ["/code/api"] and s.owner is None


def test_repo_without_github_origin_still_needs_owner():
    with pytest.raises(SystemExit) as e:
        config.resolve({"board": 1}, here={"root": "/code/api", "owner": None})
    assert "--owner" in str(e.value)
