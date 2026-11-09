"""Official API lifecycle tests, with no live service construction."""
import asyncio
from contextlib import redirect_stdout
from datetime import datetime
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from pocketdesk import layout, springboard
from tests.synthetic import assignments, state


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_write_metrics_connect_then_call_then_close(self):
        for operation, method in ((springboard.read, "get_icon_state"),
                                  (springboard.write, "set_icon_state"),
                                  (springboard.metrics, "get_homescreen_icon_metrics")):
            events = []
            value = state() if operation != springboard.metrics else {"homeScreenIconRows": 6}
            svc = SimpleNamespace()
            for name in ("connect", "close", method):
                async def callback(*args, _name=name, **kwargs):
                    events.append(_name)
                    return value
                setattr(svc, name, AsyncMock(side_effect=callback))
            tunnel = SimpleNamespace(rsd=object())
            # Patch the actual import target, not a hand-built protocol substitute.
            with patch("pymobiledevice3.services.springboard.SpringBoardServicesService",
                       return_value=svc) as factory:
                result = await operation(tunnel, value) if operation == springboard.write else await operation(tunnel)
            self.assertEqual(events, ["connect", method, "close"])
            factory.assert_called_once_with(tunnel.rsd)
            svc.connect.assert_awaited_once_with()
            svc.close.assert_awaited_once_with()
            if operation == springboard.read:
                svc.get_icon_state.assert_awaited_once_with(format_version="2")
                self.assertIs(result, value)
            elif operation == springboard.write:
                self.assertIs(svc.set_icon_state.await_args.args[0], value)
                self.assertIsInstance(value[2][0]["iconModDate"], datetime)
            else:
                self.assertIs(result, value)

    async def test_close_in_finally_on_connection_and_each_operation_failure(self):
        operations = ((springboard.read, "get_icon_state"),
                      (springboard.write, "set_icon_state"),
                      (springboard.metrics, "get_homescreen_icon_metrics"))
        for operation, method in operations:
            for stage in ("connect", method):
                svc = SimpleNamespace(connect=AsyncMock(), close=AsyncMock())
                setattr(svc, method, AsyncMock())
                getattr(svc, stage).side_effect = RuntimeError("synthetic failure")
                with self.subTest(operation=method, stage=stage), \
                     patch("pymobiledevice3.services.springboard.SpringBoardServicesService", return_value=svc):
                    with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                        if operation == springboard.write:
                            await operation(SimpleNamespace(rsd=object()), state())
                        else:
                            await operation(SimpleNamespace(rsd=object()))
                svc.close.assert_awaited_once_with()
                if stage == "connect":
                    getattr(svc, method).assert_not_awaited()

    async def test_close_on_cancellation(self):
        svc = SimpleNamespace(connect=AsyncMock(), close=AsyncMock(),
                              get_icon_state=AsyncMock(side_effect=asyncio.CancelledError()))
        with patch("pymobiledevice3.services.springboard.SpringBoardServicesService", return_value=svc):
            with self.assertRaises(asyncio.CancelledError):
                await springboard.read(SimpleNamespace(rsd=object()))
        svc.close.assert_awaited_once_with()

    async def test_public_apply_verifies_positions(self):
        before = state()
        plan = layout.merge_layout(before, assignments())
        wrong = state()  # same icons, not the proposed folders/pages
        for actual, succeeds in ((plan, True), (wrong, False)):
            with patch.object(springboard, "read", new=AsyncMock(side_effect=[before, actual])), \
                 patch.object(springboard, "write", new=AsyncMock()), \
                 patch.object(springboard, "save", return_value="synthetic.json"):
                if succeeds:
                    self.assertEqual(await springboard.apply(object(), plan), "synthetic.json")
                else:
                    with self.assertRaisesRegex(ValueError, "read-back failed"):
                        await springboard.apply(object(), plan)


class SnapshotTests(unittest.TestCase):
    def test_save_load_retains_datetime(self):
        with tempfile.TemporaryDirectory() as directory:
            path = springboard.save(state(), directory=directory)
            self.assertEqual(springboard.load(path), state())

    def test_legacy_json_repairs_only_icon_mod_date(self):
        source = state()
        source[2][0]["displayName"] = "2026-01-02 03:04:05"
        source[2][0]["bundleVersion"] = "2026-01-02 03:04:05"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.json"
            path.write_text(json.dumps(source, default=str))
            restored = springboard.load(str(path))
        self.assertEqual(restored, source)
        self.assertIsInstance(restored[2][0]["iconModDate"], datetime)
        self.assertIsInstance(restored[2][0]["displayName"], str)
        self.assertIsInstance(restored[2][0]["bundleVersion"], str)

    def test_invalid_legacy_date_and_unsupported_values_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text('[[], [{"displayIdentifier":"test.app", "iconModDate":"not a date"}]]')
            with self.assertRaises(ValueError):
                springboard.load(str(path))
            with self.assertRaises(TypeError):
                springboard.dump([[], [object()]], str(path))


if __name__ == "__main__":
    unittest.main()
