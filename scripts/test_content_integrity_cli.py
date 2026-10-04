from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from content_integrity_check import cmd_post, cmd_pre  # noqa: E402


_LONG_PARAGRAPH = (
    "The proposed method combines sparse attention with a contrastive objective, "
    "and we evaluate it on three public benchmarks with consistent gains. "
) * 12  # ~3.4k chars: far beyond the old 500-char reference field


def _write_tex(path: Path, body: str) -> None:
    path.write_text(
        "\\documentclass{article}\n\\begin{document}\n" + body + "\n\\end{document}\n",
        encoding="utf-8",
    )


class ContentIntegrityCliTest(unittest.TestCase):
    def _pre(self, tmp: Path, body: str) -> Path:
        tex = tmp / "main.tex"
        _write_tex(tex, body)
        baseline_path = tmp / "baseline.json"
        cmd_pre(
            SimpleNamespace(
                tex_file=str(tex),
                line_range="3,3",
                output=str(baseline_path),
            )
        )
        return baseline_path

    def _post(self, tmp: Path, baseline: Path, body: str) -> dict:
        tex = tmp / "main.tex"
        _write_tex(tex, body)
        output_path = tmp / "post.json"
        result = cmd_post(
            SimpleNamespace(
                tex_file=str(tex),
                line_range="3,3",
                baseline=str(baseline),
                output=str(output_path),
            )
        )
        # The written report must match the returned dict.
        on_disk = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["action_required"], result["action_required"])
        return result

    def test_long_unchanged_content_is_not_flagged_by_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            baseline = self._pre(tmp, _LONG_PARAGRAPH)
            stored = json.loads(baseline.read_text(encoding="utf-8"))
            self.assertLess(len(stored["content"]), len(_LONG_PARAGRAPH))
            # get_line_range_content keeps the trailing newline of the line.
            self.assertEqual(stored["full_content"], _LONG_PARAGRAPH + "\n")
            self.assertEqual(stored["content_length"], len(_LONG_PARAGRAPH) + 1)
            result = self._post(tmp, baseline, _LONG_PARAGRAPH)
            self.assertFalse(result["baseline_truncated"])
            self.assertEqual(result["action_required"], "none")
            self.assertEqual(result["violation"]["level"], 0)

    def test_real_content_loss_still_reaches_auto_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            baseline = self._pre(tmp, _LONG_PARAGRAPH)
            # Drop most of the paragraph (>15% word drift).
            gutted = _LONG_PARAGRAPH[:120]
            result = self._post(tmp, baseline, gutted)
            self.assertFalse(result["baseline_truncated"])
            self.assertEqual(result["action_required"], "auto_rollback")
            self.assertGreaterEqual(result["violation"]["level"], 3)

    def test_legacy_truncated_baseline_never_auto_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            baseline = self._pre(tmp, _LONG_PARAGRAPH)
            stored = json.loads(baseline.read_text(encoding="utf-8"))
            legacy = {
                "timestamp": stored["timestamp"],
                "file": stored["file"],
                "line_range": stored["line_range"],
                "semantic_hash": stored["semantic_hash"],
                "content": stored["content"],  # only the truncated reference survives
            }
            legacy_path = tmp / "legacy_baseline.json"
            legacy_path.write_text(json.dumps(legacy), encoding="utf-8")
            result = self._post(tmp, legacy_path, _LONG_PARAGRAPH)
            self.assertTrue(result["baseline_truncated"])
            self.assertEqual(result["action_required"], "manual_review")
            self.assertIn("truncated", result["action_note"])

    def test_short_legacy_baseline_behaves_normally(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            short_body = "A short paragraph about the method."
            baseline = self._pre(tmp, short_body)
            stored = json.loads(baseline.read_text(encoding="utf-8"))
            legacy = {"content": stored["full_content"]}
            legacy_path = tmp / "legacy_short.json"
            legacy_path.write_text(json.dumps(legacy), encoding="utf-8")
            result = self._post(tmp, legacy_path, short_body)
            self.assertFalse(result["baseline_truncated"])
            self.assertEqual(result["action_required"], "none")

    def test_unicode_content_survives_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            body = "该模型在三个公开基准上取得一致提升，准确率提升 3.2 个百分点。"
            baseline = self._pre(tmp, body)
            result = self._post(tmp, baseline, body)
            self.assertEqual(result["action_required"], "none")


if __name__ == "__main__":
    unittest.main()
