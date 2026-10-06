import asyncio
from types import SimpleNamespace

import pytest

from jev_board import cache, goals, scoring

# ── fake Jev ──

class FakeClient:
    """Answers every question from `answers[ref]` (level per score, label for shipped) and
    records each call's question names, so tests can see what was asked."""

    def __init__(self, answers, calls):
        self.answers, self.calls = answers, calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def system_one(self, state, questions, **kw):
        ref = (state.get("item") or {}).get("ref")
        self.calls.append((ref, sorted(questions)))
        want = self.answers.get(ref, {})
        out = {}
        for name, q in questions.items():
            if q.type == "score":
                lvl = want.get(name, 2)
                out[name] = SimpleNamespace(type="score", score=float(lvl), confidence=0.9,
                                            probabilities={lvl: 1.0})
            else:
                label = want.get(name, "open")
                out[name] = SimpleNamespace(type="choice", probabilities={k: float(k == label)
                                                                         for k in q.criteria})
        return SimpleNamespace(answers=out, model="fake", usage=SimpleNamespace(input_tokens=10))


def factory(answers, calls):
    return lambda: FakeClient(answers, calls)


def board_item(ref, body="", title=None):
    return {"id": f"id-{ref}", "ref": ref, "repo": ref.split("#")[0], "url": "", "title": title or ref,
            "type": "Issue", "status": "Todo", "labels": [], "fields": {}, "body": body,
            "blocked_by": None, "referenced_by": 0}


def ctx(tmp_path, **kw):
    return scoring.Context(owner="o", board=1, title="Board", org="a team",
                           audience=kw.pop("audience", "users"),
                           cache_path=tmp_path / "c.json", **kw)


def run(items, c, answers=None, calls=None, **kw):
    calls = [] if calls is None else calls
    stats = asyncio.run(scoring.assess(items, c, client_factory=factory(answers or {}, calls), **kw))
    return stats, calls


@pytest.fixture(autouse=True)
def fake_key(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")


# ── frameworks (pure formulas) ──

def scored(id_, **norm):
    base = {k: 0.5 for k in scoring.DIMS}
    return {"id": id_, "norm": {**base, **norm}}


def test_low_effort_wins_under_every_framework():
    items = [scored("big", effort=0.9), scored("small", effort=0.1)]
    for fw in scoring.FRAMEWORKS:
        assert scoring.rank(items, fw)[0]["id"] == "small", fw


def test_rice_rewards_clarity_wsjf_ignores_it():
    items = [scored("vague", clarity=0.0), scored("clear", clarity=1.0)]
    assert scoring.rank(items, "rice")[0]["id"] == "clear"
    r = scoring.rank(items, "wsjf")
    assert r[0]["value"] == pytest.approx(r[1]["value"])


def test_custom_weights_flip_the_order():
    items = [scored("urgent", urgency=1.0, reach=0.0), scored("wide", urgency=0.0, reach=1.0)]
    w = dict.fromkeys(scoring.DEFAULT_WEIGHTS, 0)
    assert scoring.rank(items, "custom", {**w, "urgency": 5})[0]["id"] == "urgent"
    assert scoring.rank(items, "custom", {**w, "reach": 5})[0]["id"] == "wide"


def test_rel_and_rank_leave_out_unassessed():
    r = scoring.rank([scored("a", impact=1.0), scored("b", impact=0.0), {"id": "unassessed"}], "ic")
    assert [x["rank"] for x in r] == [1, 2]
    assert r[0]["rel"] == 1.0


def test_quadrants_are_relative_to_the_board():
    items = [scored("a", impact=0.9, effort=0.6), scored("b", impact=0.2, effort=0.9),
             scored("c", impact=0.5, effort=0.75)]
    q = {x["id"]: x["quadrant"] for x in scoring.rank(items, "ic")}
    assert q["a"] == "quick win"
    assert q["b"] == "money pit"


def test_goal_named_item_outranks_its_twin_everywhere():
    items = [scored("a", alignment=0.25), scored("b", alignment=1.0)]
    for fw in scoring.FRAMEWORKS:
        assert scoring.rank(items, fw)[0]["id"] == "b", fw


def test_goal_named_weak_item_reaches_top_third_under_wsjf():
    # 11 ordinary items spread across the board, plus one goal-named item with
    # below-median base scores: the plan's success criterion is top third under WSJF.
    items = []
    for i in range(11):
        v = 0.1 + 0.08 * i
        items.append(scored(f"n{i}", impact=v, urgency=v, unblocks=v, effort=0.5, alignment=0.25))
    items.append(scored("named", impact=0.35, urgency=0.35, unblocks=0.35, effort=0.5, alignment=1.0))
    ranked = scoring.rank(items, "wsjf")
    assert next(x["rank"] for x in ranked if x["id"] == "named") <= 4


def test_shipped_with_commit_sinks_without_commit_only_flags():
    items = [scored("shipped", impact=1.0, effort=0.0), scored("open", impact=0.0, effort=1.0),
             scored("claimed", impact=1.0, effort=0.0)]
    items[0].update(shipped="shipped", evidence={"mentions": [{"sha": "abc"}]})
    items[2].update(shipped="shipped", evidence={"mentions": []})
    for fw in scoring.FRAMEWORKS:
        order = [x["id"] for x in scoring.rank(items, fw)]
        assert order[-1] == "shipped", fw
        assert order.index("claimed") < order.index("open"), fw


# ── clarity facts ──

def test_clarity_separates_specified_items_from_one_liners():
    full = board_item("api#1", body=("Change `parse_goals` in src/goals.py.\n\n"
                                     "**Blocked by:** api#2\n\n## Acceptance\n- [ ] parses headings\n"
                                     "- [ ] maps refs\n" + "Detail. " * 60))
    full["blocked_by"] = "api#2"
    thin = board_item("api#3", body="")
    ev_full = {"files": ["api:src/goals.py"]}
    assert scoring.clarity(full, ev_full) - scoring.clarity(thin, {"files": []}) >= 0.4


def test_each_clarity_fact_counts():
    plain = board_item("api#1", body="Make search faster.")
    base = scoring.clarity(plain, {"files": []})
    with_ac = board_item("api#1", body="Make search faster.\n- [ ] p95 under 200ms")
    blocked = dict(plain, blocked_by="api#2")
    assert scoring.clarity(with_ac, {"files": []}) - base >= 0.3
    assert scoring.clarity(blocked, {"files": []}) > base
    assert scoring.clarity(plain, {"files": ["api:src/search.py"]}) > base


# ── assess: what gets asked, and when ──

def test_second_run_asks_nothing_and_leaves_cache_alone(tmp_path):
    items = [board_item("api#1"), board_item("api#2")]
    c = ctx(tmp_path)
    stats, calls = run(items, c)
    assert stats["asked"] == 2 and len(calls) == 2
    before = c.cache_path.stat().st_mtime_ns
    stats, calls = run([board_item("api#1"), board_item("api#2")], c)
    assert stats["asked"] == 0 and calls == []
    assert c.cache_path.stat().st_mtime_ns == before


def test_changed_item_is_the_only_one_reasked(tmp_path):
    c = ctx(tmp_path)
    run([board_item("api#1"), board_item("api#2")], c)
    _, calls = run([board_item("api#1", body="new text"), board_item("api#2")], c)
    assert [ref for ref, _ in calls] == ["api#1"]


def test_changed_audience_reasks_every_item(tmp_path):
    run([board_item("api#1"), board_item("api#2")], ctx(tmp_path))
    stats, _ = run([board_item("api#1"), board_item("api#2")], ctx(tmp_path, audience="partners"))
    assert stats["asked"] == 2


def test_no_goals_means_no_alignment_question(tmp_path):
    items = [board_item("api#1")]
    _, calls = run(items, ctx(tmp_path))
    assert all("alignment" not in qs for _, qs in calls)
    assert "alignment" not in items[0]["norm"]
    assert "clarity" not in calls[0][1]  # clarity is computed, never asked


def test_goals_edit_asks_alignment_only(tmp_path):
    c = ctx(tmp_path)
    g1 = goals.parse("## Ship search\noutcome: users find things\n")
    run([board_item("api#1"), board_item("api#2")], c, goals=g1)
    base_before = {k: v["base"] for k, v in cache.load(c.cache_path).items()}
    g2 = goals.parse("## Ship search faster\noutcome: users find things\n")
    stats, calls = run([board_item("api#1"), board_item("api#2")], c, goals=g2)
    assert stats["asked"] == 0 and stats["aligned"] == 2
    assert all(qs == ["alignment"] for _, qs in calls)
    assert {k: v["base"] for k, v in cache.load(c.cache_path).items()} == base_before


def test_goal_named_ref_is_aligned_in_code(tmp_path):
    g = goals.parse("## Clear the backlog\nunblocks: board 28, api#2\n")
    items = [board_item("api#1"), board_item("api#2")]
    stats, calls = run(items, ctx(tmp_path), goals=g, answers={"api#1": {"alignment": 1}})
    assert stats["aligned"] == 1
    assert ("api#2", ["alignment"]) not in calls
    by = {it["ref"]: it for it in items}
    assert by["api#2"]["norm"]["alignment"] == 1.0
    assert by["api#2"]["goals"] == ["Clear the backlog"]


def test_shipped_answer_is_recorded(tmp_path):
    items = [board_item("api#1")]
    run(items, ctx(tmp_path), answers={"api#1": {"shipped": "partly"}})
    assert items[0]["shipped"] == "partly"


def test_rank_never_calls_jev(tmp_path):
    items = [board_item("api#1"), board_item("api#2")]
    c = ctx(tmp_path)
    _, calls = run(items, c)
    n = len(calls)
    for fw in scoring.FRAMEWORKS:
        scoring.rank(items, fw)
    assert len(calls) == n


def test_failed_call_leaves_item_unranked_and_keeps_the_rest(tmp_path):
    class Flaky(FakeClient):
        async def system_one(self, state, questions, **kw):
            if state["item"]["ref"] == "api#2":
                raise RuntimeError("rate limited")
            await asyncio.sleep(0.01)  # still in flight when the other call fails
            return await super().system_one(state, questions, **kw)
    c = ctx(tmp_path)
    items = [board_item("api#1"), board_item("api#2"), board_item("api#3")]
    stats = asyncio.run(scoring.assess(items, c, client_factory=lambda: Flaky({}, [])))
    assert stats["failed"] == 1 and "rate limited" in stats["error"]
    assert sorted(e["ref"] for e in cache.load(c.cache_path).values()) == ["api#1", "api#3"]
    assert sorted(it["ref"] for it in scoring.rank(items)) == ["api#1", "api#3"]
