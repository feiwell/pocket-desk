"""CLI tests: synthetic JSON, mock services, no device or real backups."""
import argparse
import asyncio
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from pocketdesk import cli, layout, springboard
from tests.synthetic import assignments, state


def run(argv):
    output = io.StringIO()
    with redirect_stdout(output):
        code = cli.main(argv)
    return code, output.getvalue()


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.snapshot = self.root / "synthetic.json"
        springboard.dump(state(), str(self.snapshot))
        self.mapping = self.root / "mapping.json"
        self.mapping.write_text(json.dumps(assignments()), encoding="utf-8")
        # Any accidental tunnel or pairing access is a hard test failure.
        self.tunnel = patch.object(cli, "Tunnel", side_effect=AssertionError("device forbidden"))
        self.tunnel.start()
        self.addCleanup(self.tunnel.stop)

    def test_help_and_rules(self):
        with self.assertRaises(SystemExit) as caught:
            run(["--help"])
        self.assertEqual(caught.exception.code, 0)
        code, out = run(["rules"])
        self.assertEqual(code, 0)
        self.assertIn("fine", out)
        target = self.root / "rules.json"
        self.assertEqual(run(["rules", "--init", str(target)])[0], 0)
        self.assertIn("groups", json.loads(target.read_text()))

    def test_plan_offline_is_dry_run_and_export_preserves_datetime(self):
        target = self.root / "plan.json"
        code, out = run(["plan", "--from", str(self.snapshot), "--out", str(target)])
        self.assertEqual(code, 0, out)
        self.assertIn("dry run", out)
        self.assertIn("verify: every app placed once", out)
        self.assertEqual(layout.verify(state(), springboard.load(str(target))), [])

    def test_offline_apply_rejected_for_plan_and_merge(self):
        for cmd in ("plan", "merge"):
            argv = [cmd, "--from", str(self.snapshot), "--apply"]
            if cmd == "merge":
                argv += ["--assignments", str(self.mapping)]
            code, out = run(argv)
            self.assertEqual(code, 1, out)
            self.assertIn("needs a device", out)

    def test_merge_offline_default_dry_run(self):
        target = self.root / "merged.json"
        with patch.object(springboard, "write", new_callable=AsyncMock) as write:
            code, out = run(["merge", "--from", str(self.snapshot),
                             "--assignments", str(self.mapping), "--out", str(target)])
        self.assertEqual(code, 0, out)
        self.assertIn("dry run", out)
        write.assert_not_awaited()
        self.assertEqual(springboard.load(str(target)), layout.merge_layout(state(), assignments()))

    def test_merge_rejects_duplicate_json_keys(self):
        self.mapping.write_text('{"test.new.a":"Tools","test.new.a":"Reading"}')
        code, out = run(["merge", "--from", str(self.snapshot), "--assignments", str(self.mapping)])
        self.assertEqual(code, 1)
        self.assertIn("duplicate assignment key", out)

    def test_merge_rejects_incomplete_mapping_and_widgets(self):
        self.mapping.write_text("{}")
        code, out = run(["merge", "--from", str(self.snapshot), "--assignments", str(self.mapping)])
        self.assertEqual(code, 1)
        self.assertIn("cover all", out)
        bad = state()
        bad[-1].append({"listType": "widget"})
        springboard.dump(bad, str(self.snapshot))
        code, out = run(["plan", "--from", str(self.snapshot)])
        self.assertEqual(code, 1)
        self.assertIn("unsupported", out)

    def test_check_is_fully_mocked(self):
        with patch.object(cli, "resolve_pairing", return_value=None), \
             patch.object(cli, "_reflect_probe", return_value=(False, "mock reflection")):
            code, out = run(["check"])
        self.assertEqual(code, 1)
        self.assertIn("no pairing record", out)


class ApplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_apply_saves_before_write_and_checks_raw_readback(self):
        source = state()
        plan = layout.merge_layout(source, assignments())
        events = []
        async def write(_tunnel, value):
            events.append("write")
            self.assertIs(value, plan)
        def save(value, tag):
            events.append("backup")
            self.assertIs(value, source)
            return "synthetic-backup.json"
        with patch.object(springboard, "save", side_effect=save), \
             patch.object(springboard, "write", side_effect=write), \
             patch.object(springboard, "read", new=AsyncMock(return_value=plan)), \
             patch.object(cli.asyncio, "sleep", new=AsyncMock()):
            output = io.StringIO()
            with redirect_stdout(output):
                code = await cli._apply_verified(object(), source, plan, "before_merge")
        self.assertEqual(code, 0, output.getvalue())
        self.assertEqual(events, ["backup", "write"])
        self.assertIn("read-back verified", output.getvalue())

    async def test_wrong_actual_layout_fails_even_with_same_counts(self):
        source = state()
        plan = layout.merge_layout(source, assignments())
        wrong = deepcopy(plan)
        wrong.append([wrong[1].pop()])
        with patch.object(springboard, "save", return_value="synthetic-backup.json"), \
             patch.object(springboard, "write", new=AsyncMock()), \
             patch.object(springboard, "read", new=AsyncMock(return_value=wrong)), \
             patch.object(cli.asyncio, "sleep", new=AsyncMock()):
            output = io.StringIO()
            with redirect_stdout(output):
                code = await cli._apply_verified(object(), source, plan, "before_apply")
        self.assertEqual(code, 1)
        self.assertIn("NOT verified", output.getvalue())
        self.assertNotIn("ok   written", output.getvalue())

    async def test_timeout_or_read_failure_reports_unknown_not_success(self):
        source = state()
        plan = layout.merge_layout(source, assignments())
        for stage in ("write", "read"):
            write = AsyncMock(side_effect=TimeoutError("mock timeout") if stage == "write" else None)
            read = AsyncMock(side_effect=ConnectionError("mock disconnected"))
            with patch.object(springboard, "save", return_value="synthetic-backup.json"), \
                 patch.object(springboard, "write", new=write), \
                 patch.object(springboard, "read", new=read), \
                 patch.object(cli.asyncio, "sleep", new=AsyncMock()):
                output = io.StringIO()
                with redirect_stdout(output):
                    code = await cli._apply_verified(object(), source, plan, "before_merge")
            self.assertEqual(code, 1)
            self.assertIn("Outcome UNKNOWN", output.getvalue())
            self.assertNotIn("ok   written", output.getvalue())

    async def test_online_plan_and_merge_without_apply_never_write(self):
        for command in (cli.cmd_plan, cli.cmd_merge):
            with tempfile.TemporaryDirectory() as temp:
                mapping = Path(temp) / "mapping.json"
                mapping.write_text(json.dumps(assignments()))
                args = argparse.Namespace(from_file=None, rules="fine", capacity=None,
                                          min_folder_size=2, out=None, apply=False,
                                          assignments=str(mapping))
                with patch.object(springboard, "read", new=AsyncMock(return_value=state())), \
                     patch.object(springboard, "metrics", new=AsyncMock(return_value={})), \
                     patch.object(springboard, "write", new=AsyncMock()) as write, \
                     patch.object(springboard, "save") as save, redirect_stdout(io.StringIO()):
                    code = await command(args, object())
                self.assertEqual(code, 0)
                write.assert_not_awaited()
                save.assert_not_called()

    async def test_restore_checks_actual_layout(self):
        target = state()
        wrong = deepcopy(target)
        wrong[-1].extend(wrong[-2])
        wrong[-2] = []
        for actual, expected_code in ((target, 0), (wrong, 1)):
            with patch.object(springboard, "load", return_value=target), \
                 patch.object(springboard, "save", return_value="synthetic-backup.json"), \
                 patch.object(springboard, "write", new=AsyncMock()), \
                 patch.object(springboard, "read", new=AsyncMock(side_effect=[state(), actual])), \
                 patch.object(cli.asyncio, "sleep", new=AsyncMock()), redirect_stdout(io.StringIO()):
                code = await cli.cmd_restore(argparse.Namespace(backup="synthetic.json"), object())
            self.assertEqual(code, expected_code)


if __name__ == "__main__":
    unittest.main()
