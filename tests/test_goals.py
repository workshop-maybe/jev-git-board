from pathlib import Path

import pytest

from jev_board import goals

TWO = """\
Preamble mentions api#999, which is ignored.

## Faster sign-up
outcome: Users finish sign-up in a minute.
unblocks: Onboarding campaign, waiting on api#42.

- web#17 trims the form.

## Reliable exports
outcome: Exports never need a retry.
unblocks: Reporting dashboards.
See some-org/api#88.
"""


def test_two_goals_parse_with_fields():
    g = goals.parse(TWO)
    assert [x.title for x in g.goals] == ["Faster sign-up", "Reliable exports"]
    assert g.goals[0].outcome == "Users finish sign-up in a minute."
    assert g.goals[0].unblocks == "Onboarding campaign, waiting on api#42."
    assert g.goals[1].outcome == "Exports never need a retry."
    assert g.goals[1].unblocks == "Reporting dashboards."


def test_unblocks_ref_and_bullet_ref_map_to_goal():
    g = goals.parse(TWO)
    assert g.goals[0].refs == ["api#42", "web#17"]
    assert g.by_ref["api#42"] == ["Faster sign-up"]
    assert g.by_ref["web#17"] == ["Faster sign-up"]


def test_owner_prefix_normalizes_and_preamble_ignored():
    g = goals.parse(TWO)
    assert g.by_ref["api#88"] == ["Reliable exports"]
    assert "api#999" not in g.by_ref


def test_missing_fields_are_empty():
    g = goals.parse("## Bare goal\nweb#1\n")
    assert g.goals[0].outcome == "" and g.goals[0].unblocks == ""
    assert g.by_ref == {"web#1": ["Bare goal"]}


def test_no_headings_is_zero_goals():
    g = goals.parse("just notes about api#1\n")
    assert g.goals == [] and g.by_ref == {}


def test_hash_tracks_raw_text():
    assert goals.parse(TWO).digest == goals.parse(TWO).digest
    assert goals.parse(TWO).digest != goals.parse(TWO + " ").digest


def test_load_missing_file_exits(tmp_path):
    with pytest.raises(SystemExit) as e:
        goals.load(str(tmp_path / "nope.md"))
    assert "nope.md" in str(e.value)


def test_example_file_parses():
    g = goals.load(Path(__file__).parent.parent / "examples" / "goals.md")
    assert len(g.goals) == 2
    assert all(x.outcome and x.unblocks for x in g.goals)
    assert g.by_ref["api#42"] == ["Faster sign-up"]
    assert g.by_ref["api#88"] == ["Reliable nightly exports"]
