"""Offline tests: layout engine behaviour, against a captured device state.

Run: python3 -m pytest tests/ -q      (or python3 tests/test_layout.py)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pocketdesk import layout, rules  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "state_sample.json"


def load_state():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_dock_is_index_zero_not_a_page():
    state = load_state()
    dock = layout.dock_of(state)
    assert len(dock) == 4, "dock must be the four items at index 0"
    assert layout.pages_of(state) == state[1:], "pages start at index 1"


def test_collect_icons_skips_dock():
    state = load_state()
    with_dock = layout.collect_icons(state, include_dock=True)
    without = layout.collect_icons(state)
    assert len(without) == len(with_dock) - 4
    dock_ids = {item["displayIdentifier"] for item in layout.dock_of(state)}
    assert not (dock_ids & set(without))


def test_pages_are_packed_to_capacity():
    state = load_state()
    groups, first_page, _ = rules.load("fine")
    capacity = 8  # deliberately small, so the fixture spans several pages
    plan = layout.build_layout(state, groups=groups, first_page=first_page,
                               page_capacity=capacity)
    pages = plan[1:]
    assert all(len(page) <= capacity for page in pages)
    # every page except the last must be completely full
    assert all(len(page) == capacity for page in pages[:-1])


def test_nothing_is_dropped_and_dock_is_not_duplicated():
    state = load_state()
    groups, first_page, _ = rules.load("fine")
    plan = layout.build_layout(state, groups=groups, first_page=first_page,
                               page_capacity=24)
    assert layout.verify(state, plan) == []


def test_unknown_apps_survive_as_loose_icons():
    state = load_state()
    plan = layout.build_layout(state, groups={}, first_page=[], page_capacity=24)
    placed = []
    for page in plan[1:]:
        for item in page:
            if isinstance(item, dict) and item.get("listType") == "folder":
                placed.extend(i["displayIdentifier"] for lst in item["iconLists"] for i in lst)
            elif isinstance(item, dict):
                placed.append(item["displayIdentifier"])
    assert len(placed) == len(layout.collect_icons(state))


def test_folder_uses_iconlists_not_iconlist():
    state = load_state()
    groups, _, _ = rules.load("fine")
    plan = layout.build_layout(state, groups=groups, page_capacity=24)
    folders = [item for page in plan[1:] for item in page
               if isinstance(item, dict) and item.get("listType") == "folder"]
    assert folders, "expected at least one folder"
    for folder in folders:
        assert "displayName" in folder
        assert "iconLists" in folder
        assert "iconList" not in folder
        assert "folderName" not in folder
        assert all(isinstance(page, list) for page in folder["iconLists"])


def test_rule_sets_load():
    names = rules.available()
    assert "fine" in names and "default" in names
    for name in names:
        groups, first_page, display = rules.load(name)
        assert groups, f"{name} has no groups"
        assert isinstance(first_page, list)


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    sys.exit(1 if failures else 0)
