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


async def service(tunnel: Tunnel):
    from pymobiledevice3.services.springboard import SpringBoardServicesService
    return SpringBoardServicesService(tunnel.rsd)


async def read(tunnel: Tunnel) -> list[Any]:
    """Read the current home-screen layout (always with formatVersion 2)."""
    return await (await service(tunnel)).get_icon_state(format_version="2")


async def write(tunnel: Tunnel, layout: list[Any]) -> None:
    """Write a layout back to the device."""
    await (await service(tunnel)).set_icon_state(layout)


async def metrics(tunnel: Tunnel) -> dict[str, float]:
    """Ask the device for its real grid geometry instead of guessing."""
    return await (await service(tunnel)).get_homescreen_icon_metrics()


def page_capacity(metrics_map: dict[str, float]) -> int:
    columns = int(metrics_map.get("homeScreenIconColumns", 4))
    rows = int(metrics_map.get("homeScreenIconRows", 6))
    return columns * rows


def strip_icon(icon: dict[str, Any]) -> dict[str, Any]:
    """Keep only the fields SpringBoard actually stores for an icon."""
    return {key: icon[key] for key in ICON_KEYS if key in icon}


def save(layout: list[Any], tag: str = "auto", directory: str | None = None) -> str:
    """Persist a layout snapshot and return its path."""
    target_dir = directory or BACKUP_DIR
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(
        target_dir, f"{tag}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    )
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(layout, handle, ensure_ascii=False, default=str, indent=1)
    return path


def load(path: str) -> list[Any]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


async def apply(tunnel: Tunnel, layout: list[Any], tag: str = "pre_apply") -> str:
    """Snapshot the current layout, then write the new one. Returns backup path."""
    current = await read(tunnel)
    backup = save(current, tag)
    await write(tunnel, layout)
    return backup
