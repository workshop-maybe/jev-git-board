"""
Per-owner, per-board (or per-user inbox) answer cache, kept outside any repo and written stably.

A cache file maps item id to an entry:

    {"ref": "repo#12",
     "base": {"key": "<hash of item text + evidence>", ...base answers},
     "alignment": {"key": "<hash of base key + goals>", ...alignment answers}}

`base` and `alignment` are independent sub-records, so refreshing one leaves the
other's serialized lines untouched. Keys are sorted and the indent is fixed, so an
unchanged entry is byte-identical across saves, and a save with nothing new skips the write.
"""
import json
import os
import tempfile
from pathlib import Path

from jev_board import config


def path_for(owner, board, root=None):
    return Path(root or config.cache_root()) / owner / f"board-{board}.json"


def inbox_path(login, root=None):
    return Path(root or config.cache_root()) / "@inbox" / f"{login}.json"  # "@" is never in a GitHub name


def load(path):
    try:
        data = json.loads(Path(path).read_text())
    except (FileNotFoundError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _dump(data):
    return json.dumps(data, sort_keys=True, indent=1) + "\n"


def save(path, data):
    """Write `data` to `path` atomically. Returns False (and touches nothing) when the
    file already holds the same text."""
    path, text = Path(path), _dump(data)
    try:
        if path.read_text() == text:
            return False
    except (FileNotFoundError, ValueError):
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise
    return True
