"""
Goals file: each `## ` heading is a goal, with optional `outcome:` and `unblocks:` lines.
Any `repo#n` (or `owner/repo#n`) in a section counts as named by that goal. Text before
the first heading is ignored. See examples/goals.md.
"""
import hashlib
import os
import re
import sys
from dataclasses import dataclass, field

REF = re.compile(r"(?:[A-Za-z0-9_.-]+/)?([A-Za-z0-9_.-]+)#(\d+)")


@dataclass
class Goal:
    title: str
    outcome: str = ""
    unblocks: str = ""
    refs: list[str] = field(default_factory=list)


@dataclass
class Goals:
    goals: list[Goal]
    by_ref: dict[str, list[str]]
    digest: str


def _field(lines, name):
    for line in lines:
        s = line.strip()
        if s.lower().startswith(name + ":"):
            return s[len(name) + 1:].strip()
    return ""


def parse(text):
    goals, by_ref = [], {}
    for section in re.split(r"^## ", text, flags=re.M)[1:]:
        head, *body = section.split("\n")
        refs = sorted({f"{m[1]}#{m[2]}" for m in REF.finditer(section)})
        g = Goal(head.strip(), _field(body, "outcome"), _field(body, "unblocks"), refs)
        goals.append(g)
        for r in refs:
            by_ref.setdefault(r, []).append(g.title)
    return Goals(goals, by_ref, hashlib.sha1(text.encode()).hexdigest())


def load(path):
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as f:
            return parse(f.read())
    except FileNotFoundError:
        sys.exit(f"goals file not found: {path}")
