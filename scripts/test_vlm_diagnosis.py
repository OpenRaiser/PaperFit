"""VLM 在环诊断测试：解析、合并、去重、未配置时安静跳过。"""

from __future__ import annotations

import json
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from vlm_diagnosis import (  # noqa: E402
    DEFECT_VOCABULARY,
    build_user_prompt,
    merge_vlm_findings,
    parse_vlm_defects,
    run_vlm_diagnosis,
    vlm_diagnosis_configured,
    VLMResponseError,
    _default_client,
    _resolve_vlm_config,
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


class _SequenceClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def analyze(self, *, images, system_prompt, user_prompt):
        self.requests.append({"sizes": [image.size for image in images], "prompt": user_prompt})
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response if isinstance(response, tuple) else (response, {"model": "fake", "finish_reason": "stop"})


def _defect(page=1, code="D1", **updates):
    defect = {"code": code, "page": page, "severity": "degrading", "confidence": 0.85,
              "evidence": "A line extends beyond the right body-text margin."}
    defect.update(updates)
    return defect


class StrictResponseTest(unittest.TestCase):
    def test_malformed_or_incomplete_response_is_an_error(self):
        for text in ("", "not json", "{}", '{"defects": null}', '{"defects": {}}',
                     '{"defects": [null]}', 'I could not read the images. {"defects": []}'):
            with self.subTest(text=text), self.assertRaises(VLMResponseError):
                parse_vlm_defects(text, strict=True, page_numbers=[1])

    def test_empty_defects_cannot_override_error_or_status_fields(self):
        for fields in ({"error": "unable to inspect images"},
                       {"status": "failed"}, {"status": "completed"}):
            response = json.dumps({"defects": [], **fields})
            with self.subTest(fields=fields), self.assertRaises(VLMResponseError):
                parse_vlm_defects(response, strict=True, page_numbers=[1])

    def test_duplicate_json_fields_cannot_discard_findings_or_rewrite_pages(self):
        defect = json.dumps(_defect())
        responses = ('{"defects": [' + defect + '], "defects": []}',
                     '{"defects": [{"page": 9, ' + defect[1:] + ']}')
        for response in responses:
            with self.subTest(response=response), self.assertRaisesRegex(VLMResponseError, "duplicate"):
                parse_vlm_defects(response, strict=True, page_numbers=[1])

    def test_canonical_empty_response_remains_valid(self):
        for response in ('{"defects": []}', '```json\n{"defects": []}\n```'):
            with self.subTest(response=response):
                self.assertEqual(parse_vlm_defects(response, strict=True, page_numbers=[1]), [])

    def test_invalid_fields_are_not_silently_dropped_or_coerced(self):
        invalid = ({"page": 0}, {"page": True}, {"page": "1"}, {"page": 2},
                   {"code": "ZZ"}, {"confidence": -0.1}, {"confidence": 1.1},
                   {"confidence": float("nan")}, {"confidence": True},
                   {"severity": "whatever"}, {"evidence": ""}, {"caption_number": True})
        for fields in invalid:
            with self.subTest(fields=fields), self.assertRaises(VLMResponseError):
                parse_vlm_defects(json.dumps({"defects": [_defect(**fields)]}), strict=True, page_numbers=[1])

    def test_zero_confidence_and_caption_anchor_survive(self):
        response = json.dumps({"defects": [_defect(code="C2", caption_number=4, confidence=0)]})
        finding = parse_vlm_defects(response, strict=True, page_numbers=[1])[0]
        self.assertEqual(finding["confidence"], 0)
        self.assertEqual(finding["metrics"]["caption_number"], 4)
        self.assertEqual(finding["metrics"]["object_kind"], "table")

    def test_b2_width_subtypes_survive_strict_parsing(self):
        for subtype in ("overflow_width", "underfilled_width"):
            with self.subTest(subtype=subtype):
                response = json.dumps({"defects": [_defect(code="B2", width_subtype=subtype)]})
                finding = parse_vlm_defects(response, strict=True, page_numbers=[1])[0]
                self.assertEqual(finding["metrics"]["subtype"], subtype)
                self.assertEqual(finding["metrics"]["object_kind"], "figure")

    def test_b2_unknown_width_subtype_is_rejected(self):
        for subtype in ("unknown", "", None, [], "overflow"):
            with self.subTest(subtype=subtype), self.assertRaises(VLMResponseError):
                parse_vlm_defects(json.dumps({"defects": [_defect(code="B2", width_subtype=subtype)]}),
                                  strict=True, page_numbers=[1])

    def test_b2_omitted_width_subtype_remains_untyped(self):
        finding = parse_vlm_defects(json.dumps({"defects": [_defect(code="B2")]}),
                                    strict=True, page_numbers=[1])[0]
        self.assertNotIn("subtype", finding["metrics"])


class BatchDiagnosisTest(unittest.TestCase):
    def _pages(self, directory, numbers, size=(1700, 2400), padded=True):
        for number in numbers:
            name = f"page_{number:03d}.png" if padded else f"page_{number}.png"
            with Image.new("RGB", size, "white") as image:
                image.save(Path(directory) / name)

    def test_long_paper_keeps_individual_page_resolution_and_adjacent_context(self):
        client = _SequenceClient(['{"defects": []}'] * 9)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            self._pages(directory, range(1, 11), padded=False)
            findings, report = run_vlm_diagnosis(Path(directory), client=client)
        self.assertEqual(findings, [])
        self.assertEqual(report["status"], "completed", report)
        self.assertTrue(report["clean"])
        self.assertEqual(report["coverage"], 1)
        self.assertEqual([batch["page_numbers"] for batch in report["batches"]],
                         [[p, p + 1] for p in range(1, 10)])
        self.assertTrue(all(request["sizes"] == [(1700, 2400), (1700, 2400)] for request in client.requests))
        self.assertIn("physical PDF page 10", client.requests[-1]["prompt"])
        self.assertEqual(report["pages_analyzed"], 10)

    def test_double_column_crops_are_mapped_to_original_pages(self):
        client = _SequenceClient(['{"defects": []}'])
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            self._pages(directory, [1, 2])
            _, report = run_vlm_diagnosis(Path(directory), client=client, column_type="double")
        self.assertEqual(len(client.requests[0]["sizes"]), 6)
        self.assertEqual([view["page"] for view in report["batches"][0]["image_views"]], [1, 1, 1, 2, 2, 2])
        self.assertIn("crop edge is NOT a document margin", client.requests[0]["prompt"])
        self.assertIn("Image 4: physical PDF page 2", client.requests[0]["prompt"])

    def test_batch_budget_and_selection_cannot_claim_whole_document_clean(self):
        for settings in ({"max_batches": 1}, {"page_numbers": [2, 4]}):
            client = _SequenceClient(['{"defects": []}'])
            with self.subTest(settings=settings), tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
                self._pages(directory, [1, 2, 3, 4])
                _, report = run_vlm_diagnosis(Path(directory), client=client, **settings)
            self.assertEqual(report["status"], "partial")
            self.assertFalse(report["success"])
            self.assertIsNone(report["clean"])
            self.assertEqual(report["coverage"], 0.5)
            self.assertEqual(len(report["missing_page_numbers"]), 2)

    def test_bad_response_or_out_of_batch_page_is_failed_not_clean(self):
        for response in ('{"message": "invalid request"}', '{"defects": null}',
                         json.dumps({"defects": [_defect(page=9)]}),
                         ('{"defects": []}', {"finish_reason": "length"})):
            with self.subTest(response=response), tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
                self._pages(directory, [1])
                findings, report = run_vlm_diagnosis(Path(directory), client=_SequenceClient([response]))
            self.assertEqual(findings, [])
            self.assertEqual(report["status"], "failed")
            self.assertIsNone(report["clean"])
            self.assertEqual(report["pages_analyzed"], 0)
            self.assertTrue(report["error"])

    def test_runtime_requires_unambiguous_empty_success_response(self):
        responses = (
            (json.dumps({"defects": [], "error": "unable to inspect images"}), False),
            (json.dumps({"defects": [], "status": "failed"}), False),
            ('{"defects": [' + json.dumps(_defect()) + '], "defects": []}', False),
            ('{"defects": []}', True),
            ('```json\n{"defects": []}\n```', True),
        )
        for response, valid in responses:
            with self.subTest(response=response), tempfile.TemporaryDirectory() as directory, \
                    patch.dict(os.environ, {}, clear=True), \
                    patch("vlm_diagnosis.urlopen", side_effect=AssertionError("offline test forbids API calls")):
                self._pages(directory, [1], size=(50, 70))
                client = _SequenceClient([response])
                findings, report = run_vlm_diagnosis(Path(directory), client=client)
            self.assertEqual(len(client.requests), 1)
            self.assertEqual(findings, [])
            self.assertTrue(report["configured"])
            self.assertEqual(report["status"], "completed" if valid else "failed")
            self.assertEqual(report["success"], valid)
            self.assertIs(report["clean"], True if valid else None)
            self.assertEqual(report["coverage"], 1.0 if valid else 0.0)
            self.assertEqual(report["missing_page_numbers"], [] if valid else [1])
            self.assertEqual(report["batches"][0]["status"], "completed" if valid else "failed")
            self.assertEqual(report["error"] is None, valid)

    def test_partial_failure_retains_valid_evidence_without_repeating_findings(self):
        good = json.dumps({"defects": [_defect(page=2, code="B2", caption_number=3)]})
        client = _SequenceClient([good, good, RuntimeError("offline")])
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            self._pages(directory, [1, 2, 3, 4])
            findings, report = run_vlm_diagnosis(Path(directory), client=client)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["defect_id"], "vf-vlm-001")
        self.assertEqual(report["status"], "partial")
        self.assertFalse(report["clean"])
        self.assertEqual(report["coverage"], 0.75)
        self.assertEqual(report["missing_page_numbers"], [4])

    def test_render_gaps_and_duplicate_page_numbers_are_visible(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            self._pages(directory, [1, 3])
            _, report = run_vlm_diagnosis(Path(directory), client=_SequenceClient(['{"defects": []}']))
            self.assertEqual(report["missing_page_numbers"], [2])
            self.assertEqual(report["status"], "partial")
            self._pages(directory, [1], padded=False)
            _, report = run_vlm_diagnosis(Path(directory), client=_FakeClient('{"defects": []}'))
            self.assertEqual(report["status"], "failed")
            self.assertIn("duplicate rendered page", report["error"])

    def test_environment_can_disable_crops_and_limit_batches(self):
        client = _SequenceClient(['{"defects": []}'])
        settings = {"PAPERFIT_VLM_DETAIL_CROPS": "false", "PAPERFIT_VLM_PAGES_PER_BATCH": "3",
                    "PAPERFIT_VLM_MAX_BATCHES": "1", "PAPERFIT_VLM_MAX_IMAGE_DIM": "1200"}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, settings, clear=True):
            self._pages(directory, [1, 2, 3, 4])
            _, report = run_vlm_diagnosis(Path(directory), client=client, column_type="double")
        self.assertEqual(len(client.requests[0]["sizes"]), 3)
        self.assertTrue(all(max(size) == 1200 for size in client.requests[0]["sizes"]))
        self.assertEqual(report["status"], "partial")

    def test_error_report_redacts_key_and_image_data(self):
        key = "sensitive-test-credential"
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"DEEPSEEK_API_KEY": key}, clear=True):
            self._pages(directory, [1])
            _, report = run_vlm_diagnosis(Path(directory), client=_SequenceClient([
                RuntimeError(key + " data:image/png;base64,AAABBBCCC=")]))
        serialized = json.dumps(report)
        self.assertNotIn(key, serialized)
        self.assertNotIn("AAABBBCCC", serialized)

    def test_batch_persists_model_and_only_safe_numeric_usage(self):
        metadata = {"model": "deepseek-flash", "finish_reason": "stop", "usage": {
            "prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25,
            "prompt_cache_hit_tokens": True, "prompt_cache_miss_tokens": -1,
            "api_key": "do-not-store", "completion_tokens_details": {"private": "ignored"},
        }}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            self._pages(directory, [1], size=(50, 70))
            _, report = run_vlm_diagnosis(Path(directory), client=_SequenceClient([
                ('{"defects": []}', metadata)]))
        batch = report["batches"][0]
        self.assertEqual(batch["response_model"], "deepseek-flash")
        self.assertEqual(batch["usage"], {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25})
        self.assertNotIn("do-not-store", json.dumps(report))


class ConfigAndTransportTest(unittest.TestCase):
    def test_deepseek_key_uses_official_vision_defaults(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=True):
            self.assertTrue(vlm_diagnosis_configured())
            config = _resolve_vlm_config()
            self.assertEqual(config["api_url"], "https://api.deepseek.com")
            self.assertEqual(config["model_name"], "deepseek-flash")

    def test_legacy_and_paperfit_configuration_precedence(self):
        settings = {"DEEPSEEK_API_KEY": "deepseek-key", "BASELINE_VLM_API_KEY": "legacy-key",
                    "BASELINE_VLM_API_URL": "https://legacy.example/v1", "BASELINE_VLM_MODEL_NAME": "legacy-model"}
        with patch.dict(os.environ, settings, clear=True):
            self.assertEqual(_resolve_vlm_config()["api_key"], "legacy-key")
            with patch.dict(os.environ, {"PAPERFIT_VLM_API_KEY": "paperfit-key",
                                         "PAPERFIT_VLM_BASE_URL": "https://paperfit.example/v1", "PAPERFIT_VLM_MODEL": "vision"}):
                config = _resolve_vlm_config()
                self.assertEqual(config["api_key"], "paperfit-key")
                self.assertEqual(config["api_url"], "https://paperfit.example/v1")
                self.assertEqual(config["model_name"], "vision")

    def test_incomplete_legacy_credentials_are_never_sent_to_default_endpoint(self):
        with patch.dict(os.environ, {"BASELINE_VLM_API_KEY": "legacy-key"}, clear=True):
            self.assertFalse(vlm_diagnosis_configured())

    def test_legacy_base_url_keeps_previous_v1_endpoint_normalization(self):
        settings = {"BASELINE_VLM_API_KEY": "legacy-key", "BASELINE_VLM_API_URL": "https://legacy.example/",
                    "BASELINE_VLM_MODEL_NAME": "legacy-model"}
        result = {"choices": [{"finish_reason": "stop", "message": {"content": '{"defects": []}'}}]}
        with patch.dict(os.environ, settings, clear=True), \
                patch("vlm_diagnosis.urlopen", return_value=io.BytesIO(json.dumps(result).encode())) as transport:
            _default_client().analyze(images=[], system_prompt="Return JSON", user_prompt="Inspect")
        self.assertEqual(transport.call_args[0][0].full_url, "https://legacy.example/v1/chat/completions")

    def test_standard_library_request_has_sharp_images_and_no_sdk_dependency(self):
        result = {"model": "deepseek-flash", "choices": [{"finish_reason": "stop",
                  "message": {"content": '{"defects": []}'}}], "usage": {"total_tokens": 10}}
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=True), \
                patch("vlm_diagnosis.urlopen", return_value=io.BytesIO(json.dumps(result).encode())) as transport:
            client = _default_client()
            with Image.new("RGB", (20, 30), "white") as image:
                text, metadata = client.analyze(images=[image], system_prompt="Return JSON", user_prompt="Inspect")
        request = transport.call_args[0][0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(transport.call_args[1]["timeout"], 240)
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        content = payload["messages"][1]["content"][1]["image_url"]
        self.assertEqual(content["detail"], "high")
        self.assertTrue(content["url"].startswith("data:image/png;base64,"))
        self.assertEqual(text, '{"defects": []}')
        self.assertEqual(metadata["finish_reason"], "stop")

    def test_http_error_does_not_expose_response_body(self):
        from urllib.error import HTTPError

        error = HTTPError("https://api.deepseek.com/chat/completions", 401,
                          "request included a secret", {}, io.BytesIO(b"secret echoed in remote body"))
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=True), \
                patch("vlm_diagnosis.urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "HTTP 401") as raised:
                _default_client().analyze(images=[], system_prompt="Return JSON", user_prompt="Inspect")
        self.assertNotIn("secret", str(raised.exception))

    def test_api_protocol_failures_are_not_clean_completions(self):
        results = ({"error": {"message": "unavailable"}},
                   {"choices": [{"message": {"content": '{"defects": []}'}}]},
                   {"choices": [{"finish_reason": "length", "message": {"content": '{"defects": []}'}}]},
                   {"choices": [{"finish_reason": "stop", "message": {"content": ""}}]},
                   {"choices": [{"finish_reason": "stop", "message": {"content": '{"defects": []}', "refusal": "refused"}}]})
        for result in results:
            with self.subTest(result=result), patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=True), \
                    patch("vlm_diagnosis.urlopen", return_value=io.BytesIO(json.dumps(result).encode())):
                with self.assertRaises(VLMResponseError):
                    _default_client().analyze(images=[], system_prompt="Return JSON", user_prompt="Inspect")


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

    def test_merge_preserves_failed_zero_findings_diagnosis(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._report(Path(directory))
            diagnosis = {"status": "failed", "success": False, "clean": None, "coverage": 0, "error": "HTTP 401"}
            self.assertEqual(merge_vlm_findings(path, [], diagnosis), 0)
            report = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(report["vlm_diagnosis"], diagnosis)
            self.assertEqual(report["summary"]["total_findings"], 1)
            self.assertNotIn("vlm_detector", report.get("machine_sources", []))

    def test_merge_real_machine_sources_mapping_preserves_cv_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._report(Path(directory))
            report = json.loads(path.read_text(encoding="utf-8"))
            provenance = {"cv_detector": {"enabled": True, "page_results_count": 8},
                          "pymupdf_native": {"available": True}}
            report["machine_sources"] = provenance
            path.write_text(json.dumps(report), encoding="utf-8")
            diagnosis = {"configured": True, "status": "completed", "coverage": 1.0, "clean": False}
            self.assertEqual(merge_vlm_findings(path, parse_vlm_defects(VALID_RESPONSE), diagnosis), 2)
            result = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(result["machine_sources"]["cv_detector"], provenance["cv_detector"])
            self.assertEqual(result["machine_sources"]["pymupdf_native"], provenance["pymupdf_native"])
            self.assertEqual(result["machine_sources"]["vlm_detector"]["findings_added"], 2)
            self.assertEqual(result["vlm_diagnosis"], diagnosis)

    def test_merge_explicit_legacy_source_list_remains_a_list(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._report(Path(directory))
            report = json.loads(path.read_text(encoding="utf-8"))
            report["machine_sources"] = ["cv_detector"]
            path.write_text(json.dumps(report), encoding="utf-8")
            merge_vlm_findings(path, parse_vlm_defects(VALID_RESPONSE))
            merge_vlm_findings(path, parse_vlm_defects(VALID_RESPONSE))
            result = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(result["machine_sources"], ["cv_detector", "vlm_detector"])

    def test_same_taxonomy_page_and_number_keep_different_object_kinds(self):
        figure = parse_vlm_defects(json.dumps({"defects": [_defect(code="B1", caption_number=1)]}))[0]
        table = json.loads(json.dumps(figure))
        figure["metrics"]["object_kind"] = "figure"
        table["metrics"]["object_kind"] = "table"
        table["defect_id"] = "vf-vlm-002"
        with tempfile.TemporaryDirectory() as directory:
            path = self._report(Path(directory))
            self.assertEqual(merge_vlm_findings(path, [figure, table]), 2)
            self.assertEqual(merge_vlm_findings(path, [figure, table]), 0)
            result = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual([finding["metrics"]["object_kind"] for finding in result["findings"][1:]],
                         ["figure", "table"])


class UnconfiguredTest(unittest.TestCase):
    def test_unconfigured_returns_empty(self):
        import os

        with patch.dict(os.environ, {}, clear=True):
            findings, report = run_vlm_diagnosis(Path("does/not/matter"))
            self.assertEqual(findings, [])
            self.assertFalse(report["configured"])
            self.assertEqual(report["status"], "skipped")
            self.assertIsNone(report["clean"])


if __name__ == "__main__":
    unittest.main()
