from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.run_representative_repairs import (
    _apply_permission_error,
    _prepare_case_copy,
    _run_one,
)


class RepresentativeRepairRunnerTest(unittest.TestCase):
    def test_direct_apply_requires_config_permission(self) -> None:
        item = {"allow_source_apply": False, "allow_controlled_copy_apply": True}

        error = _apply_permission_error(item, copy_suffix=None, allow_copy_apply=False)

        self.assertEqual(error, "representative config forbids direct source apply")

    def test_controlled_copy_apply_requires_flag_and_config_permission(self) -> None:
        item = {"allow_source_apply": False, "allow_controlled_copy_apply": True}

        self.assertEqual(
            _apply_permission_error(item, copy_suffix="_copy", allow_copy_apply=False),
            "controlled copy apply requires --allow-copy-apply",
        )
        self.assertIsNone(
            _apply_permission_error(item, copy_suffix="_copy", allow_copy_apply=True),
        )

        blocked_item = {"allow_source_apply": False, "allow_controlled_copy_apply": False}
        self.assertEqual(
            _apply_permission_error(blocked_item, copy_suffix="_copy", allow_copy_apply=True),
            "representative config forbids controlled copy apply",
        )

    def test_existing_copy_requires_explicit_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "AAAI" / "paper"
            destination = root / "AAAI" / "paper_copy"
            source.mkdir(parents=True)
            destination.mkdir(parents=True)
            item = {"conference": "AAAI", "case": "paper"}

            blocked = _prepare_case_copy(
                benchmark_root=root,
                copy_root=root,
                item=item,
                suffix="_copy",
                resume_existing=False,
            )
            resumed = _prepare_case_copy(
                benchmark_root=root,
                copy_root=root,
                item=item,
                suffix="_copy",
                resume_existing=True,
            )

            self.assertEqual(blocked["copy_status"], "blocked_existing")
            self.assertIn("--resume-existing-copy", blocked["copy_error"])
            self.assertEqual(resumed["copy_status"], "resumed_existing")

    @mock.patch("scripts.run_representative_repairs._handle_paperfit_request")
    @mock.patch("scripts.run_representative_repairs._detect_main_tex")
    def test_blocked_apply_is_not_counted_as_ok(self, detect_main_tex, handle_request) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            case_root = root / "AAAI" / "paper"
            (case_root / "data").mkdir(parents=True)
            main_tex = case_root / "main.tex"
            main_tex.write_text("\\documentclass{article}", encoding="utf-8")
            detect_main_tex.return_value = main_tex
            handle_request.return_value = {
                "run_result": {
                    "run_id": "r1",
                    "status": "blocked",
                    "gatekeeper_decision": "BLOCKED",
                    "defect_summary": {"remaining": 2},
                    "artifact_manifest": {"freshness": {"status": "stale_or_missing"}},
                    "runtime_actions": {
                        "repair_plan_executor": {"applied_count": 1, "requires_approval": True}
                    },
                }
            }

            result = _run_one(
                benchmark_root=root,
                item={"conference": "AAAI", "case": "paper", "main_tex": "main.tex"},
                run_id="r1",
                apply_source_mutation=True,
                max_rounds=1,
                request="repair layout",
            )

            self.assertTrue(result["execution_completed"])
            self.assertFalse(result["ok"])
            self.assertEqual(result["outcome"], "blocked_or_invalid_apply")


if __name__ == "__main__":
    unittest.main()
