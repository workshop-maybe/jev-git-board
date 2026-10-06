"""
Score board items once with Jev, then rank them under any framework for free.

Jev answers five of the six dimensions (reach, impact, urgency, effort, unblocks) and
whether the work already shipped, in one call per item. Clarity is the sixth dimension;
it is computed here from facts, never asked. With a goals file, a seventh score,
alignment, is asked in its own small call, so editing goals never re-asks the rest.

Facts stay in code: status, labels, fields, the `Blocked by:` line, how many other items
reference this one, and the codebase evidence from the retrieval pass.
"""
import asyncio
import dataclasses
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from jev_board import cache, retrieval, typesafe
from jev_board.typesafe import Choice, Score

SCHEMA_VERSION = "1"  # bump when the state or cache entry changes shape
MAX_BODY = 6000  # the head of an issue carries its intent
DIMS = ("reach", "impact", "urgency", "effort", "clarity", "unblocks")
ASKED = ("reach", "impact", "urgency", "effort", "unblocks")
MAX_LEVEL = 4  # every Score below has five levels, 0-4
SHORT = {"reach": "R", "impact": "I", "urgency": "U", "effort": "E", "clarity": "C",
         "unblocks": "B", "alignment": "G"}


def base_questions(audience):
    """The one call per item. Levels are concrete situations, lowest first."""
    return {
        "reach": Score(
            instructions=f"How many people does the work in `item` affect once it is done? Count {audience}.",
            criteria=["Nobody outside the person doing it; internal tidy-up",
                      "A handful of people, or one internal workflow",
                      "A whole team, or one external partner or client",
                      "Many users of one product, or several partners",
                      "Every user of the product, or the public at large"]),
        "impact": Score(
            instructions="For the people it reaches, how much does finishing the work in `item` change "
                         "their outcome?",
            criteria=["Cosmetic; nobody would notice it missing",
                      "A small convenience or polish",
                      "A clear improvement to a workflow people use",
                      "Unlocks something people could not do before, or fixes something broken",
                      "Decisive: a launch, a revenue commitment, or a production failure depends on it"]),
        "urgency": Score(
            instructions="How time-critical is the work in `item` today? Look for deadlines, outages, "
                         "external commitments and launch dates.",
            criteria=["No time pressure; could wait months",
                      "Wanted this quarter, no fixed date",
                      "Tied to an upcoming milestone or release",
                      "Has a near, named deadline or an external party waiting",
                      "Something is broken or blocked in production right now"]),
        "effort": Score(
            instructions="How much work does `item` take to finish? Judge by its scope and by `codebase`: "
                         "the files and repos it names that exist today.",
            criteria=["Minutes to an hour; a config or copy change",
                      "About a day; one small, contained change",
                      "A few days; one repo, some design needed",
                      "One to two weeks; several repos or a contract change",
                      "Weeks or more; a programme of work with many parts"]),
        "unblocks": Score(
            instructions="How much other work is waiting for `item` to finish? Use `item.referenced_by_count` "
                         "and what the text says depends on it.",
            criteria=["Nothing waits on it",
                      "One small follow-up waits on it",
                      "One other issue or a teammate is blocked by it",
                      "Several issues, or a release, wait on it",
                      "A whole initiative or launch is gated on it"]),
        "shipped": Choice(
            instructions="Is the work in `item` already done, even though the item is still open? "
                         "`codebase.mentions` lists commits whose messages reference it.",
            criteria={"open": "Not done; the work is still ahead",
                      "partly": "Some of it has landed, the rest is still open",
                      "shipped": "The commits show the work is done; the item was left open"}),
    }


ALIGNMENT = Score(
    instructions="How directly does finishing `item` advance one of `goals`? A goal's `unblocks` line "
                 "says what it is waiting on.",
    criteria=["Unrelated to every goal",
              "Loosely related; helps a goal only indirectly",
              "Moves one goal forward",
              "A clear step a goal depends on",
              "The step a goal names as its blocker"])


# ── Frameworks: pure functions of the normalised (0-1) dimensions. ──

def _rice(d, w):
    return (0.1 + d["reach"]) * (0.1 + d["impact"]) * (0.25 + 0.75 * d["clarity"]) / (0.2 + d["effort"])


def _ic(d, w):
    return (0.1 + d["impact"]) / (0.2 + d["effort"])


def _wsjf(d, w):
    return (d["impact"] + d["urgency"] + d["unblocks"]) / (0.2 + d["effort"])


def _custom(d, w):
    vals = {**d, "effort": 1 - d["effort"]}  # low effort is good
    keys = [k for k in w if k in vals]
    total = sum(w[k] for k in keys) or 1
    return sum(w[k] * vals[k] for k in keys) / total


FRAMEWORKS = {
    "wsjf": ("WSJF", "Cost of delay (impact + urgency + unblocks) ÷ Effort", _wsjf),
    "rice": ("RICE", "Reach × Impact × Confidence (clarity) ÷ Effort", _rice),
    "ic": ("Impact / Complexity", "Impact ÷ Effort, with quadrants", _ic),
    "custom": ("Custom weights", "Weighted sum; effort counts as ease", _custom),
}
DEFAULT_WEIGHTS = {"reach": 2, "impact": 3, "urgency": 2, "effort": 1, "clarity": 1, "unblocks": 2,
                   "alignment": 3}


def goal_factor(d):
    """Scales a formula's value by goal alignment; 1 when there are no goals."""
    return 1 + 2 * d["alignment"] if "alignment" in d else 1


def sinks(it):
    """Shipped per Jev and backed by a commit that names it."""
    return it.get("shipped") == "shipped" and bool((it.get("evidence") or {}).get("mentions"))


def medians(items):
    """The board's own midpoints, so quadrants are relative to this board, not absolute."""
    out = {}
    for k in ("impact", "effort"):
        vals = sorted(it["norm"][k] for it in items if it.get("norm"))
        out[k] = vals[len(vals) // 2] if vals else 0.5
    return out


def quadrant(d, mid=None):
    mid = mid or {"impact": 0.5, "effort": 0.5}
    hi_i, hi_e = d["impact"] > mid["impact"], d["effort"] > mid["effort"]
    return {(True, False): "quick win", (True, True): "big bet",
            (False, False): "fill-in", (False, True): "money pit"}[(hi_i, hi_e)]


def rank(items, framework="wsjf", weights=None):
    """Score every assessed item under one framework. Returns items sorted (shipped work
    last), each with `value` (raw) and `rel` (0-1 relative to the top item)."""
    fn = FRAMEWORKS[framework][2]
    w = weights or DEFAULT_WEIGHTS
    scored = []
    for it in items:
        if not it.get("norm"):
            continue
        v = fn(it["norm"], w)
        scored.append(dict(it, value=v if framework == "custom" else v * goal_factor(it["norm"])))
    scored.sort(key=lambda it: (sinks(it), -it["value"]))
    top = max((it["value"] for it in scored), default=1)
    mid = medians(scored)
    for i, it in enumerate(scored):
        it["quadrant"] = quadrant(it["norm"], mid)
        it["rel"] = it["value"] / top if top else 0
        it["rank"] = i + 1
    return scored


# ── Clarity, from facts. ──

ACCEPTANCE = re.compile(r"^\s*[-*] \[[ xX]\]|^#+\s*(acceptance|done when|definition of done)"
                        r"|acceptance criteria|\bdone when\b", re.I | re.M)


def clarity(item, ev):
    """0-1: acceptance criteria present, a `Blocked by:` line, references that resolve in
    the codebases, and enough body to act on."""
    body = item.get("body") or ""
    return round(0.4 * bool(ACCEPTANCE.search(body))
                 + 0.15 * bool(item.get("blocked_by"))
                 + 0.25 * bool(ev.get("files"))
                 + 0.2 * min(1, len(body) / 800), 3)


# ── Ask Jev. ──

@dataclass
class Context:
    owner: str
    board: int
    title: str
    org: str
    audience: str
    cache_path: Path
    env_file: str | None = None


def state_for(item, ctx, ev):
    body = item["body"]
    stable = retrieval.stable(ev) if ev else {}
    return {
        "board": {"title": ctx.title, "organization": ctx.org},
        "item": {"ref": item["ref"], "title": item["title"], "type": item["type"],
                 "status": item["status"], "labels": item["labels"], "fields": item["fields"],
                 "blocked_by_line": item["blocked_by"], "referenced_by_count": item["referenced_by"],
                 "body": body[:MAX_BODY], "body_truncated": len(body) > MAX_BODY},
        "codebase": {"repos": stable.get("repos", []), "files": stable.get("files", []),
                     "mentions": ev.get("mentions", [])},
    }


def _hash(*parts):
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _dump_q(qs):
    return {k: q.model_dump() for k, q in qs.items()}


def _answers(r):
    out = {}
    for name, a in r.answers.items():
        if a.type == "score":
            out[name] = {"score": a.score, "confidence": a.confidence,
                         "probabilities": {str(k): p for k, p in a.probabilities.items()}}
        else:
            out[name] = {"label": max(a.probabilities.items(), key=lambda kv: kv[1])[0],
                         "probabilities": dict(a.probabilities)}
    return out


async def _ask(client, sem, state, questions):
    async with sem:
        r = await client.system_one(state=state, questions=questions)
    return {"answers": _answers(r), "model": r.model, "in_tokens": r.usage.input_tokens}


async def assess(items, ctx, repos=(), goals=None, refresh=False, client_factory=None, on_progress=None):
    """Attach `evidence`, `jev`, `shipped`, `goals` and `norm` (0-1 per dimension) to each item.
    Asks Jev only for what changed since the cached answers. An item whose call fails is
    left unranked and counted in `failed`; the rest still rank. Returns counts and tokens."""
    store = cache.load(ctx.cache_path)
    qs = base_questions(ctx.audience)
    base_sig = (SCHEMA_VERSION, _dump_q(qs))
    align_sig = (SCHEMA_VERSION, ALIGNMENT.model_dump(), goals.digest if goals else None)
    goal_state = [dataclasses.asdict(g) for g in goals.goals] if goals else []
    base_todo, align_todo = [], []
    # git grep per item; run off the event loop so the TUI stays responsive
    evs = await asyncio.gather(*(asyncio.to_thread(retrieval.evidence, it, repos) for it in items)) \
        if repos else [{} for _ in items]
    for it, ev in zip(items, evs):
        it["evidence"] = ev
        it["clarity"] = clarity(it, ev)
        state = state_for(it, ctx, ev)
        it["_key"] = _hash(base_sig, state)
        entry = store.get(it["id"]) or {}
        hit = entry.get("base")
        if hit and hit["key"] == it["_key"] and not refresh:
            it["jev"] = hit
        else:
            base_todo.append((it, state))
        it["goals"] = goals.by_ref.get(it["ref"], []) if goals else []
        if goals and goals.goals and not it["goals"]:
            akey = _hash(align_sig, it["_key"])
            hit = entry.get("alignment")
            if hit and hit["key"] == akey and not refresh:
                it["align"] = hit
            else:
                align_todo.append((it, akey, {"goals": goal_state, "item": state["item"]}))
    tokens = 0
    if base_todo or align_todo:
        typesafe.load_key(ctx.env_file)
        sem = asyncio.Semaphore(typesafe.PARALLEL)
        factory = client_factory or typesafe.AsyncTypeSafeClient
        async with factory() as client:
            async def base(it, state):
                it["jev"] = {"key": it["_key"], **await _ask(client, sem, state, qs)}
                store.setdefault(it["id"], {})["base"] = it["jev"]
                if on_progress:
                    on_progress(it)

            async def align(it, akey, state):
                it["align"] = {"key": akey, **await _ask(client, sem, state, {"alignment": ALIGNMENT})}
                store.setdefault(it["id"], {})["alignment"] = it["align"]

            results = await asyncio.gather(*(base(it, s) for it, s in base_todo),
                                           *(align(it, k, s) for it, k, s in align_todo),
                                           return_exceptions=True)
        for it in items:
            if it["id"] in store:
                store[it["id"]]["ref"] = it["ref"]
        cache.save(ctx.cache_path, store)  # every answer that landed, even when some calls failed
        errors = [r for r in results if isinstance(r, BaseException)]
        tokens = sum(it["jev"]["in_tokens"] for it, _ in base_todo if "jev" in it) + \
            sum(it["align"]["in_tokens"] for it, _, _ in align_todo if "align" in it)
    else:
        errors = []
    failed = 0
    for it in items:
        if "jev" not in it or (goals and goals.goals and not it["goals"] and "align" not in it):
            failed += 1  # left without `norm`, so rank() leaves it out
            continue
        a = it["jev"]["answers"]
        it["shipped"] = a["shipped"]["label"]
        it["norm"] = {k: it["clarity"] if k == "clarity" else a[k]["score"] / MAX_LEVEL for k in DIMS}
        if goals and goals.goals:
            aligned = 1.0 if it["goals"] else it["align"]["answers"]["alignment"]["score"] / MAX_LEVEL
            it["norm"]["alignment"] = aligned
    return {"asked": len(base_todo), "aligned": len(align_todo), "tokens": tokens,
            "failed": failed, "error": str(errors[0]) if errors else None}


# ── Duel: which of two items goes first. Asked in both option orders. ──

DUEL_Q = ("Both `a` and `b` are open on the same team board, and the team can start only one "
          "this week. Which should they start first?")


async def duel(a, b, ctx, client_factory=None):
    typesafe.load_key(ctx.env_file)

    def card(it):
        return {"ref": it["ref"], "title": it["title"], "status": it["status"], "labels": it["labels"],
                "blocked_by_line": it["blocked_by"], "body": it["body"][:MAX_BODY // 2]}
    state = {"board": ctx.title, "a": card(a), "b": card(b)}
    async with (client_factory or typesafe.AsyncTypeSafeClient)() as client:
        r = await client.system_one(state=state, questions={
            "ab": Choice(instructions=DUEL_Q, criteria={"a": "Start `a` first", "b": "Start `b` first"}),
            "ba": Choice(instructions=DUEL_Q, criteria={"b": "Start `b` first", "a": "Start `a` first"}),
        })
    pa = (r.choices["ab"].probabilities["a"] + r.choices["ba"].probabilities["a"]) / 2
    flip = abs(r.choices["ab"].probabilities["a"] - r.choices["ba"].probabilities["a"])
    return {"p_a": pa, "order_spread": flip, "in_tokens": r.usage.input_tokens}
