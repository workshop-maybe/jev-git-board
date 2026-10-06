import difflib

from jev_board import cache


def _entry(ref, base_key, align_key, score):
    return {
        "ref": ref,
        "base": {"key": base_key, "value": 5, "effort": 3},
        "alignment": {"key": align_key, "score": score},
    }


def test_save_then_load_round_trips(tmp_path):
    p = cache.path_for("o", 7, root=tmp_path)
    data = {"I_1": _entry("o/r#1", "b1", "a1", 4)}
    assert cache.save(p, data) is True
    assert cache.load(p) == data


def test_unchanged_save_does_not_write(tmp_path):
    p = cache.path_for("o", 7, root=tmp_path)
    data = {"I_1": _entry("o/r#1", "b1", "a1", 4)}
    cache.save(p, data)
    before = p.stat().st_mtime_ns
    assert cache.save(p, {"I_1": _entry("o/r#1", "b1", "a1", 4)}) is False
    assert p.stat().st_mtime_ns == before


def test_alignment_update_changes_only_that_items_lines(tmp_path):
    p = cache.path_for("o", 7, root=tmp_path)
    data = {f"I_{i}": _entry(f"o/r#{i}", f"b{i}", f"a{i}", i) for i in range(5)}
    cache.save(p, data)
    old = p.read_text().splitlines()
    data["I_2"]["alignment"] = {"key": "a2-new", "score": 9}
    assert cache.save(p, data) is True
    new = p.read_text().splitlines()
    changed = [ln for ln in difflib.unified_diff(old, new, lineterm="", n=0)
               if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]
    assert sorted(changed) == sorted(['-   "key": "a2",', '-   "score": 2', '+   "key": "a2-new",', '+   "score": 9'])


def test_corrupt_file_loads_empty_and_next_save_replaces_it(tmp_path):
    p = cache.path_for("o", 7, root=tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text("{not json")
    assert cache.load(p) == {}
    data = {"I_1": _entry("o/r#1", "b1", "a1", 4)}
    assert cache.save(p, data) is True
    assert cache.load(p) == data


def test_missing_file_loads_empty(tmp_path):
    assert cache.load(tmp_path / "nope.json") == {}


def test_owners_with_same_board_get_separate_files(tmp_path):
    a, b = cache.path_for("alpha", 3, root=tmp_path), cache.path_for("beta", 3, root=tmp_path)
    assert a == tmp_path / "alpha" / "board-3.json"
    assert a != b
    cache.save(a, {"I_1": _entry("alpha/r#1", "b", "a", 1)})
    cache.save(b, {"I_9": _entry("beta/r#9", "b", "a", 1)})
    assert set(cache.load(a)) == {"I_1"} and set(cache.load(b)) == {"I_9"}


def test_default_root_is_cache_root(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert cache.path_for("o", 1) == tmp_path / "jev-board" / "o" / "board-1.json"
