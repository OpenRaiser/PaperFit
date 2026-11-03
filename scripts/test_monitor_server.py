from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from scripts.monitor_server import _case_evidence, MonitorRequestHandler


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


class MonitorFileApiWhitelistTest(unittest.TestCase):
    def _with_server(self, project_root: Path):
        server = ThreadingHTTPServer(("127.0.0.1", 0), MonitorRequestHandler)
        server.project_root = project_root  # type: ignore[attr-defined]
        server.benchmark_root = None  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_address[1]}"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_page_images_served_and_other_files_forbidden(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pages = root / "data" / "pages"
            pages.mkdir(parents=True)
            (pages / "page_001.png").write_bytes(b"png")
            (root / "paper.tex").write_text("secret source", encoding="utf-8")
            git_dir = root / ".git"
            git_dir.mkdir()
            (git_dir / "config").write_text("[core]", encoding="utf-8")

            for url in self._with_server(root):
                image = urllib.request.urlopen(
                    f"{url}/api/file?path=data/pages/page_001.png", timeout=10
                )
                self.assertEqual(image.status, 200)
                self.assertEqual(image.read(), b"png")
                image.close()

                for forbidden in ("paper.tex", ".git/config"):
                    with self.assertRaises(urllib.error.HTTPError) as ctx:
                        urllib.request.urlopen(
                            f"{url}/api/file?path={forbidden}", timeout=10
                        )
                    self.assertEqual(ctx.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
