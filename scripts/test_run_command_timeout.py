from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import paperfit_command  # noqa: E402
from runtime_actions import run_command  # noqa: E402


SLEEP_COMMAND = [sys.executable, "-c", "import time; time.sleep(30)"]


class RunCommandTimeoutTest(unittest.TestCase):
    """Timed-out subprocesses must terminate cleanly on every platform."""

    def test_runtime_actions_run_command_timeout_returns_124(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_command(SLEEP_COMMAND, cwd=Path(tmpdir), timeout=1)
        self.assertEqual(result.returncode, 124)
        self.assertIn("TIMEOUT after 1s", result.stderr)

    def test_paperfit_command_run_timeout_returns_124(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = paperfit_command._run(SLEEP_COMMAND, cwd=Path(tmpdir), timeout=1)
        self.assertEqual(result.returncode, 124)
        self.assertIn("TIMEOUT after 1s", result.stderr)

    def test_run_command_normal_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_command(
                [sys.executable, "-c", "print('hello')"],
                cwd=Path(tmpdir),
                timeout=30,
            )
        self.assertEqual(result.returncode, 0)
        self.assertIn("hello", result.stdout)


if __name__ == "__main__":
    unittest.main()
