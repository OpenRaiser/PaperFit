"""Regression coverage for global VLM credential/quota rejection and partial evidence."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vlm_diagnosis import VLMTransportError, _default_client, run_vlm_diagnosis


def completion(defects=None, *, finish_reason="stop"):
    return io.BytesIO(json.dumps({
        "model": "deepseek-flash",
        "choices": [{"finish_reason": finish_reason,
                     "message": {"content": json.dumps({"defects": defects or []})}}],
    }).encode())


def rejection(status, error=None, *, body=None):
    payload = body if body is not None else json.dumps({"error": error or {}}).encode()
    return HTTPError("https://api.deepseek.com/chat/completions", status,
                     "remote reason must not be logged", {}, io.BytesIO(payload))


class NonRetryableTransportTest(unittest.TestCase):
    def run_pages(self, outcomes):
        with tempfile.TemporaryDirectory() as directory:
            for page in range(1, 7):
                with Image.new("RGB", (32, 48), "white") as image:
                    image.save(Path(directory) / ("page_%03d.png" % page))
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-only-fake-key",
                                        "PAPERFIT_VLM_DIRECT_FALLBACK": "1"}, clear=True), \
                    patch("vlm_diagnosis.urlopen", side_effect=outcomes) as post, \
                    patch("vlm_diagnosis.build_opener") as alternate:
                findings, report = run_vlm_diagnosis(Path(directory), pages_per_batch=2,
                    batch_overlap=0, detail_crops=False, max_image_dim=128)
            alternate.assert_not_called()
            self.assertTrue(all(call.args[0].get_method() == "POST" for call in post.call_args_list))
            return findings, report, post.call_count

    def test_401_stops_after_first_post_and_keeps_every_page_missing(self):
        findings, report, calls = self.run_pages([rejection(401), completion(), completion()])
        self.assertEqual(calls, 1)
        self.assertEqual(findings, [])
        self.assertEqual(report["status"], "failed")
        self.assertFalse(report["success"])
        self.assertIsNone(report["clean"])
        self.assertEqual(report["coverage"], 0.0)
        self.assertEqual(report["missing_page_numbers"], list(range(1, 7)))
        self.assertEqual(report["transport_failure"]["status_code"], 401)
        self.assertTrue(report["transport_failure"]["non_retryable"])
        self.assertEqual(report["stopped_reason"], "non_retryable_transport_failure")

    def test_402_recognizes_balance_reason_without_logging_remote_text(self):
        remote = {"message": "Insufficient Balance", "debug": "test-only-fake-key"}
        findings, report, calls = self.run_pages([rejection(402, remote), completion()])
        self.assertEqual(calls, 1)
        self.assertEqual(report["transport_failure"]["response_error_code"], "insufficient_balance")
        self.assertEqual(report["transport_failure"]["error_kind"], "payment_or_quota")
        self.assertNotIn("test-only-fake-key", json.dumps(report))
        self.assertNotIn("debug", json.dumps(report))
        self.assertEqual(report["status"], "failed")
        self.assertIsNone(report["clean"])

    def test_success_then_402_preserves_findings_coverage_and_partial_status(self):
        defect = {"code": "D2", "page": 1, "severity": "degrading", "confidence": 0.8,
                  "evidence": "The displayed formula visibly protrudes past the column."}
        findings, report, calls = self.run_pages([
            completion([defect]), rejection(402, {"code": "insufficient_quota"}), completion()])
        self.assertEqual(calls, 2)
        self.assertEqual(len(findings), 1)
        self.assertEqual(report["status"], "partial")
        self.assertFalse(report["success"])
        self.assertFalse(report["clean"])
        self.assertEqual(report["analyzed_page_numbers"], [1, 2])
        self.assertEqual(report["missing_page_numbers"], [3, 4, 5, 6])
        self.assertEqual(report["coverage"], 0.3333)
        self.assertEqual([b["status"] for b in report["batches"]], ["completed", "failed"])

    def test_known_403_permission_rejection_stops_document_requests(self):
        _, report, calls = self.run_pages([
            rejection(403, {"code": "permission_denied"}), completion(), completion()])
        self.assertEqual(calls, 1)
        self.assertEqual(report["transport_failure"]["error_kind"], "permission")
        self.assertTrue(report["transport_failure"]["non_retryable"])

    def test_unknown_403_is_only_a_failed_batch_and_later_pages_are_inspected(self):
        _, report, calls = self.run_pages([
            rejection(403, {"code": "content_policy_violation"}), completion(), completion()])
        self.assertEqual(calls, 3)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["analyzed_page_numbers"], [3, 4, 5, 6])
        self.assertEqual(report["missing_page_numbers"], [1, 2])
        self.assertFalse(report["transport_failure"]["non_retryable"])
        self.assertNotIn("stopped_reason", report)
        self.assertIsNone(report["clean"])

    def test_5xx_keeps_existing_bounded_batch_behavior(self):
        _, report, calls = self.run_pages([
            rejection(503, body=b"remote secret must stay private"), completion(), completion()])
        self.assertEqual(calls, 3)
        self.assertEqual(report["status"], "partial")
        self.assertFalse(report["transport_failure"]["non_retryable"])
        self.assertNotIn("remote secret", json.dumps(report))
        self.assertNotIn("stopped_reason", report)

    def test_timeout_keeps_existing_batch_behavior_and_never_switches_route(self):
        _, report, calls = self.run_pages([
            URLError(TimeoutError("timed out")), completion(), completion()])
        self.assertEqual(calls, 3)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["analyzed_page_numbers"], [3, 4, 5, 6])
        self.assertNotIn("stopped_reason", report)

    def test_truncation_is_not_clean_and_does_not_trigger_account_abort(self):
        _, report, calls = self.run_pages([
            completion(finish_reason="length"), completion(), completion()])
        self.assertEqual(calls, 3)
        self.assertEqual(report["status"], "partial")
        self.assertIsNone(report["clean"])
        self.assertEqual(report["missing_page_numbers"], [1, 2])
        self.assertNotIn("transport_failure", report)

    def test_http_body_is_bounded_and_exception_remains_runtime_error(self):
        class RecordingBody(io.BytesIO):
            def __init__(self):
                super().__init__(b"x" * 20000)
                self.read_sizes = []
            def read(self, size=-1):
                self.read_sizes.append(size)
                return super().read(size)
        body = RecordingBody()
        error = HTTPError("https://api.deepseek.com/chat/completions", 402,
                          "private remote reason", {}, body)
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-only-fake-key"}, clear=True), \
                patch("vlm_diagnosis.urlopen", side_effect=error):
            with self.assertRaises(RuntimeError) as caught:
                _default_client().analyze(images=[], system_prompt="JSON", user_prompt="Inspect")
        self.assertIsInstance(caught.exception, VLMTransportError)
        self.assertEqual(body.read_sizes, [8192])
        self.assertIsNone(caught.exception.response_error_code)
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(caught.exception.as_report()["status_code"], 402)

    def test_unknown_response_codes_and_echoed_credentials_are_never_preserved(self):
        body = {"code": "test-only-fake-key", "type": "test-only-fake-key",
                "message": "test-only-fake-key"}
        _, report, calls = self.run_pages([rejection(402, body), completion()])
        self.assertEqual(calls, 1)
        self.assertIsNone(report["transport_failure"]["response_error_code"])
        self.assertNotIn("test-only-fake-key", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
