"""Home-screen layout engine.

Takes the state returned by SpringBoard, groups apps into folders according to
a rule set, and produces a new state that is packed into as few pages as the
device's real grid allows.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from .springboard import strip_icon


def dock_of(state: list[Any]) -> list[dict[str, Any]]:
    """Return the dock (index 0 of the state array).

    The top-level array is `[dock, page1, page2, ...]`. Treating index 0 as a
    page and dropping it blanks the dock on write.
    """
    if state and isinstance(state[0], list):
        return [strip_icon(item) for item in state[0] if isinstance(item, dict)]
    return []


def pages_of(state: list[Any]) -> list[list[Any]]:
    """All real pages, i.e. everything after the dock."""
    return [page for page in state[1:] if isinstance(page, list)]


def unwrap_pages(state: list[Any], include_dock: bool = False) -> list[list[Any]]:
    """Normalise to `page -> [icon | folder]`.

    Devices differ: some return `page -> [row, row, ...]` where each row is a
    list, others return `page -> [icon, ...]` flat. Both normalise here.
    """
    source = state if include_dock else state[1:]
    pages: list[list[Any]] = []
    for page in source:
        if not isinstance(page, list):
            continue
        if page and all(isinstance(row, list) for row in page):
            pages.append([item for row in page for item in row])
        else:
            pages.append(page)
    return pages


def collect_icons(state: list[Any], include_dock: bool = False) -> "OrderedDict[str, dict[str, Any]]":
    """Flatten the state into `{bundle_id: icon}`.

    Dock entries are skipped by default. Including them makes docks apps show up
    twice: once in the dock, once inside a folder.
    """
    pool: "OrderedDict[str, dict[str, Any]]" = OrderedDict()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            bundle_id = node.get("displayIdentifier")
            if bundle_id and "displayName" in node:
                pool.setdefault(bundle_id, node)
                return
            for key in ("iconLists", "iconList"):
                for sub in node.get(key) or []:
                    walk(sub)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(state if include_dock else state[1:])
    return pool


def build_layout(
    state: list[Any],
    groups: "OrderedDict[str, list[str]]" | dict[str, list[str]] | None = None,
    first_page: list[str] | None = None,
    page_capacity: int = 24,
    min_folder_size: int = 2,
) -> list[Any]:
    """Build a packed layout: dock preserved, pages filled to capacity.

    page_capacity is the real number of items per page (4x6 = 24 on a modern
    iPhone, but read it from `get_homescreen_icon_metrics()` rather than assuming).
    """
    groups = groups or {}
    first_page = first_page or []
    pool = collect_icons(state)
    used: set[str] = set()

    pinned: list[dict[str, Any]] = []
    for bundle_id in first_page:
        if bundle_id in pool and bundle_id not in used:
            pinned.append(strip_icon(pool[bundle_id]))
            used.add(bundle_id)

    folders: list[dict[str, Any]] = []
    for name, bundle_ids in groups.items():
        members = [
            strip_icon(pool[bundle_id])
            for bundle_id in bundle_ids
            if bundle_id in pool and bundle_id not in used
        ]
        used.update(bundle_id for bundle_id in bundle_ids if bundle_id in pool)

        if len(members) >= min_folder_size:
            folders.append({
                "listType": "folder",
                "displayName": name,
                "iconLists": [members],
            })
        else:
            pinned.extend(members)

    rest = [strip_icon(icon) for bundle_id, icon in pool.items() if bundle_id not in used]

    # Everything on the home screen, packed into full pages. iOS renders the page
    # boundaries exactly as given and will not backfill gaps, so pack them here.
    items: list[Any] = list(pinned) + folders + rest
    pages = [items[i:i + page_capacity] for i in range(0, len(items), page_capacity)] or [[]]

    return [dock_of(state)] + pages


def describe(layout: list[Any]) -> str:
    lines: list[str] = []
    dock = layout[0] if layout and isinstance(layout[0], list) else []
    lines.append("Dock: " + ("、".join(x.get("displayName", "?") for x in dock) or "(empty)"))
    for index, page in enumerate(layout[1:], 1):
        if not isinstance(page, list):
            continue
        folders = [x for x in page if isinstance(x, dict) and x.get("listType") == "folder"]
        icons = [x for x in page if isinstance(x, dict) and x.get("listType") != "folder"]
        lines.append(f"── page {index}: {len(icons)} icons / {len(folders)} folders")
        if icons:
            lines.append("   " + "、".join(x.get("displayName", "?") for x in icons))
        for folder in folders:
            members = [x.get("displayName", "?") for lst in folder.get("iconLists", []) for x in lst]
            lines.append(f"   📁 {folder['displayName']} ({len(members)}): " + "、".join(members))
    return "\n".join(lines)


def verify(before: list[Any], after: list[Any]) -> list[str]:
    """Sanity-check a plan against the current state. Returns a list of problems."""
    problems: list[str] = []
    pool = collect_icons(before)
    dock_ids = {x["displayIdentifier"] for x in dock_of(before)}

    placed: list[str] = []
    for page in after[1:]:
        if not isinstance(page, list):
            continue
        for item in page:
            if not isinstance(item, dict):
                continue
            if item.get("listType") == "folder":
                placed.extend(
                    icon["displayIdentifier"] for lst in item.get("iconLists", []) for icon in lst
                )
            else:
                placed.append(item["displayIdentifier"])

    missing = set(pool) - set(placed)
    if missing:
        problems.append(f"{len(missing)} app(s) not placed: {sorted(missing)[:5]}")
    if set(placed) & dock_ids:
        problems.append(f"dock app(s) duplicated in folders: {sorted(set(placed) & dock_ids)}")
    if len(placed) != len(set(placed)):
        duplicates = len(placed) - len(set(placed))
        problems.append(f"{duplicates} duplicate placement(s) on the home screen")
    return problems
