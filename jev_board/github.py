"""
Read-only board fetch. The only `gh` calls are `project view` and `project item-list`.
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
        blocked = re.search(r"\*\*Blocked by:?\*\*:?\s*(.+)", body)
        fields = {k: v for k, v in it.items()
                  if k not in ("content", "id", "title", "labels", "repository", "status") and v}
        ref = ref_of(c)
        items.append({
            "id": it["id"], "ref": ref or "draft",
            "repo": _repo_of(c) if ref else None, "number": c.get("number") if ref else None,
            "url": c.get("url", ""),
            "title": it.get("title") or c.get("title", ""), "type": c.get("type", "DraftIssue"),
            "status": status, "labels": it.get("labels") or [], "fields": fields,
            "body": body, "blocked_by": blocked.group(1).strip()[:200] if blocked else None,
        })
    # who on the whole board (done items included) mentions this item
    texts = [((it.get("content") or {}).get("body") or "", it["id"],
              _repo_of(it.get("content") or {})) for it in raw]
    for item in items:
        if item["ref"] == "draft":
            item["referenced_by"] = 0
            continue
        repo, num = item["repo"], item["number"]
        pat = re.compile(rf"(?:{re.escape(repo)}#{num}\b|/{re.escape(repo)}/(?:issues|pull)/{num}\b)")
        bare = re.compile(rf"(?<![\w/#-])#{num}\b")  # same-repo shorthand
        item["referenced_by"] = sum(1 for t, i, r in texts if i != item["id"]
                                    and (pat.search(t) or (r == repo and bare.search(t))))
    return meta, items, len(raw)
