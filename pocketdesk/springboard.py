"""SpringBoard home-screen layout read/write.

Wire format notes for iOS 26/27 (`formatVersion: "2"`):

    state  = [dock, page1, page2, ...]     <- index 0 is the DOCK, not a page
    page   = [icon | folder | False, ...]  (False is an empty slot)
    folder = {"listType": "folder",
              "displayName": "Name",
              "iconLists": [[icon, icon, ...]]}   <- array of arrays

Two rules that silently corrupt layouts when violated:
  * `getIconState` must carry `formatVersion: "2"`, or folders come back as `False`.
  * Folders need `displayName` + `iconLists`. `folderName`/`iconList` are ignored
    and the folder is flattened into loose icons with no error.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
import json
import os
import time
from typing import Any

from .tunnel import Tunnel

SPRINGBOARD = "com.apple.springboardservices.shim.remote"
BACKUP_DIR = os.environ.get(
    "POCKETDESK_BACKUP_DIR",
    os.path.join(os.path.expanduser("~"), ".cache", "pocketdesk", "backup"),
)

ICON_KEYS = ("displayIdentifier", "displayName", "iconModDate",
             "bundleVersion", "bundleIdentifier")


@asynccontextmanager
async def service(tunnel: Tunnel):
    """Own one connected official service, including cleanup on connect failure."""
    from pymobiledevice3.services.springboard import SpringBoardServicesService
    svc = SpringBoardServicesService(tunnel.rsd)
    try:
        await svc.connect()
        yield svc
    finally:
        await svc.close()


async def read(tunnel: Tunnel) -> list[Any]:
    """Read the current home-screen layout (always with formatVersion 2)."""
    async with service(tunnel) as svc:
        return await svc.get_icon_state(format_version="2")


async def write(tunnel: Tunnel, layout: list[Any]) -> None:
    """Write the raw Python layout using the official API; never JSON round-trip."""
    async with service(tunnel) as svc:
        await svc.set_icon_state(layout)


async def metrics(tunnel: Tunnel) -> dict[str, float]:
    """Ask the device for its real grid geometry instead of guessing."""
    async with service(tunnel) as svc:
        return await svc.get_homescreen_icon_metrics()


def page_capacity(metrics_map: dict[str, float]) -> int:
    columns = int(metrics_map.get("homeScreenIconColumns", 4))
    rows = int(metrics_map.get("homeScreenIconRows", 6))
    return columns * rows


def strip_icon(icon: dict[str, Any]) -> dict[str, Any]:
    """Keep only the fields SpringBoard actually stores for an icon."""
    return {key: icon[key] for key in ICON_KEYS if key in icon}


def _json_default(value: Any) -> dict[str, str]:
    if isinstance(value, datetime):
        return {"__pocketdesk_datetime__": value.isoformat()}
    raise TypeError(f"Unsupported snapshot value: {type(value).__name__}")


def _json_object(value: dict[str, Any]) -> Any:
    if set(value) == {"__pocketdesk_datetime__"}:
        return datetime.fromisoformat(value["__pocketdesk_datetime__"])
    # Legacy snapshots used default=str. Repair ONLY the known plist date field;
    # display names, versions and unrelated strings must remain untouched.
    if "displayIdentifier" in value and isinstance(value.get("iconModDate"), str):
        value["iconModDate"] = datetime.fromisoformat(value["iconModDate"])
    return value


def dump(layout: list[Any], path: str) -> None:
    """Export a snapshot without turning plist datetimes into ordinary strings."""
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(layout, handle, ensure_ascii=False, default=_json_default, indent=1)


def save(layout: list[Any], tag: str = "auto", directory: str | None = None) -> str:
    """Persist a layout snapshot and return its path."""
    target_dir = directory or BACKUP_DIR
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(
        target_dir, f"{tag}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    )
    dump(layout, path)
    return path


def load(path: str) -> list[Any]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle, object_hook=_json_object)


async def apply(tunnel: Tunnel, layout: list[Any], tag: str = "pre_apply") -> str:
    """Snapshot, write and verify actual placement; raise on unsafe/mismatched state."""
    from .layout import verify

    current = await read(tunnel)
    problems = verify(current, layout)
    if problems:
        raise ValueError("unsafe plan: " + "; ".join(problems))
    backup = save(current, tag)
    await write(tunnel, layout)
    actual = await read(tunnel)
    problems = verify(current, actual, expected=layout)
    if problems:
        raise ValueError(f"read-back failed (backup: {backup}): " + "; ".join(problems))
    return backup
