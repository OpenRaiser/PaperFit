from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

from scripts.runtime_artifacts import collect_artifact_manifest
from scripts.runtime_status import build_runtime_status
from scripts.pdf_test_support import write_test_pdf, write_test_png


class RuntimeStatusRevalidationTest(unittest.TestCase):
    def _checked_project(self, root):
        (root / "data/pages").mkdir(parents=True)
        files = {
            "main.tex": "\\input{section}\n",
            "section.tex": "Original content",
            "main.pdf": "pdf fixture",
            "data/pages/page_001.png": "page fixture",
            "data/visual_signal_report.json": '{"summary":{"pages_analyzed":1}}',
            "data/defect_report.json": '{"defects":[]}',
            "data/gatekeeper_decision.json": '{"decision":"DONE","remaining_defects":[]}',
        }
        for index, (name, text) in enumerate(files.items()):
            path = root / name
            if name == "main.pdf":
                write_test_pdf(path)
            elif name.endswith(".png"):
                write_test_png(path)
            else:
                path.write_text(text, encoding="utf-8")
            os.utime(path, (1700000000 + index, 1700000000 + index))
        artifacts = {
            "page_images_dir": "data/pages",
            "visual_signal_report": "data/visual_signal_report.json",
            "defect_report": "data/defect_report.json",
            "gatekeeper_decision": "data/gatekeeper_decision.json",
        }
        manifest = collect_artifact_manifest(root, "main.tex", artifacts)
        self.assertEqual(manifest["freshness"]["status"], "pass")
        state = {
            "main_tex": "main.tex", "status": "DONE", "last_gatekeeper_decision": "DONE",
            "task": {"type": "visual_only"}, "artifacts": artifacts,
            "content_integrity": {"validation_status": "mutation_reported"},
        }
        (root / "data/state.json").write_text(json.dumps(state), encoding="utf-8")
        (root / "data/run_result_check_visual.json").write_text(json.dumps({
            "status": "done", "gatekeeper_decision": "DONE", "artifact_manifest": manifest,
        }), encoding="utf-8")

    def test_host_edit_invalidates_checked_status_without_mutating_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._checked_project(root)
            before = (root / "data/state.json").read_bytes()
            self.assertEqual(build_runtime_status(project_root=root)["status"], "DONE")
            (root / "section.tex").write_text("Host changed the included section", encoding="utf-8")
            for explicit in (None, "data/run_result_check_visual.json"):
                status = build_runtime_status(project_root=root, run_result_path=explicit)
                self.assertEqual(status["status"], "EVALUATING")
                self.assertEqual(status["gatekeeper_decision"], "DONE")
                self.assertEqual(status["artifact_freshness"]["status"], "stale_or_missing")
                self.assertIn("source_set", status["artifact_freshness"]["changed_artifacts"])
                self.assertEqual(status["content_integrity"]["validation_status"], "revalidation_required")
                self.assertEqual(status["content_integrity"]["recorded_validation_status"], "mutation_reported")
                self.assertEqual(status["status_consistency"]["reason"], "current_artifacts_require_revalidation")
                self.assertTrue(status["next_actions"])
            self.assertEqual((root / "data/state.json").read_bytes(), before)

    def test_content_hash_detects_edit_even_when_mtime_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._checked_project(root)
            source = root / "section.tex"
            original_time = source.stat().st_mtime
            source.write_text("Changed with preserved timestamp", encoding="utf-8")
            os.utime(source, (original_time, original_time))
            status = build_runtime_status(project_root=root)
            self.assertEqual(status["status"], "EVALUATING")
            self.assertIn("artifacts_match_checked_run", status["artifact_freshness"]["blocking_checks"])

    def test_replaced_or_missing_visual_artifacts_invalidate_done(self):
        for artifact in ("main.pdf", "data/pages/page_001.png", "data/visual_signal_report.json"):
            for deleted in (False, True):
                with self.subTest(artifact=artifact, deleted=deleted), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    self._checked_project(root)
                    path = root / artifact
                    if deleted:
                        path.unlink()
                    else:
                        path.write_text('{}' if artifact.endswith('.json') else 'replacement', encoding="utf-8")
                    status = build_runtime_status(project_root=root)
                    self.assertEqual(status["status"], "EVALUATING")
                    self.assertEqual(status["artifact_freshness"]["status"], "stale_or_missing")


if __name__ == "__main__":
    unittest.main()
