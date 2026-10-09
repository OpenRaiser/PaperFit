"""Late byte-binding barriers and local font review; synthetic receipts only.

No compiler, model API, manuscript or retained experiment is used.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import compile_inputs
import layout_policy
import template_parameter_review
from source_review import validate_source_review
from test_layout_policy_host_review import CurrentProject, TABLE, TABULAR
from test_template_parameter_source_review import TemplateParameterSourceReviewTest


def mutate(path):
    previous = path.stat()
    path.write_bytes(path.read_bytes() + b"\n% synthetic late mutation\n")
    os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns))


class ReviewIdentityBarrierTest(unittest.TestCase):
    def test_collector_final_probe_mutations_make_existing_review_unavailable(self):
        for target in ("source", "pdf", "page", "imports", "journal", "recorder", "new_page", "source_database"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                p = CurrentProject(Path(directory), r"\newpage")
                p.add_resources()
                request = p.signed()
                real = compile_inputs.live_compile_inputs

                def late(*args, **kwargs):
                    value = real(*args, **kwargs)
                    if target == "new_page":
                        (p.pages / "page_003.png").write_bytes((p.pages / "page_001.png").read_bytes())
                    else:
                        path = {"source": p.root / "main.tex", "pdf": p.pdf,
                                "page": p.pages / "page_001.png", "imports": p.root / "custom.cls",
                                "journal": p.journal, "recorder": p.recorder,
                                "source_database": p.root / "references.bib"}[target]
                        mutate(path)
                    return value

                with mock.patch("compile_inputs.live_compile_inputs", side_effect=late):
                    result = p.validate(request)
                self.assertEqual(result["status"], "manual_review", result)
                self.assertEqual(result["review_binding_status"], "unavailable", result)
                self.assertFalse(result["approved_operations"])

    def test_mutation_after_operation_validation_is_caught_before_policy_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            p = CurrentProject(Path(directory), r"\newpage")
            request = p.signed()
            real = layout_policy._approved

            def late(*args, **kwargs):
                result = real(*args, **kwargs)
                self.assertTrue(result[0])
                mutate(p.pdf)
                return result

            with mock.patch("layout_policy._approved", side_effect=late):
                result = p.validate(request)
            self.assertEqual(result["status"], "manual_review")
            self.assertFalse(result["approved_operations"])
            self.assertEqual(result["approval_evaluations"][0]["reason"], "evidence_changed_at_final_read_barrier")

    def test_unchanged_real_fixture_remains_available(self):
        with tempfile.TemporaryDirectory() as directory:
            p = CurrentProject(Path(directory), r"\newpage")
            self.assertEqual(p.validate(p.signed())["status"], "pass")

    def test_unreferenced_new_file_is_outside_native_recorder_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            p = CurrentProject(Path(directory), r"\newpage")
            request = p.signed()
            (p.root / "unused.tex").write_text("Unreferenced source fixture.", encoding="utf-8")
            self.assertEqual(p.validate(request)["status"], "pass")
            self.assertNotIn("unused.tex", {r["path"] for r in request["approvals"][0]["evidence"]["binding"]["source_set"]})


class SourceFinalBarrierTest(unittest.TestCase):
    setUp = TemplateParameterSourceReviewTest.setUp
    refresh = TemplateParameterSourceReviewTest.refresh
    request = TemplateParameterSourceReviewTest.request
    signed = TemplateParameterSourceReviewTest.signed
    decision = TemplateParameterSourceReviewTest.decision
    diff = TemplateParameterSourceReviewTest.diff
    def test_mutation_after_typed_template_verification_cannot_return_verified(self):
        real = template_parameter_review.verify_template_glue_evidence
        for target in ("main.tex", "main.pdf", "native.cls", "data/pages/page_001.png", "data/compile/journal.jsonl"):
            with self.subTest(target=target):
                # Each attack has its own complete native-style synthetic receipt.
                with tempfile.TemporaryDirectory() as directory:
                    p = CurrentProject(Path(directory), "")
                    template = p.root / "native.cls"
                    template.write_text(self.template.read_text(encoding="utf-8"), encoding="utf-8")
                    (p.root / "main.tex").write_text(self.new, encoding="utf-8")
                    p.retime(); os.utime(template, (1700000000, 1700000000))
                    from compile_result import source_fingerprint
                    from source_review import build_source_review_request
                    fp = source_fingerprint(p.root, "main.tex")
                    p.attempt.update(source_fingerprint=fp, source_fingerprint_before=dict(fp))
                    p.record_inputs(["main.tex", "references.bib", "native.cls"]); p.set_attempt(p.attempt)
                    request = build_source_review_request(self.old, self.new, project_root=p.root, file="main.tex", **p.context)
                    request.update(approved=True, source_reviewed=True, all_pages_inspected=True,
                                   content_preservation_confirmed=True, template_checked=True,
                                   reason="Synthetic test host assertion only.")
                    for item in request["template_parameter_evidence"]: item["template_checked"] = True

                    def late(*args, **kwargs):
                        value = real(*args, **kwargs)
                        self.assertEqual(value["status"], "verified")
                        mutate(p.root / target)
                        return value

                    with mock.patch("template_parameter_review.verify_template_glue_evidence", side_effect=late):
                        result = validate_source_review(self.old, self.new, project_root=p.root, file="main.tex", review=request)
                    self.assertEqual(result["status"], "manual_review", result)

    def test_constructed_visible_use_never_becomes_verified_from_host_booleans(self):
        self.old = self.old.replace("Result 91.2.", r"Result 91.2. Visible \csname ReferenceGap\endcsname.")
        self.refresh(self.new.replace("Result 91.2.", r"Result 91.2. Visible \csname ReferenceGap\endcsname."))
        signed = self.signed()
        self.assertEqual(self.decision(signed)["status"], "manual_review")
        self.assertNotEqual(self.diff(signed)["action_required"], "none")


class LocalFontReviewTest(unittest.TestCase):
    def test_positive_local_point_sizes_require_actual_host_measurement_context(self):
        for fontsize in ("4", "9"):
            for operation in (TABLE.replace(TABULAR, "\\fontsize{" + fontsize + r"}{11}\selectfont " + TABULAR),
                              r"\footnote{\fontsize{" + fontsize + r"}{11}\selectfont Retained 91.2.}"):
                with self.subTest(fontsize=fontsize, operation=operation), tempfile.TemporaryDirectory() as directory:
                    p = CurrentProject(Path(directory), operation)
                    request = p.signed()
                    self.assertTrue(request["approvals"][0]["can_host_approve"])
                    self.assertEqual(p.validate(request)["status"], "manual_review")
                    request["approvals"][0]["evidence"].update(
                        readability_confirmed=True,
                        measurement_context="Synthetic fixture local font/object, exact source and full current pages; not a manuscript readability finding.")
                    self.assertEqual(p.validate(request)["status"], "pass")

    def test_nested_font_reader_is_opaque_not_host_approvable(self):
        source = r"\def\outer{\def\fontsize#1#2{#1}}\outer" + TABLE.replace(TABULAR, r"\fontsize{4}{5}\selectfont " + TABULAR)
        report = layout_policy.compare_layout_policy("", source)
        self.assertEqual(report["status"], "manual_review")
        self.assertEqual(report["findings"][0]["rule"], "opaque_font_definition")


if __name__ == "__main__":
    unittest.main()
