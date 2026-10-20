# iconState format notes (iOS 26 / 27)

Everything here was confirmed against a real device (iPhone18,2 / iOS 27.0.1)
by reading and writing the state and observing what SpringBoard accepted.
Where this file says "silently", the device returned success and did the wrong
thing anyway.

## Top level

```text
state = [dock, page1, page2, ...]
```

**Index 0 is the dock, not the first page.** It holds exactly
`homeScreenIconDockMaxCount` items (4 on the devices tested).

Symptom of getting this wrong: after writing, the dock is empty or shifted, and
your "first page" content appears one page later than expected.

## A page

```text
page = [item, item, ...]          // formatVersion "2" returns a flat list
```

Older/other revisions return `page -> [row, row, ...]` where each row is a list.
Both shapes occur; normalise before counting:

```python
if page and all(isinstance(row, list) for row in page):
    flat = [item for row in page for item in row]
else:
    flat = page
```

`False` entries are empty slots, not corruption. Do not delete them blindly if
you are trying to preserve an exact layout.

## An item

An icon is a dict of five stored fields:

```json
{
  "displayIdentifier": "com.apple.MobileSMS",
  "displayName": "Messages",
  "iconModDate": "2026-10-18 15:25:54.411631",
  "bundleVersion": "1491.100.1.2.28",
  "bundleIdentifier": "com.apple.MobileSMS"
}
```

`iconModDate` deserialises into a `datetime`. That matters for round-tripping —
see below.

A folder:

```json
{
  "listType": "folder",
  "displayName": "Social",
  "iconLists": [[ {icon}, {icon} ]]
}
```

`iconLists` is an **array of arrays**. The outer array is pages of the folder
(a folder can scroll); the inner arrays are the icons on each page.

## The five ways this format bites you

### 1. Missing `formatVersion` on read

```python
{"command": "getIconState"}                          # ❌ degraded
{"command": "getIconState", "formatVersion": "2"}    # ✅
```

Without it, later pages come back with most items collapsed to `False` and all
folder information gone. It looks like the folders were deleted; they were not.
`SpringBoardServicesService.get_icon_state()` passes `"2"` by default.

### 2. Wrong folder field names on write

```json
{"listType": "folder", "folderName": "Social", "iconList": [...]}   // ❌
{"listType": "folder", "displayName": "Social", "iconLists": [[...]]} // ✅
```

SpringBoard ignores unknown keys **without error** and flattens the folder into
loose icons. Reading back shows all icons present, so a naive "did I lose any
apps?" check passes while the layout is wrong. Verify by counting
`listType == "folder"` entries, not by counting icons.

### 3. Assuming iOS will repack pages

If you hand it `[16 items], [8 items]` you get two pages with a gap on the first
one, even though the device fits 24 per page. **Page boundaries are literal.**
Pack them yourself:

```python
pages = [items[i:i + capacity] for i in range(0, len(items), capacity)]
```

Read `capacity` from the device:

```python
metrics = await service.get_homescreen_icon_metrics()
capacity = int(metrics["homeScreenIconColumns"]) * int(metrics["homeScreenIconRows"])
```

Reported values on iPhone18,2:

| key | value |
| --- | ---: |
| `homeScreenIconColumns` | 4 |
| `homeScreenIconRows` | 6 |
| `homeScreenIconDockMaxCount` | 4 |
| `homeScreenIconMaxPages` | 15 |
| `homeScreenIconFolderColumns` | 3 |
| `homeScreenIconFolderRows` | 3 |
| `homeScreenIconFolderMaxPages` | 15 |

### 4. JSON round-tripping `iconModDate`

`json.dump(..., default=str)` is fine for **snapshots**. It is not fine for a
state you intend to write back: the datetime becomes a string, SpringBoard
rejects it, and the home screen comes back empty or unchanged.

Write back the objects `getIconState` gave you, not a JSON re-parse of them.

### 5. Writing the dock into a folder

`collect_icons()` must skip index 0. If it does not, every dock app is also
placed inside a folder and appears twice.

Two invariants worth asserting before any write:

```text
len(dock) + len(items_in_folders) == total apps on device
set(dock_ids) & set(folder_ids) == empty
```

## App Library side effect

`setIconState` materialises App Library apps onto the home screen. On the test
device the icon count went from 139 to 166. **There is no API to send them
back** — only the manual "Remove from Home Screen" action. Tell the user before
writing.

## Service and command reference

| purpose | call |
| --- | --- |
| read | `SpringBoardServicesService(rsd).get_icon_state(format_version="2")` |
| write | `SpringBoardServicesService(rsd).set_icon_state(state)` |
| geometry | `SpringBoardServicesService(rsd).get_homescreen_icon_metrics()` |
| service name | `com.apple.springboardservices.shim.remote` |

`set_icon_state()` is a plain plist command over a `send_recv_prefixed` frame and
returns in well under a second. If a hand-rolled `setIconState` times out, the
payload structure is wrong — not the network.
