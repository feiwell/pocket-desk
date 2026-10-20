"""Rule files: which app goes into which folder.

A rule file is plain JSON so you can edit it without touching Python:

    {
      "name": "fine",
      "groups": {
        "Social": ["com.tencent.xin", "com.apple.MobileSMS"],
        "Media":  ["com.apple.Music"]
      },
      "first_page": []
    }

Apps that match no group stay as loose icons on the last page, so a partial
rule set is safe — nothing gets dropped.
"""

from __future__ import annotations

import json
import os
from collections import OrderedDict
from pathlib import Path

BUNDLED_DIR = Path(__file__).resolve().parent / "presets"
CONFIG_DIR = Path(os.environ.get("POCKETDESK_CONFIG",
                                 Path.home() / ".config" / "pocketdesk")).expanduser()


def available() -> list[str]:
    """Rule sets shipped with the package, plus any in ~/.config/pocketdesk/rules."""
    names: list[str] = []
    for directory in (BUNDLED_DIR, CONFIG_DIR / "rules"):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            if path.stem not in names:
                names.append(path.stem)
    return names


def resolve(name_or_path: str) -> Path:
    """Accept a rule-set name (`fine`) or an explicit path."""
    candidate = Path(name_or_path).expanduser()
    if candidate.is_file():
        return candidate
    for directory in (CONFIG_DIR / "rules", BUNDLED_DIR):
        path = directory / f"{name_or_path}.json"
        if path.is_file():
            return path
    raise FileNotFoundError(
        f"unknown rule set '{name_or_path}'. Available: {', '.join(available()) or '(none)'}"
    )


def load(name_or_path: str) -> tuple["OrderedDict[str, list[str]]", list[str], str]:
    """Returns (groups, first_page, display_name)."""
    path = resolve(name_or_path)
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)

    groups = OrderedDict()
    for folder, bundle_ids in (data.get("groups") or {}).items():
        if isinstance(bundle_ids, list):
            groups[folder] = [str(x) for x in bundle_ids]

    first_page = [str(x) for x in (data.get("first_page") or [])]
    return groups, first_page, data.get("name") or path.stem


def write_template(path: str | Path) -> Path:
    """Write a starter rule file the user can edit."""
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    template = {
        "name": target.stem,
        "description": "Edit freely. Unknown bundle ids are ignored; unmatched apps stay outside folders.",
        "groups": {
            "Social": ["com.tencent.xin", "com.apple.MobileSMS", "com.apple.mobilephone"],
            "Tools": ["com.apple.Preferences", "com.apple.mobiletimer"],
        },
        "first_page": [],
    }
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(template, handle, ensure_ascii=False, indent=1)
    return target
