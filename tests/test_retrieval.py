import os
import subprocess

import pytest

from jev_board import retrieval

ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
       "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@t"}


def git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], env=ENV, check=True,
                          capture_output=True, text=True).stdout


def commit(path, msg, files=None):
    for name, text in (files or {}).items():
        f = path / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    git(path, "add", "-A")
    git(path, "commit", "-q", "--allow-empty", "-m", msg)


def make_repo(tmp_path, name, files=None, msg="init"):
    path = tmp_path / name
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    commit(path, msg, files)
    return path


def item(ref, title="", body=""):
    return {"ref": ref, "title": title, "body": body}


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path, "repo", {
        "src/app/login.ts": "export function login() {}\n",
        "src/goals.py": "def parseGoals(text):\n    return []\n",
        "README.md": "hello\n",
    })


# ── files and symbols ──

def test_path_matches_tracked_file(repo):
    ev = retrieval.evidence(item("repo#1", "Fix login", "See src/app/login.ts"),
                            retrieval.load_repos([str(repo)]))
    assert ev["files"] == ["repo:src/app/login.ts"]
    assert ev["repos"] == ["repo"]


def test_backticked_symbol_found_by_grep(repo):
    ev = retrieval.evidence(item("repo#2", "Speed up `parseGoals`"),
                            retrieval.load_repos([str(repo)]))
    assert ev["hits"] == {"repo": 1}
    assert ev["repos"] == ["repo"]


def test_no_code_references_is_empty(repo):
    ev = retrieval.evidence(item("repo#3", "Make the onboarding nicer", "Users find it confusing."),
                            retrieval.load_repos([str(repo)]))
    assert ev == {"files": [], "repos": [], "hits": {}, "mentions": []}


def test_running_twice_is_identical(repo):
    it = item("repo#1", "Fix login in `parseGoals`", "src/app/login.ts")
    assert (retrieval.evidence(it, retrieval.load_repos([str(repo)]))
            == retrieval.evidence(it, retrieval.load_repos([str(repo)])))


def test_unrelated_commit_with_symbol_keeps_stable_evidence(repo):
    it = item("repo#2", "Speed up `parseGoals`", "touches src/goals.py")
    before = retrieval.evidence(it, retrieval.load_repos([str(repo)]))
    commit(repo, "Add notes", {"docs/notes.md": "mentions parseGoals here\n"})
    after = retrieval.evidence(it, retrieval.load_repos([str(repo)]))
    assert after["hits"] == {"repo": 2}
    assert retrieval.stable(after) == retrieval.stable(before)


# ── mention commits ──

def test_qualified_ref_matches_and_is_word_bounded(repo):
    commit(repo, "Fix login (repo#7)")
    repos = retrieval.load_repos([str(repo)])
    assert [m["subject"] for m in retrieval.evidence(item("repo#7"), repos)["mentions"]] == ["Fix login (repo#7)"]
    assert retrieval.evidence(item("repo#70"), repos)["mentions"] == []


def test_bare_ref_in_other_repo_is_not_evidence(tmp_path, repo):
    other = make_repo(tmp_path, "other", {"a.txt": "a\n"}, msg="fixes #7")
    ev = retrieval.evidence(item("repo#7"), retrieval.load_repos([str(repo), str(other)]))
    assert ev["mentions"] == []


def test_bare_ref_needs_keyword_and_squash_suffix_never_counts(repo):
    commit(repo, "Add export (#7)")
    commit(repo, "Tidy\n\nCloses #7")
    ev = retrieval.evidence(item("repo#7"), retrieval.load_repos([str(repo)]))
    sha = git(repo, "rev-parse", "HEAD").strip()
    assert ev["mentions"] == [{"repo": "repo", "sha": sha, "subject": "Tidy"}]
    assert retrieval.stable(ev)["mentions"] == [sha]


# ── repo loading ──

def test_evidence_reflects_default_branch(tmp_path):
    work = make_repo(tmp_path, "work", {"src/core.py": "def coreThing(): pass\n"})
    origin = tmp_path / "proj.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    git(work, "remote", "add", "origin", str(origin))
    git(work, "push", "-q", "origin", "main")
    git(work, "remote", "set-head", "origin", "-a")
    main_sha = git(work, "rev-parse", "HEAD").strip()
    git(work, "checkout", "-q", "-b", "feature")
    commit(work, "fixes #9", {"src/new.py": "coreThing()\n"})
    (work / "src" / "core.py").write_text("coreThing coreThing\n")
    (work / "src" / "dirty.py").write_text("coreThing\n")

    [r] = retrieval.load_repos([str(work)])
    assert (r.name, r.ref, r.sha) == ("proj", "origin/HEAD", main_sha)
    ev = retrieval.evidence(item("proj#9", "`coreThing`", "src/new.py src/core.py"), [r])
    assert ev == {"files": ["proj:src/core.py"], "repos": ["proj"], "hits": {"proj": 1}, "mentions": []}


def test_non_git_path_exits_naming_it(tmp_path):
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    with pytest.raises(SystemExit) as e:
        retrieval.load_repos([str(plain)])
    assert str(plain) in str(e.value)
