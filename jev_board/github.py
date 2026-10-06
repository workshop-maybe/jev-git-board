"""
Read-only fetches. A board uses `gh project view` and `gh project item-list`. The inbox
uses `gh api` GETs (`user`, `notifications`) and GraphQL queries, never a mutation, so
reading it marks nothing read or done.
"""
import json
import re
import subprocess
import sys

DONE = ("Done", "Closed", "Shipped", "Won't do", "Archived")


def _subprocess_run(argv):
    return subprocess.run(argv, capture_output=True, text=True, check=True).stdout


def gh_json(*args, run=None):
    """Run `gh <args> --format json` and parse its output. `run(argv) -> stdout`."""
    argv = ["gh", *args, "--format", "json"]
    try:
        out = (run or _subprocess_run)(argv)
    except subprocess.CalledProcessError as e:
        sys.exit(f"gh {' '.join(args)} failed: {(e.stderr or '').strip()}")
    return json.loads(out)


def _parse_all(out):
    """Every JSON document in `out`; some `gh` versions print one per page."""
    dec, i, docs = json.JSONDecoder(), 0, []
    while True:
        while i < len(out) and out[i].isspace():
            i += 1
        if i >= len(out):
            return docs
        doc, i = dec.raw_decode(out, i)
        docs.append(doc)


def gh_api(*args, run=None):
    """Run `gh api <args>` and parse its output. A GraphQL reply with partial errors still
    returns its data, so one deleted repo doesn't sink the rest."""
    argv = ["gh", "api", *args]
    try:
        out = (run or _subprocess_run)(argv)
    except subprocess.CalledProcessError as e:
        docs = _parse_all(e.stdout or "") if (e.stdout or "").lstrip().startswith("{") else []
        if docs and isinstance(docs[0], dict) and docs[0].get("data"):
            return docs
        sys.exit(f"gh api {args[0]} failed: {(e.stderr or '').strip()}")
    return _parse_all(out)


def _blocked_by(body):
    m = re.search(r"\*\*Blocked by:?\*\*:?\s*(.+)", body)
    return m.group(1).strip()[:200] if m else None


def _count_references(items, texts):
    """Set `referenced_by` on each item: how many of `texts` (body, id, repo) mention it."""
    for item in items:
        if item["ref"] == "draft":
            item["referenced_by"] = 0
            continue
        repo, num = item["repo"], item["number"]
        pat = re.compile(rf"(?:{re.escape(repo)}#{num}\b|/{re.escape(repo)}/(?:issues|pull)/{num}\b)")
        bare = re.compile(rf"(?<![\w/#-])#{num}\b")  # same-repo shorthand
        item["referenced_by"] = sum(1 for t, i, r in texts if i != item["id"]
                                    and (pat.search(t) or (r == repo and bare.search(t))))


def _repo_of(content):
    return (content.get("repository") or "").split("/")[-1] or None


def ref_of(content):
    repo = _repo_of(content)
    return f"{repo}#{content['number']}" if content.get("number") else None


def fetch(board, owner, statuses=None, run=None):
    """Open items on the board as (meta, items, total). `statuses` keeps only those
    columns; by default every column except the done-like ones."""
    meta = gh_json("project", "view", str(board), "--owner", owner, run=run)
    data = gh_json("project", "item-list", str(board), "--owner", owner, "--limit", "2000", run=run)
    raw = data["items"]
    items = []
    for it in raw:
        c = it.get("content") or {}
        status = it.get("status") or "(no status)"
        if statuses and status not in statuses:
            continue
        if not statuses and status in DONE:
            continue
        body = c.get("body") or ""
        fields = {k: v for k, v in it.items()
                  if k not in ("content", "id", "title", "labels", "repository", "status") and v}
        ref = ref_of(c)
        items.append({
            "id": it["id"], "ref": ref or "draft",
            "repo": _repo_of(c) if ref else None, "number": c.get("number") if ref else None,
            "url": c.get("url", ""),
            "title": it.get("title") or c.get("title", ""), "type": c.get("type", "DraftIssue"),
            "status": status, "labels": it.get("labels") or [], "fields": fields,
            "body": body, "blocked_by": _blocked_by(body),
        })
    # who on the whole board (done items included) mentions this item
    _count_references(items, [((it.get("content") or {}).get("body") or "", it["id"],
                               _repo_of(it.get("content") or {})) for it in raw])
    return meta, items, len(raw)


# ── The notification inbox. ──

INBOX_TYPES = ("Issue", "PullRequest")
CHUNK = 50  # threads per GraphQL query
NODE = ("__typename ... on Issue { state body url author { login } labels(first: 20) { nodes { name } } } "
        "... on PullRequest { state isDraft body url author { login } labels(first: 20) { nodes { name } } }")


def _lookup(threads, run=None):
    """The issue or PR behind each thread, or None when it is gone or hidden. One
    GraphQL query per CHUNK threads, with owner, name and number passed as variables."""
    out = []
    for start in range(0, len(threads), CHUNK):
        chunk = threads[start:start + CHUNK]
        decl, parts, args = [], [], []
        for j, t in enumerate(chunk):
            repo = t["repository"]
            decl.append(f"$o{j}: String!, $r{j}: String!, $n{j}: Int!")
            parts.append(f"t{j}: repository(owner: $o{j}, name: $r{j}) "
                         f"{{ issueOrPullRequest(number: $n{j}) {{ {NODE} }} }}")
            args += ["-f", f"o{j}={repo['owner']['login']}", "-f", f"r{j}={repo['name']}",
                     "-F", f"n{j}={t['_number']}"]
        query = f"query({', '.join(decl)}) {{ {' '.join(parts)} }}"
        data = gh_api("graphql", "-f", f"query={query}", *args, run=run)[0].get("data") or {}
        out += [(data.get(f"t{j}") or {}).get("issueOrPullRequest") for j in range(len(chunk))]
    return out


def fetch_inbox(reasons=None, owner=None, unread=False, run=None):
    """Open issues and PRs in the signed-in user's notification inbox, as (meta, items,
    total). `status` holds the notification reason (review_requested, mention, ...).
    `reasons` keeps only those; `owner` keeps one org or user's repos; `unread` skips
    threads already read. Closed and merged work, and other thread types, are dropped."""
    login = gh_api("user", run=run)[0]["login"]
    query = "notifications?per_page=50" + ("" if unread else "&all=true")
    threads = [t for page in gh_api(query, "--paginate", run=run) for t in page]
    picked = []
    for t in threads:
        subj, repo = t.get("subject") or {}, t.get("repository") or {}
        if subj.get("type") not in INBOX_TYPES or not (subj.get("url") or "").rsplit("/", 1)[-1].isdigit():
            continue
        if reasons and t.get("reason") not in reasons:
            continue
        if owner and repo["owner"]["login"].lower() != owner.lower():
            continue
        picked.append({**t, "_number": int(subj["url"].rsplit("/", 1)[-1])})
    nodes = _lookup(picked, run)
    items = []
    for t, node in zip(picked, nodes):
        if not node or node.get("state") != "OPEN":
            continue
        repo, body = t["repository"], node.get("body") or ""
        fields = {"repository": repo["full_name"], "author": (node.get("author") or {}).get("login")}
        if node.get("isDraft"):
            fields["draft"] = True
        items.append({
            "id": t["id"], "ref": f"{repo['name']}#{t['_number']}",
            "repo": repo["name"], "number": t["_number"], "url": node.get("url", ""),
            "title": t["subject"]["title"], "type": t["subject"]["type"], "status": t["reason"],
            "labels": [n["name"] for n in (node.get("labels") or {}).get("nodes") or []],
            "fields": {k: v for k, v in fields.items() if v}, "body": body, "blocked_by": _blocked_by(body),
        })
    # who else in the inbox (closed work included) mentions this item
    _count_references(items, [((n or {}).get("body") or "", t["id"], t["repository"]["name"])
                              for t, n in zip(picked, nodes)])
    return {"title": f"{login}'s inbox", "login": login}, items, len(threads)
