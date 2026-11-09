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
        springboard.dump(plan, args.out)
        print(f"ok   plan written to {args.out}")

    if args.from_file and args.apply:
        print("\nFAIL --apply needs a device; drop --from to write")
        return 1

    if not args.apply:
        print("\n(dry run - nothing written. Re-run with --apply to commit)")
        return 0

    assert tunnel is not None
    return await _apply_verified(tunnel, state, plan, "before_apply")


async def _apply_verified(tunnel: Tunnel, current: list, plan: list, tag: str) -> int:
    """Do not report success until actual positions match, not merely counts."""
    problems = layout_mod.verify(current, plan)
    if problems:
        raise ValueError("unsafe plan: " + "; ".join(problems))
    backup = springboard.save(current, tag=tag)
    print(f"previous layout backed up to {backup}")
    return await _write_read_verified(tunnel, current, plan, backup)


async def _write_read_verified(tunnel: Tunnel, baseline: list, plan: list, backup: str) -> int:
    try:
        await springboard.write(tunnel, plan)
        await asyncio.sleep(4)
        actual = await springboard.read(tunnel)
    except Exception as exc:  # a timed-out request may already have changed iOS
        print(f"FAIL write/read-back: {type(exc).__name__}: {exc}")
        print(f"Outcome UNKNOWN; do not retry blindly. Inspect device; recovery snapshot: {backup}")
        return 1
    problems = layout_mod.verify(baseline, actual, expected=plan)
    if problems:
        for problem in problems:
            print(f"FAIL read-back: {problem}")
        print(f"Layout NOT verified. Inspect device; recovery snapshot: {backup}")
        return 1
    print("ok   written and read-back verified: Dock, icons, folders, order and page boundaries")
    return 0


def _unique_assignments(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate assignment key: {key}")
        result[key] = value
    return result


async def cmd_merge(args: argparse.Namespace, tunnel: Tunnel | None) -> int:
    if args.from_file and args.apply:
        print("FAIL --apply needs a device; drop --from to write")
        return 1
    with open(args.assignments, encoding="utf-8") as handle:
        assignments = json.load(handle, object_pairs_hook=_unique_assignments)
    if args.from_file:
        state = springboard.load(args.from_file)
    else:
        assert tunnel is not None
        state = await springboard.read(tunnel)
    plan = layout_mod.merge_layout(state, assignments)
    print(layout_mod.describe(plan))
    print("ok   verify: every app placed once, dock untouched; trailing pages removed")
    if args.out:
        springboard.dump(plan, args.out)
        print(f"ok   plan written to {args.out}")
    if not args.apply:
        print("\n(dry run - nothing written. Re-run with --apply to write)")
        return 0
    assert tunnel is not None
    return await _apply_verified(tunnel, state, plan, "before_merge")


async def cmd_restore(args: argparse.Namespace, tunnel: Tunnel) -> int:
    target = springboard.load(args.backup)
    problems = layout_mod.verify(target, target)
    if problems:
        raise ValueError("invalid restore snapshot: " + "; ".join(problems))
    current = await springboard.read(tunnel)
    # A restore intentionally changes the current Dock/inventory. Validate the
    # read-back against the restore target, not the layout being replaced.
    backup = springboard.save(current, tag="before_restore")
    print(f"previous layout backed up to {backup}")
    code = await _write_read_verified(tunnel, target, target, backup)
    if code == 0:
        print(f"ok   restored and read-back verified from {args.backup}")
    return code


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

def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pocketdesk",
        description="Organize an iPhone home screen from the iPhone itself.",
    )
    # duplicate the globals onto every subcommand so `check --pairing X` parses
    # the same as `--pairing X check`
    common = argparse.ArgumentParser(add_help=False)
    # SUPPRESS: when the flag is absent on the subcommand, do not overwrite the
    # value already parsed from the global position.
    common.add_argument("--pairing", default=argparse.SUPPRESS,
                        help="path to pairing.plist")
    common.add_argument("--host", default=argparse.SUPPRESS,
                        help=f"reflected host (default: {DEFAULT_HOST})")
    common.add_argument("--quiet", action="store_true", default=argparse.SUPPRESS,
                        help="hide handshake progress")

    parser.add_argument("--pairing", help="path to pairing.plist")
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help=f"reflected host (default {DEFAULT_HOST})")
    parser.add_argument("--quiet", action="store_true", help="hide handshake progress")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", parents=[common], help="preflight: pairing record + loopback reflection")

    p_list = sub.add_parser("list", parents=[common], help="show the current home screen")
    p_list.set_defaults(handler="list")

    p_grid = sub.add_parser("grid", parents=[common], help="print the device grid geometry")
    p_grid.set_defaults(handler="grid")

    p_plan = sub.add_parser("plan", parents=[common], help="build a layout and preview it")
    p_plan.add_argument("--rules", default="fine", help="rule set name or path (default: fine)")
    p_plan.add_argument("--apply", action="store_true", help="write the plan to the device")
    p_plan.add_argument("--out", help="also write the plan JSON here")
    p_plan.add_argument("--min-folder-size", type=int, default=2)
    p_plan.add_argument("--from", dest="from_file",
                        help="preview from a saved state JSON instead of a device")
    p_plan.add_argument("--capacity", type=int,
                        help="items per page to assume when using --from (default 24)")
    p_plan.set_defaults(handler="plan")

    p_merge = sub.add_parser("merge", parents=[common],
                             help="merge trailing loose icons into existing folders (dry run)")
    p_merge.add_argument("--assignments", required=True,
                         help="JSON object: displayIdentifier -> existing folder name")
    p_merge.add_argument("--apply", action="store_true", help="write only after explicit opt-in")
    p_merge.add_argument("--from", dest="from_file", help="offline snapshot; cannot combine with --apply")
    p_merge.add_argument("--out", help="export the proposed snapshot JSON")
    p_merge.set_defaults(handler="merge")

    p_restore = sub.add_parser("restore", parents=[common], help="restore a previous layout snapshot")
    p_restore.add_argument("backup", help="path to a snapshot JSON")
    p_restore.set_defaults(handler="restore")

    p_rules = sub.add_parser("rules", parents=[common], help="list rule sets, or write a starter file")
    p_rules.add_argument("--init", help="write a starter rule file at this path")
    p_rules.set_defaults(handler="rules")

    args = parser.parse_args(argv)

    if args.command == "check":
        return cmd_check(args)
    if args.command == "rules":
        return cmd_rules(args)

    if args.command in ("plan", "merge") and args.from_file:
        # offline: no tunnel needed at all
        handler = cmd_plan if args.command == "plan" else cmd_merge
        return asyncio.run(handler(args, None))

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
            if handler == "merge":
                return await cmd_merge(args, tunnel)
            if handler == "restore":
                return await cmd_restore(args, tunnel)
            return 2

    return asyncio.run(_run())


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except (ValueError, TypeError, OSError) as exc:
        print(f"FAIL {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
