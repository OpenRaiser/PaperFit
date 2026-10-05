"""VLM 在环诊断测试：解析、合并、去重、未配置时安静跳过。"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from vlm_diagnosis import (  # noqa: E402
    DEFECT_VOCABULARY,
    build_user_prompt,
    merge_vlm_findings,
    parse_vlm_defects,
    run_vlm_diagnosis,
)


VALID_RESPONSE = """```json
{"defects": [
  {"code": "B2", "page": 5, "severity": "degrading", "caption_number": 3,
   "confidence": 0.9, "evidence": "Figure 3 extends past the right column boundary."},
  {"code": "A2", "page": 14, "severity": "cosmetic", "confidence": 0.7,
   "evidence": "Large blank area before the bibliography."},
  {"code": "ZZ", "page": 3, "severity": "degrading", "confidence": 0.9, "evidence": "invalid code must be dropped"},
  {"code": "D1", "page": 0, "severity": "degrading", "confidence": 0.9, "evidence": "page 0 must be dropped"}
]}
```"""


class ParseTest(unittest.TestCase):
    def test_valid_defects_parsed_with_schema(self):
        findings = parse_vlm_defects(VALID_RESPONSE)
        self.assertEqual(len(findings), 2)
        b2 = findings[0]
        self.assertEqual(b2["taxonomy_defect_id"], "B2")
        self.assertEqual(b2["severity"], "major")  # degrading → major
        self.assertEqual(b2["source"], "vlm_detector")
        self.assertEqual(b2["metrics"]["caption_number"], 3)
        self.assertEqual(findings[1]["taxonomy_defect_id"], "A2")
        self.assertEqual(findings[1]["severity"], "minor")  # cosmetic → minor

    def test_invalid_json_returns_empty(self):
        self.assertEqual(parse_vlm_defects("not json at all"), [])
        self.assertEqual(parse_vlm_defects(""), [])

    def test_empty_defects(self):
        self.assertEqual(parse_vlm_defects('{"defects": []}'), [])


class PromptTest(unittest.TestCase):
    def test_prompt_contains_full_vocabulary(self):
        prompt = build_user_prompt(template_name="ICLR", column_type="single", page_count=12)
        for code in DEFECT_VOCABULARY:
            self.assertIn(code, prompt)
        self.assertIn("caption_number", prompt)


class _FakeClient:
    def __init__(self, text: str):
        self.text = text
        self.calls = 0

    def analyze(self, *, images, system_prompt, user_prompt):
        self.calls += 1
        return self.text, {"model": "fake"}


class MergeTest(unittest.TestCase):
    def _report(self, tmpdir: Path) -> Path:
        report = {
            "summary": {"pages_analyzed": 8, "total_findings": 1, "category_breakdown": {"A": 1}},
            "findings": [
                {
                    "defect_id": "vf-001",
                    "taxonomy_defect_id": "A1",
                    "severity": "major",
                    "page": 3,
                    "metrics": {},
                }
            ],
        }
        path = tmpdir / "visual_signal_report.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        return path

    def test_merge_appends_and_updates_summary(self):
        import tempfile

        findings = parse_vlm_defects(VALID_RESPONSE)
        with tempfile.TemporaryDirectory() as td:
            path = self._report(Path(td))
            added = merge_vlm_findings(path, findings, {"findings_count": 2})
            self.assertEqual(added, 2)
            report = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(report["findings"]), 3)
            self.assertEqual(report["summary"]["total_findings"], 3)
            self.assertEqual(report["summary"]["category_breakdown"]["B"], 1)
            self.assertIn("vlm_detector", report["machine_sources"])

    def test_merge_dedupes_same_defect(self):
        import tempfile

        findings = parse_vlm_defects(VALID_RESPONSE)
        with tempfile.TemporaryDirectory() as td:
            path = self._report(Path(td))
            self.assertEqual(merge_vlm_findings(path, findings), 2)
            self.assertEqual(merge_vlm_findings(path, findings), 0)  # 全部重复
            report = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(report["findings"]), 3)

    def test_merge_empty_is_noop(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            path = self._report(Path(td))
            self.assertEqual(merge_vlm_findings(path, []), 0)


class UnconfiguredTest(unittest.TestCase):
    def test_unconfigured_returns_empty(self):
        import os

        saved = {k: os.environ.pop(k, None) for k in ("BASELINE_VLM_API_KEY", "BASELINE_VLM_API_URL", "BASELINE_VLM_MODEL_NAME")}
        try:
            findings, report = run_vlm_diagnosis(Path("does/not/matter"))
            self.assertEqual(findings, [])
            self.assertFalse(report["configured"])
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
