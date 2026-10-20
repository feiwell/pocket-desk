"""CLI smoke tests that never open a tunnel.

Run: python3 tests/test_cli.py
"""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pocketdesk import cli  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "state_sample.json"


def run(argv):
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = cli.main(argv)
    return code, buffer.getvalue()


def test_help_exits_cleanly():
    try:
        run(["--help"])
    except SystemExit as exc:  # argparse prints help then exits
        assert exc.code == 0


def test_rules_lists_presets():
    code, out = run(["rules"])
    assert code == 0
    assert "fine" in out and "default" in out


def test_rules_init_writes_template(tmp_path=None):
    target = Path("/tmp/pocketdesk_test_rules.json")
    if target.exists():
        target.unlink()
    code, out = run(["rules", "--init", str(target)])
    assert code == 0 and target.is_file()
    data = json.loads(target.read_text(encoding="utf-8"))
    assert "groups" in data and "first_page" in data
    target.unlink()


def test_plan_offline_from_snapshot():
    code, out = run(["plan", "--rules", "fine", "--from", str(FIXTURE)])
    assert code == 0, out
    assert "verify: every app placed once" in out
    assert "Dock:" in out


def test_plan_offline_rejects_apply():
    code, out = run(["plan", "--rules", "fine", "--from", str(FIXTURE), "--apply"])
    assert code == 1
    assert "needs a device" in out


def test_check_reports_missing_pairing(monkeypatch=None):
    # check is safe: it never dials 49152. We only assert it returns an int.
    code, out = run(["check"])
    assert code in (0, 1)
    assert "pairing record" in out


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
            except SystemExit:
                pass
    sys.exit(1 if failures else 0)
