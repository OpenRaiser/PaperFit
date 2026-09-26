from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from gatekeeper_enforcer import make_decision  # noqa: E402


class GatekeeperEnforcerTest(unittest.TestCase):
    def test_total_page_budget_violation_blocks_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            data_dir = root / "data"
            data_dir.mkdir()
            (data_dir / "visual_signal_report.json").write_text(
                json.dumps({"summary": {"pages_analyzed": 11}, "findings": []}),
                encoding="utf-8",
            )
            state = {
                "compile_success": True,
                "page_images_rendered": True,
                "task": {"target_pages": 9},
                "artifacts": {"visual_signal_report": "data/visual_signal_report.json"},
                "visual_signals_summary": {"updated_at": "2026-07-05T18:43:39"},
            }

            cwd = Path.cwd()
            try:
                os.chdir(root)
                decision = make_decision(
                    state=state,
                    defects_payload={"defects": []},
                    strict_mode=False,
                    semantic_report_path=None,
                )
            finally:
                os.chdir(cwd)

            self.assertEqual(decision["decision"], "CONTINUE")
            self.assertFalse(decision["checks"]["category_A"]["pass"])
            self.assertIn("category A blocking defects remaining", decision["reasons"])
            self.assertIn(
                {
                    "id": "system:page_budget_violation",
                    "defect_family": "A3",
                    "category": "A",
                    "severity": "critical",
                    "page": 11,
                    "label": None,
                    "description": "Page budget violation: current_pages=11, target_pages=9",
                },
                decision["remaining_defects"],
            )

    def test_main_body_page_budget_scope_does_not_block_endmatter_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            data_dir = root / "data"
            data_dir.mkdir()
            (data_dir / "visual_signal_report.json").write_text(
                json.dumps({"summary": {"pages_analyzed": 11}, "findings": []}),
                encoding="utf-8",
            )
            state = {
                "compile_success": True,
                "page_images_rendered": True,
                "task": {"target_pages": 9, "page_budget_scope": "main_body"},
                "artifacts": {"visual_signal_report": "data/visual_signal_report.json"},
                "visual_signals_summary": {"updated_at": "2026-07-05T18:43:39"},
            }

            cwd = Path.cwd()
            try:
                os.chdir(root)
                decision = make_decision(
                    state=state,
                    defects_payload={"defects": []},
                    strict_mode=False,
                    semantic_report_path=None,
                )
            finally:
                os.chdir(cwd)

            self.assertEqual(decision["decision"], "DONE")
            self.assertTrue(decision["checks"]["category_A"]["pass"])
            self.assertEqual(decision["remaining_defects"], [])


if __name__ == "__main__":
    unittest.main()
