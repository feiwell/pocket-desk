"""Home-screen layout engine.

Takes the state returned by SpringBoard, groups apps into folders according to
a rule set, and produces a new state that is packed into as few pages as the
device's real grid allows.
"""

from __future__ import annotations

from collections import Counter, OrderedDict
from copy import deepcopy
from typing import Any


def dock_of(state: list[Any]) -> list[dict[str, Any]]:
    """Return the dock (index 0 of the state array).

    The top-level array is `[dock, page1, page2, ...]`. Treating index 0 as a
    page and dropping it blanks the dock on write.
    """
    if state and isinstance(state[0], list):
        return deepcopy(state[0])
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
            if bundle_id and node.get("listType", "icon") == "icon":
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
    _inspect(state)  # refuse unknown objects or duplicates before collecting/deduplicating
    if page_capacity <= 0 or min_folder_size <= 0:
        raise ValueError("capacities and minimum folder size must be positive")
    groups = groups or {}
    first_page = first_page or []
    pool = collect_icons(state)
    used: set[str] = set()

    pinned: list[dict[str, Any]] = []
    for bundle_id in first_page:
        if bundle_id in pool and bundle_id not in used:
            pinned.append(deepcopy(pool[bundle_id]))
            used.add(bundle_id)

    folders: list[dict[str, Any]] = []
    for name, bundle_ids in groups.items():
        members = [
            deepcopy(pool[bundle_id])
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

    rest = [deepcopy(icon) for bundle_id, icon in pool.items() if bundle_id not in used]

    # Everything on the home screen, packed into full pages. iOS renders the page
    # boundaries exactly as given and will not backfill gaps, so pack them here.
    items: list[Any] = list(pinned) + folders + rest
    pages = [items[i:i + page_capacity] for i in range(0, len(items), page_capacity)] or [[]]

    return [dock_of(state)] + pages


def describe(layout: list[Any]) -> str:
    lines: list[str] = []
    dock = layout[0] if layout and isinstance(layout[0], list) else []
    lines.append("Dock: " + ("、".join(x.get("displayName", "?") for x in _slots(dock)
                                    if isinstance(x, dict)) or "(empty)"))
    for index, page in enumerate(layout[1:], 1):
        if not isinstance(page, list):
            continue
        page = _slots(page)
        folders = [x for x in page if isinstance(x, dict) and x.get("listType") == "folder"]
        icons = [x for x in page if isinstance(x, dict) and x.get("listType") != "folder"]
        lines.append(f"── page {index}: {len(icons)} icons / {len(folders)} folders")
        if icons:
            lines.append("   " + "、".join(x.get("displayName", "?") for x in icons))
        for folder in folders:
            members = [x.get("displayName", "?") for lst in folder.get("iconLists", [])
                       for x in lst if isinstance(x, dict)]
            lines.append(f"   📁 {folder['displayName']} ({len(members)}): " + "、".join(members))
    return "\n".join(lines)


def _slots(page: Any) -> list[Any]:
    """Normalize row wrappers only; never discard holes or page boundaries."""
    if not isinstance(page, list):
        raise ValueError("each dock/page must be a list")
    if page and all(isinstance(row, list) for row in page):
        return [item for row in page for item in row]
    return page


def _icon_id(icon: Any) -> str:
    if (not isinstance(icon, dict)
            or icon.get("listType", "icon") != "icon"
            or not isinstance(icon.get("displayIdentifier"), str)
            or not icon["displayIdentifier"]
            or any(key in icon for key in ("iconLists", "iconList", "widgetKind"))):
        raise ValueError("unsupported icon/widget: expected an app displayIdentifier")
    return icon["displayIdentifier"]


def _inspect(state: list[Any]) -> tuple[list[str], list[Any]]:
    """Validate supported state and return IDs plus positional layout signature.

    Signatures ignore app metadata but retain holes, order, folder membership,
    internal folder pages and home-screen page boundaries. Dock is not a page.
    """
    if not isinstance(state, list) or not state:
        raise ValueError("state must contain a dock at index 0")
    ids: list[str] = []
    signature: list[Any] = []
    for index, page in enumerate(state):
        items: list[Any] = []
        for item in _slots(page):
            if item is False:
                items.append(False)
            elif isinstance(item, dict) and item.get("listType") == "folder":
                if index == 0:
                    raise ValueError("folders in Dock are unsupported")
                name, lists = item.get("displayName"), item.get("iconLists")
                if (not isinstance(name, str) or not name
                        or not isinstance(lists, list) or "iconList" in item
                        or "displayIdentifier" in item):
                    raise ValueError("unsupported folder structure")
                members = []
                for folder_page in lists:
                    if not isinstance(folder_page, list):
                        raise ValueError("folder iconLists must contain lists")
                    member_ids = []
                    for icon in folder_page:
                        if icon is False:
                            member_ids.append(False)
                        else:
                            bundle_id = _icon_id(icon)
                            ids.append(bundle_id)
                            member_ids.append(bundle_id)
                    members.append(member_ids)
                items.append(("folder", name, members))
            else:
                bundle_id = _icon_id(item)
                ids.append(bundle_id)
                items.append(("icon", bundle_id))
        signature.append(items)
    duplicates = [key for key, count in Counter(ids).items() if count > 1]
    if duplicates:
        raise ValueError(f"duplicate displayIdentifier(s): {sorted(duplicates)}")
    return ids, signature


def verify(before: list[Any], after: list[Any], expected: list[Any] | None = None) -> list[str]:
    """Check conservation and unchanged Dock; optionally compare actual to plan.

    Conservation alone cannot detect a wrong folder or a changed page boundary.
    Pass expected=plan when verifying a device read-back.
    """
    problems: list[str] = []
    try:
        original_ids, original_signature = _inspect(before)
        placed_ids, actual_signature = _inspect(after)
        # Planning preserves the entire raw Dock. Read-back compares positions,
        # allowing row wrappers and non-layout metadata refreshed by iOS.
        if ((expected is None and before[0] != after[0])
                or original_signature[0] != actual_signature[0]):
            problems.append("Dock changed (order or contents; raw fields must also match in plans)")
        missing = set(original_ids) - set(placed_ids)
        extra = set(placed_ids) - set(original_ids)
        if missing:
            problems.append(f"app(s) not placed: {sorted(missing)}")
        if extra:
            problems.append(f"extra app(s): {sorted(extra)}")
        if expected is not None:
            _, planned_signature = _inspect(expected)
            if actual_signature != planned_signature:
                problems.append("actual layout differs from plan (order, folders or page boundaries)")
    except ValueError as exc:
        problems.append(str(exc))
    return problems


def merge_layout(state: list[Any], assignments: dict[str, str]) -> list[Any]:
    """Move ALL loose icons on trailing pages into uniquely named existing folders.

    Pure and fail-closed: no creation/reordering/repacking, no metadata stripping
    and no serialization. Existing members stay in their original folder pages;
    additions are appended to the last internal page in source traversal order.
    Empty trailing home-screen pages are removed, not earlier page boundaries.
    """
    _inspect(state)
    if not isinstance(assignments, dict) or any(
        not isinstance(key, str) or not key or not isinstance(value, str) or not value
        for key, value in assignments.items()
    ):
        raise ValueError("assignments must map displayIdentifier to existing folder name")
    plan = deepcopy(state)
    folders: dict[str, dict[str, Any]] = {}
    last_folder_page = 0
    for index, page in enumerate(plan[1:], 1):
        for item in _slots(page):
            if isinstance(item, dict) and item.get("listType") == "folder":
                name = item["displayName"]
                if name in folders:
                    raise ValueError(f"ambiguous duplicate folder name: {name}")
                folders[name] = item
                last_folder_page = index
    if not folders:
        raise ValueError("merge requires existing folders")
    loose: list[dict[str, Any]] = []
    for index, page in enumerate(plan[1:], 1):
        for item in _slots(page):
            if item is False or item.get("listType") == "folder":
                continue
            if index <= last_folder_page:
                raise ValueError("loose icons must be on trailing pages after all folders")
            loose.append(item)
    loose_ids = {icon["displayIdentifier"] for icon in loose}
    if set(assignments) != loose_ids:
        missing = sorted(loose_ids - set(assignments))
        unknown = sorted(set(assignments) - loose_ids)
        raise ValueError(f"assignments must cover all trailing icons exactly; missing={missing}, unknown={unknown}")
    unknown_targets = set(assignments.values()) - set(folders)
    if unknown_targets:
        raise ValueError(f"unknown target folder(s): {sorted(unknown_targets)}")
    for icon in loose:
        target = folders[assignments[icon["displayIdentifier"]]]["iconLists"]
        if not target:
            target.append([])
        target[-1].append(icon)
    plan = plan[:last_folder_page + 1]
    problems = verify(state, plan)
    if problems:
        raise ValueError("; ".join(problems))
    return plan
