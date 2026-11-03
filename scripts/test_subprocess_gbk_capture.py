"""Windows GBK 编码回归：子进程输出含非 GBK 字节时进程捕获必须可读。"""

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


# 模拟 pdflatex 日志：UTF-8 重音/数学符号 + 孤立高位字节（cp936 无法解码）
TEX_LIKE_OUTPUT = (
    "Overfull \\hbox (12.34pt too wide) in paragraph at lines 3--7\n"
    "LaTeX Warning: référence indéfinie ✓ élargie\n"
).encode("utf-8") + b"\x81\x8b\xff\xfe\n"

TEX_LIKE_CHILD = (
    "import sys\n"
    "sys.stdout.buffer.write(%r)\n"
    "sys.stdout.buffer.flush()\n"
    "sys.stderr.buffer.write(%r)\n"
    "sys.stderr.buffer.flush()\n"
) % (TEX_LIKE_OUTPUT, b"!\x81 Emergency stop.\n")


class SubprocessGbkCaptureTest(unittest.TestCase):
    def test_run_command_reads_non_gbk_tex_output(self) -> None:
        """验收：非 GBK 字节的 TeX 输出 → 可读 stdout/stderr + returncode 保真。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_command(
                [sys.executable, "-c", TEX_LIKE_CHILD],
                cwd=Path(tmpdir),
                timeout=60,
            )
        self.assertEqual(result.returncode, 0)
        self.assertIsInstance(result.stdout, str)
        self.assertIsInstance(result.stderr, str)
        self.assertIn("Overfull", result.stdout)
        self.assertIn("12.34pt", result.stdout)
        self.assertIn("référence indéfinie", result.stdout)
        self.assertIn("Emergency stop", result.stderr)

    def test_run_command_preserves_failure_returncode_and_streams(self) -> None:
        child = (
            "import sys\n"
            "sys.stdout.buffer.write(%r)\n"
            "sys.stderr.buffer.write(%r)\n"
            "sys.exit(1)\n"
        ) % (b"partial \xc3\xa9 output", b"! LaTeX Error: file not found.\n")
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_command(
                [sys.executable, "-c", child],
                cwd=Path(tmpdir),
                timeout=60,
            )
        self.assertEqual(result.returncode, 1)
        self.assertIsInstance(result.stdout, str)
        self.assertIsInstance(result.stderr, str)
        self.assertIn("LaTeX Error", result.stderr)

    def test_run_command_empty_output_yields_empty_strings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_command(
                [sys.executable, "-c", "pass"],
                cwd=Path(tmpdir),
                timeout=60,
            )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_paperfit_command_run_reads_non_gbk_tex_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = paperfit_command._run(
                [sys.executable, "-c", TEX_LIKE_CHILD],
                cwd=Path(tmpdir),
                timeout=60,
            )
        self.assertEqual(result.returncode, 0)
        self.assertIsInstance(result.stdout, str)
        self.assertIsInstance(result.stderr, str)
        self.assertIn("Overfull", result.stdout)
        self.assertIn("Emergency stop", result.stderr)


if __name__ == "__main__":
    unittest.main()
