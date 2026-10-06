"""
Deterministic, local codebase evidence per board item (KTD5).

Each repo is read once at its default branch (`origin/HEAD`, else `HEAD`), never the
working tree. Paths named in an item match tracked files by suffix and fill `files`.
Symbols are searched with one fixed-string `git grep` per item per repo and only count
toward `repos` and the display-only `hits`, so an unrelated commit that happens to use a
symbol never changes the stable part that keys the cache.
"""
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

SYMS = 12   # candidate paths + symbols per item
FILES = 20  # matched files per item, and per-repo grep hits counted

KEYWORDS = r"fix(?:e[sd])?|close[sd]?|resolve[sd]?|refs|part\s+of"
STOP = {"none", "null", "true", "false", "self", "this", "todo", "http", "https"}

_PATH = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)*[\w-]+\.[A-Za-z]\w{0,5}|[\w.-]+(?:/[\w.-]+)+)")
_TICK = re.compile(r"`([^`\s]+)`")
_SYM = re.compile(r"\b(?:[a-z]+[A-Z]\w*|[A-Z][a-z0-9]+[A-Z]\w*|[a-z0-9]+_[a-z0-9_]+)\b")


@dataclass
class Repo:
    name: str
    path: str
    ref: str
    sha: str
    files: list[str] = field(default_factory=list)
    log: list[tuple[str, str]] = field(default_factory=list)  # (sha, full message)


def _git(path, *args, ok=(0,)):
    p = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True)
    if p.returncode not in ok:
        return None
    return p.stdout


def _name(path):
    url = (_git(path, "config", "--get", "remote.origin.url", ok=(0, 1)) or "").strip().rstrip("/")
    last = re.split(r"[/:]", url)[-1] if url else ""
    return last.removesuffix(".git") or Path(path).resolve().name


def _load(path):
    if _git(path, "rev-parse", "--git-dir") is None:
        sys.exit(f"not a git repository: {path}")
    for ref in ("origin/HEAD", "HEAD"):
        sha = _git(path, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}", ok=(0, 1))
        if sha and sha.strip():
            break
    else:
        sys.exit(f"git repository has no commits: {path}")
    files = _git(path, "ls-tree", "-r", "--name-only", ref).splitlines()
    raw = _git(path, "log", ref, "--format=%H%x00%B%x1e")
    log = [tuple(e.strip("\n").split("\0", 1)) for e in raw.split("\x1e") if e.strip()]
    return Repo(_name(path), path, ref, sha.strip(), files, log)


def load_repos(paths):
    return [_load(p) for p in paths]


def _candidates(text):
    """Path-like tokens and code-shaped symbols, in first-seen order, capped."""
    paths, syms = [], []
    for m in sorted([(m.start(), m.group(1), "p") for m in _PATH.finditer(text)]
                    + [(m.start(), m.group(1), "t") for m in _TICK.finditer(text)]
                    + [(m.start(), m.group(0), "s") for m in _SYM.finditer(text)]):
        _, tok, kind = m
        tok = tok.rstrip(".").removeprefix("./")
        if "://" in tok or len(tok) < 4 or tok.lower() in STOP or tok in paths or tok in syms:
            continue
        if len(paths) + len(syms) >= SYMS:
            break
        (paths if kind == "p" or (kind == "t" and ("/" in tok or "." in tok)) else syms).append(tok)
    return paths, syms


def _grep(repo, syms):
    if not syms:
        return []
    args = [a for s in syms for a in ("-e", s)]
    out = _git(repo.path, "grep", "-l", "-F", "-w", *args, repo.ref, "--", ok=(0, 1)) or ""
    return sorted(line.split(":", 1)[1] for line in out.splitlines())[:FILES]


def _mentions(item_repo, n, repo):
    qual = re.compile(rf"(?<![\w.-]){re.escape(item_repo)}#{n}(?!\d)", re.I)
    bare = re.compile(rf"\b(?:{KEYWORDS}):?\s+#{n}(?!\d)", re.I) if repo.name == item_repo else None
    return [{"repo": repo.name, "sha": sha, "subject": msg.split("\n", 1)[0]}
            for sha, msg in repo.log if qual.search(msg) or (bare and bare.search(msg))]


def evidence(item, repos):
    item_repo, _, n = item["ref"].partition("#")
    paths, syms = _candidates(f"{item['title']}\n{item.get('body') or ''}")
    files, hits, mentions = set(), {}, []
    for r in repos:
        files |= {f"{r.name}:{f}" for f in r.files for p in paths if f == p or f.endswith("/" + p)}
        if found := _grep(r, syms):
            hits[r.name] = len(found)
        if n.isdigit():
            mentions += _mentions(item_repo, n, r)
    files = sorted(files)[:FILES]
    touched = {f.split(":", 1)[0] for f in files} | set(hits)
    return {"files": files, "repos": sorted(touched), "hits": dict(sorted(hits.items())),
            "mentions": sorted(mentions, key=lambda m: (m["repo"], m["sha"]))}


def stable(ev):
    """The part of the evidence that keys the cache: hit counts are left out."""
    return {"repos": ev["repos"], "files": ev["files"], "mentions": [m["sha"] for m in ev["mentions"]]}
