from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.monitor_server import _case_evidence


class MonitorServerEvidenceTest(unittest.TestCase):
    def test_case_quality_uses_latest_run_instead_of_historical_maxima(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            data = root / "data"
            pages = data / "pages"
            pages.mkdir(parents=True)
            (pages / "page_001.png").write_bytes(b"png")
            old = {
                "status": "continue",
                "gatekeeper_decision": "CONTINUE",
                "defect_summary": {"remaining": 99},
                "artifact_manifest": {
                    "freshness": {"status": "pass"},
                    "artifacts": {"page_images": {"exists": True, "count": 9}},
                },
                "runtime_actions": {"repair_plan_executor": {"planned_candidates": 30}},
            }
            latest = {
                "status": "continue",
                "gatekeeper_decision": "CONTINUE",
                "defect_summary": {"remaining": 4},
                "artifact_manifest": {
                    "freshness": {"status": "pass"},
                    "artifacts": {"page_images": {"exists": True, "count": 1}},
                },
                "runtime_actions": {
                    "compile": {"success": True},
                    "render_pages": {"success": True},
                    "repair_plan_executor": {"planned_candidates": 2},
                },
            }
            old_path = data / "run_result_old.json"
            latest_path = data / "run_result_latest.json"
            old_path.write_text(json.dumps(old), encoding="utf-8")
            latest_path.write_text(json.dumps(latest), encoding="utf-8")
            old_path.touch()
            latest_path.touch()
            old_path.chmod(0o600)
            latest_path.chmod(0o600)
            old_path.touch()
            import os
            os.utime(old_path, (1, 1))
            os.utime(latest_path, (2, 2))

            evidence = _case_evidence(root)

            self.assertEqual(evidence["evidence_mode"], "latest_run")
            self.assertEqual(evidence["quality"]["remaining_defects"], 4)
            self.assertEqual(evidence["quality"]["repair_candidates"], 2)
            self.assertEqual(evidence["quality"]["page_images_count"], 1)


if __name__ == "__main__":
    unittest.main()
