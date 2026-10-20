"""Command line entry point."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import sys
import threading
import time

from . import layout as layout_mod
from . import rules as rules_mod
from . import springboard
from .tunnel import DEFAULT_HOST, Tunnel, resolve_pairing


# ---------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------

def _reflect_probe(timeout: float = 6.0) -> tuple[bool, str]:
    """Verify loopback reflection with a real echo, not just a TCP connect.

    Some userspace network stacks complete the handshake for ANY address and
    port, so `connect()` succeeding proves nothing. We bind an ephemeral port,
    connect to it through 10.7.0.1, and require the bytes to come back.
    """
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", 0))
    port = server.getsockname()[1]
    server.listen(1)
    server.settimeout(timeout)
    seen: dict = {}

    def _accept() -> None:
        try:
            conn, addr = server.accept()
            seen["from"] = addr
            try:
                conn.sendall(b"POCKETDESK")
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            seen["error"] = repr(exc)

    threading.Thread(target=_accept, daemon=True).start()
    time.sleep(0.3)

    client = socket.socket()
    client.settimeout(timeout)
    try:
        client.connect((DEFAULT_HOST, port))
        data = client.recv(16)
        if data == b"POCKETDESK":
            return True, f"reflection ok ({DEFAULT_HOST}:{port} -> local {seen.get('from')})"
        return False, f"connected but no echo: {data!r}"
    except Exception as exc:  # noqa: BLE001
        return False, f"reflection failed: {type(exc).__name__}: {exc}"
    finally:
        client.close()
        server.close()


def cmd_check(args: argparse.Namespace) -> int:
    ok = True
    pairing = args.pairing or resolve_pairing()
    if pairing:
        mode = oct(os.stat(pairing).st_mode & 0o777)
        print(f"ok   pairing record: {pairing} ({mode})")
    else:
        print("FAIL no pairing record "
              "(pass --pairing PATH or set $POCKETDESK_PAIRING)")
        ok = False

    reflected, detail = _reflect_probe()
    print(("ok   " if reflected else "FAIL ") + detail)
    ok = ok and reflected

    print("\n" + ("ready." if ok else "not ready - fix the FAIL line above."))
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# tunnel-backed commands
# ---------------------------------------------------------------------------

async def cmd_list(tunnel: Tunnel) -> int:
    state = await springboard.read(tunnel)
    capacity = springboard.page_capacity(await springboard.metrics(tunnel))
    dock = layout_mod.dock_of(state)
    pages = layout_mod.unwrap_pages(state)

    print(f"Dock ({len(dock)}): " + "、".join(x.get("displayName", "?") for x in dock))
    for index, page in enumerate(pages, 1):
        folders = [x for x in page if isinstance(x, dict) and x.get("listType") == "folder"]
        icons = [x for x in page if isinstance(x, dict) and x.get("listType") != "folder"]
        print(f"\nPage {index}: {len(icons)} icons / {len(folders)} folders "
              f"({len(page)}/{capacity} slots)")
        for folder in folders:
            members = [i.get("displayName", "?") for lst in folder.get("iconLists", []) for i in lst]
            print(f"  [{folder['displayName']}] ({len(members)}): " + "、".join(members))
        if icons:
            print("  loose: " + "、".join(i.get("displayName", "?") for i in icons))
    return 0


async def cmd_grid(tunnel: Tunnel) -> int:
    metrics = await springboard.metrics(tunnel)
    for key in sorted(metrics):
        print(f"  {key:32s} {metrics[key]}")
    print(f"\n  page capacity: {springboard.page_capacity(metrics)} items")
    return 0


async def cmd_plan(args: argparse.Namespace, tunnel: Tunnel | None) -> int:
    if args.from_file:
        state = springboard.load(args.from_file)
        capacity = args.capacity or 24
        print(f"(offline) state from {args.from_file}, capacity assumed {capacity}")
    else:
        assert tunnel is not None
        state = await springboard.read(tunnel)
        capacity = springboard.page_capacity(await springboard.metrics(tunnel))

    groups, first_page, name = rules_mod.load(args.rules)
    plan = layout_mod.build_layout(
        state, groups=groups, first_page=first_page,
        page_capacity=capacity, min_folder_size=args.min_folder_size,
    )

    print(f"rule set: {name}   page capacity: {capacity}   "
          f"pages in plan: {len(plan) - 1}\n")
    print(layout_mod.describe(plan))

    problems = layout_mod.verify(state, plan)
    print()
    if problems:
        for problem in problems:
            print(f"FAIL {problem}")
        return 1
    print("ok   verify: every app placed once, dock untouched")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(plan, handle, ensure_ascii=False, default=str, indent=1)
        print(f"ok   plan written to {args.out}")

    if args.from_file and args.apply:
        print("\nFAIL --apply needs a device; drop --from to write")
        return 1

    if not args.apply:
        print("\n(dry run - nothing written. Re-run with --apply to commit)")
        return 0

    assert tunnel is not None
    backup = springboard.save(state, tag="before_apply")
    await springboard.write(tunnel, plan)
    print(f"ok   written. previous layout backed up to {backup}")

    await asyncio.sleep(4)
    after = await springboard.read(tunnel)
    dock = layout_mod.dock_of(after)
    pages = layout_mod.unwrap_pages(after)
    print(f"read back: dock {len(dock)}, {len(pages)} page(s), "
          f"items per page {[len(p) for p in pages]}")
    return 0


async def cmd_restore(args: argparse.Namespace, tunnel: Tunnel) -> int:
    target = springboard.load(args.backup)
    current = await springboard.read(tunnel)
    safety = springboard.save(current, tag="before_restore")
    await springboard.write(tunnel, target)
    print(f"ok   restored from {args.backup}")
    print(f"     previous state saved to {safety}")
    return 0


def cmd_rules(args: argparse.Namespace) -> int:
    if args.init:
        path = rules_mod.write_template(args.init)
        print(f"ok   wrote starter rule file: {path}")
        return 0
    names = rules_mod.available()
    print("available rule sets: " + (", ".join(names) or "(none)"))
    for name in names:
        groups, _first_page, _ = rules_mod.load(name)
        total = sum(len(v) for v in groups.values())
        print(f"  {name:12s} {len(groups):2d} folders, {total:3d} bundle ids")
    return 0


# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pocketdesk",
        description="Organize an iPhone home screen from the iPhone itself.",
    )
    parser.add_argument("--pairing", help="path to pairing.plist")
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help=f"reflected host (default {DEFAULT_HOST})")
    parser.add_argument("--quiet", action="store_true", help="hide handshake progress")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="preflight: pairing record + loopback reflection")

    p_list = sub.add_parser("list", help="show the current home screen")
    p_list.set_defaults(handler="list")

    p_grid = sub.add_parser("grid", help="print the device grid geometry")
    p_grid.set_defaults(handler="grid")

    p_plan = sub.add_parser("plan", help="build a layout and preview it")
    p_plan.add_argument("--rules", default="fine", help="rule set name or path (default: fine)")
    p_plan.add_argument("--apply", action="store_true", help="write the plan to the device")
    p_plan.add_argument("--out", help="also write the plan JSON here")
    p_plan.add_argument("--min-folder-size", type=int, default=2)
    p_plan.add_argument("--from", dest="from_file",
                        help="preview from a saved state JSON instead of a device")
    p_plan.add_argument("--capacity", type=int,
                        help="items per page to assume when using --from (default 24)")
    p_plan.set_defaults(handler="plan")

    p_restore = sub.add_parser("restore", help="restore a previous layout snapshot")
    p_restore.add_argument("backup", help="path to a snapshot JSON")
    p_restore.set_defaults(handler="restore")

    p_rules = sub.add_parser("rules", help="list rule sets, or write a starter file")
    p_rules.add_argument("--init", help="write a starter rule file at this path")
    p_rules.set_defaults(handler="rules")

    args = parser.parse_args(argv)

    if args.command == "check":
        return cmd_check(args)
    if args.command == "rules":
        return cmd_rules(args)

    if args.command == "plan" and args.from_file:
        # offline: no tunnel needed at all
        return asyncio.run(cmd_plan(args, None))

    async def _run() -> int:
        async with Tunnel(host=args.host, pairing=args.pairing,
                          verbose=not args.quiet) as tunnel:
            handler = args.handler
            if handler == "list":
                return await cmd_list(tunnel)
            if handler == "grid":
                return await cmd_grid(tunnel)
            if handler == "plan":
                return await cmd_plan(args, tunnel)
            if handler == "restore":
                return await cmd_restore(args, tunnel)
            return 2

    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
