"""Independent artifact-backed host review regressions; fixture compile receipts are synthetic.

No compiler, model API, manuscript source, or retained benchmark artifact is modified.
"""
from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pymupdf
from PIL import Image
from compile_result import source_fingerprint
from compile_inputs import bind_compile_inputs, snapshot_project_inputs
from layout_policy import (build_layout_review_request, collect_layout_review_binding,
                           compare_layout_policy)
from runtime_artifacts import collect_artifact_manifest

TABULAR = r"\begin{tabular}{lr}Method&Score\\A&91.2\\B&88.4\\\end{tabular}"
TABLE = r"\begin{table}\caption{Results}\label{tab:r}" + TABULAR + r"\end{table}"
BASE = '\\documentclass{article}\n\\begin{document}\nResult words.\n\\bibliography{references}\n\\end{document}\n'


def manuscript(operation: str) -> str:
    return BASE.replace("Result words.", operation + "\nResult words.")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CurrentProject:
    def __init__(self, root: Path, operation: str):
        self.root = root
        self.original = BASE
        self.repaired = manuscript(operation)
        (root / "main.tex").write_text(self.repaired, encoding="utf-8")
        (root / "baseline.before").write_text(self.original, encoding="utf-8")
        (root / "references.bib").write_text("@article{r,title={Retained}}\n", encoding="utf-8")
        self.pdf = root / "main.pdf"
        self.write_pdf("Synthetic test page")
        self.pages = root / "data/pages"
        self.pages.mkdir(parents=True)
        for number in (1, 2):
            Image.new("RGB", (30, 40), (245, 245, number)).save(self.pages / f"page_{number:03d}.png")
        self.journal = root / "data/compile/journal.jsonl"
        self.journal.parent.mkdir(parents=True)
        self.context = {"main_tex": "main.tex", "page_images_dir": "data/pages"}
        self.retime()
        fingerprint = source_fingerprint(root, "main.tex")
        self.attempt = {
            "schema_version": "compile_result.v2", "attempt_id": "synthetic-test-attempt",
            "main_tex": "main.tex", "pdf_path": str(self.pdf), "success": True,
            "returncode": 0, "timed_out": False, "pdf_fresh": True,
            "restored_baseline_pdf": False, "partial_pdf_available": False,
            "pdf_sha256": digest(self.pdf), "page_count": 2,
            "source_fingerprint": fingerprint, "source_fingerprint_before": dict(fingerprint),
            "source_unchanged_during_compile": True,
        }
        self.recorder = root / "main.fls"
        self.record_inputs(["main.tex", "references.bib"])
        self.set_attempt(self.attempt)

    def record_inputs(self, files):
        before = snapshot_project_inputs(self.root, self.root / "main.tex")
        self.recorder.write_text("PWD " + str(self.root) + "\n" + "".join("INPUT " + f + "\n" for f in files), encoding="utf-8")
        self.attempt["compile_inputs"] = bind_compile_inputs(self.root, self.root / "main.tex", before)

    def add_resources(self):
        Image.new("RGB", (10, 10), "blue").save(self.root / "figure.png")
        (self.root / "custom.cls").write_text(r"\LoadClass{article}", encoding="utf-8")
        (self.root / "localstyle.sty").write_text(r"\newcommand{\fixturestyle}{Original}", encoding="utf-8")
        for key in ("original", "repaired"):
            value = getattr(self, key).replace(r"\documentclass{article}", r"\documentclass{custom}\usepackage{localstyle}")
            setattr(self, key, value.replace("Result words.", r"\includegraphics{figure.png} Result words."))
        (self.root / "main.tex").write_text(self.repaired, encoding="utf-8")
        (self.root / "baseline.before").write_text(self.original, encoding="utf-8")
        self.retime()
        fp = source_fingerprint(self.root, "main.tex")
        self.attempt.update(source_fingerprint=fp, source_fingerprint_before=dict(fp))
        self.record_inputs(["main.tex", "references.bib", "figure.png", "custom.cls", "localstyle.sty"])
        self.set_attempt(self.attempt)

    def write_pdf(self, text: str):
        doc = pymupdf.open()
        for _ in range(2):
            page = doc.new_page()
            page.insert_text((30, 40), text)
        doc.save(self.pdf)
        doc.close()

    def retime(self):
        for path in (self.root / "main.tex", self.root / "references.bib"):
            os.utime(path, (1700000000, 1700000000))
        os.utime(self.pdf, (1700000010, 1700000010))
        for path in self.pages.glob("*.png"):
            os.utime(path, (1700000020, 1700000020))

    def set_attempt(self, attempt):
        self.journal.write_text(json.dumps(attempt) + "\n", encoding="utf-8")

    def request(self):
        return build_layout_review_request(self.original, self.repaired, project_root=self.root,
                                           file="main.tex", **self.context)

    def signed(self):
        request = self.request()
        for approval in request["approvals"]:
            approval["approved"] = True
            evidence = approval["evidence"]
            evidence.update(reason="Host inspected every current page and this exact operation.",
                            visual_inspected=True, all_pages_inspected=True)
            if "no_blank_padding" in evidence:
                evidence["no_blank_padding"] = True
            if "readability_confirmed" in evidence:
                evidence["readability_confirmed"] = True
        return request

    def validate(self, request):
        return compare_layout_policy(self.original, self.repaired, file="main.tex",
                                     approvals=request["approvals"], project_root=self.root,
                                     review_context=self.context)


class ArtifactBackedHostReviewTest(unittest.TestCase):
    def project(self, directory, operation=r"\newpage"):
        return CurrentProject(Path(directory), operation)

    def test_imported_graphic_class_and_style_changes_invalidate_existing_approval(self):
        for filename in ("figure.png", "custom.cls", "localstyle.sty"):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                p = self.project(directory)
                p.add_resources()
                request = p.signed()
                self.assertEqual(p.validate(request)["status"], "pass")
                path = p.root / filename
                previous = path.stat()
                if filename.endswith("png"):
                    Image.new("RGB", (10, 10), "red").save(path)
                else:
                    path.write_bytes(path.read_bytes() + b"\n% changed imported template")
                os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns))
                result = p.validate(request)
                self.assertEqual(result["status"], "manual_review")
                self.assertEqual(result["review_binding_status"], "unavailable")
                self.assertTrue(result["binding_blocking_checks"])

    def test_old_tex_only_receipt_cannot_approve_even_with_fresh_pdf_and_complete_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            p = self.project(directory)
            request = p.signed()
            del p.attempt["compile_inputs"]
            p.set_attempt(p.attempt)
            self.assertEqual(p.request()["binding_status"], "unavailable")
            result = p.validate(request)
            self.assertEqual(result["status"], "manual_review")
            self.assertIn("imported-input receipt", " ".join(result["binding_blocking_checks"]))

    def test_full_imported_input_map_and_recorder_must_match_receipt(self):
        for attack in ("drop_asset", "recorder_changed", "during_build", "missing_prebuild", "missing_actual_file"):
            with self.subTest(attack=attack), tempfile.TemporaryDirectory() as directory:
                p = self.project(directory)
                p.add_resources()
                request = p.signed()
                receipt = p.attempt["compile_inputs"]
                if attack == "drop_asset":
                    for phase in ("before", "after"):
                        del receipt[phase]["figure.png"]
                elif attack == "recorder_changed":
                    p.recorder.write_text(p.recorder.read_text(encoding="utf-8") + "INPUT new.pdf\n", encoding="utf-8")
                elif attack == "during_build":
                    receipt["before"]["figure.png"] = "0" * 64
                elif attack == "missing_prebuild":
                    receipt["bound"] = False
                    receipt["missing_prebuild_inputs"] = ["figure.png"]
                else:
                    (p.root / "figure.png").unlink()
                p.set_attempt(p.attempt)
                result = p.validate(request)
                self.assertEqual(result["status"], "manual_review", result)
                self.assertEqual(result["review_binding_status"], "unavailable")

    def test_empty_pwd_only_unrelated_and_other_main_recorders_cannot_bind_review(self):
        cases = ("empty", "pwd_only", "unrelated", "other_main", "no_pwd", "relative_pwd")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                p = self.project(directory)
                request = p.signed()
                (p.root / "other.tex").write_text(BASE, encoding="utf-8")
                before = snapshot_project_inputs(p.root, p.root / "main.tex")
                pwd = "PWD " + str(p.root) + "\n"
                text = {"empty": "", "pwd_only": pwd,
                        "unrelated": pwd + "INPUT references.bib\n",
                        "other_main": pwd + "INPUT other.tex\n",
                        "no_pwd": "INPUT main.tex\n",
                        "relative_pwd": "PWD .\nINPUT main.tex\n"}[case]
                p.recorder.write_text(text, encoding="utf-8")
                # A new fresh-looking receipt cannot repair an invalid actual recorder.
                p.attempt["compile_inputs"] = bind_compile_inputs(p.root, p.root / "main.tex", before)
                self.assertFalse(p.attempt["compile_inputs"]["bound"])
                self.assertTrue(p.attempt["compile_inputs"]["unavailable"])
                p.set_attempt(p.attempt)
                self.assertEqual(p.request()["binding_status"], "unavailable")
                self.assertEqual(p.validate(request)["status"], "manual_review")

    def test_request_is_unsigned_and_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            p = self.project(directory)
            paths = [p.root / "main.tex", p.pdf, p.journal, *p.pages.glob("*.png")]
            before = {str(path): digest(path) for path in paths}
            request = p.request()
            self.assertEqual(request["binding_status"], "available")
            self.assertTrue(all(a["approved"] is False for a in request["approvals"]))
            self.assertTrue(all(a["evidence"]["visual_inspected"] is False for a in request["approvals"]))
            self.assertEqual(p.validate(request)["status"], "manual_review")
            self.assertEqual(before, {str(path): digest(path) for path in paths})

    def test_safe_pagination_and_positive_scaling_default_to_review_then_exact_host_approval(self):
        for operation in [r"\newpage", r"\pagebreak[4]", r"\clearpage", r"\vspace{-0.3em}",
                          TABLE.replace(TABULAR, r"\resizebox{\linewidth}{!}{" + TABULAR + "}"),
                          r"\scalebox{0.9}{\includegraphics{plot.pdf}}"]:
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as directory:
                p = self.project(directory, operation)
                self.assertEqual(compare_layout_policy(BASE, p.repaired)["status"], "manual_review")
                result = p.validate(p.signed())
                self.assertEqual(result["status"], "pass", result)
                self.assertEqual(len(result["approved_operations"]), 1)
                self.assertTrue(result["approval_evaluations"][0]["approved"])

    def test_endmatter_old_outer_shape_is_compatible_only_with_complete_new_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            p = self.project(directory, r"\clearpage")
            request = p.signed()
            request["approvals"][0]["evidence"].update(scope="endmatter", endmatter_intrusion=True)
            self.assertEqual(p.validate(request)["status"], "pass")
            del request["approvals"][0]["evidence"]["endmatter_intrusion"]
            self.assertEqual(p.validate(request)["status"], "manual_review")

    def test_legacy_spacing_approval_is_not_a_pass(self):
        source = "\\vspace{-0.3em}\n"
        approval = {"approved": True, "file": "main.tex", "line": 1, "command": "vspace",
                    "arguments": ["-0.3em"], "evidence": {"source": "host_review", "reason": "old",
                    "scope": "local", "visual_inspected": True, "page_images": ["arbitrary.png"]}}
        result = compare_layout_policy("", source, file="main.tex", approvals=[approval])
        self.assertEqual(result["status"], "manual_review")
        self.assertFalse(result["approval_evaluations"][0]["approved"])

    def test_source_change_with_same_mtime_invalidates_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            p = self.project(directory)
            request = p.signed()
            p.repaired += "% later source edit\n"
            (p.root / "main.tex").write_text(p.repaired, encoding="utf-8")
            p.retime()
            result = p.validate(request)
            self.assertEqual(result["status"], "manual_review")
            self.assertIn("current sources differ", " ".join(result["binding_blocking_checks"]))

    def test_asset_change_outside_edited_operation_invalidates_full_source_set(self):
        with tempfile.TemporaryDirectory() as directory:
            p = self.project(directory)
            request = p.signed()
            (p.root / "references.bib").write_text("@article{r,title={Changed}}\n", encoding="utf-8")
            p.retime()
            self.assertEqual(p.validate(request)["status"], "manual_review")

    def test_source_inventory_missing_member_cannot_be_approved(self):
        with tempfile.TemporaryDirectory() as directory:
            p = self.project(directory)
            del p.attempt["source_fingerprint"]["references.bib"]
            del p.attempt["source_fingerprint_before"]["references.bib"]
            p.set_attempt(p.attempt)
            self.assertEqual(p.request()["binding_status"], "unavailable")

    def test_pdf_replaced_even_at_same_mtime_invalidates_recorded_build(self):
        with tempfile.TemporaryDirectory() as directory:
            p = self.project(directory)
            request = p.signed()
            p.pdf.unlink()
            p.write_pdf("Changed candidate PDF")
            p.retime()
            self.assertEqual(p.validate(request)["status"], "manual_review")

    def test_image_pixels_changed_with_same_mtime_invalidate_old_review(self):
        with tempfile.TemporaryDirectory() as directory:
            p = self.project(directory)
            request = p.signed()
            Image.new("RGB", (30, 40), "red").save(p.pages / "page_001.png")
            p.retime()
            result = p.validate(request)
            self.assertEqual(result["status"], "manual_review")
            self.assertEqual(result["approval_evaluations"][0]["reason"], "source_pdf_page_or_compile_binding_changed")

    def test_new_or_missing_pages_fail_complete_coverage(self):
        for operation in ("add", "delete"):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as directory:
                p = self.project(directory)
                if operation == "add":
                    Image.new("RGB", (30, 40), "blue").save(p.pages / "page_003.png")
                else:
                    (p.pages / "page_002.png").unlink()
                self.assertEqual(p.request()["binding_status"], "unavailable")
                self.assertIn("page_images_complete", " ".join(p.request()["blocking_checks"]))

    def test_invalid_image_cannot_authorize_host_review(self):
        with tempfile.TemporaryDirectory() as directory:
            p = self.project(directory)
            (p.pages / "page_001.png").write_bytes(b"not PNG data")
            self.assertEqual(p.request()["binding_status"], "unavailable")

    def test_latest_failed_or_malformed_compile_never_falls_back_to_previous_success(self):
        for latest in (json.dumps({"schema_version": "compile_result.v2", "success": False}), "{invalid}"):
            with self.subTest(latest=latest), tempfile.TemporaryDirectory() as directory:
                p = self.project(directory)
                p.journal.write_text(p.journal.read_text(encoding="utf-8") + latest + "\n", encoding="utf-8")
                self.assertEqual(p.request()["binding_status"], "unavailable")

    def test_legacy_journal_no_source_binding_is_pending_despite_companion_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            p = self.project(directory)
            p.attempt.pop("source_fingerprint")
            p.set_attempt(p.attempt)
            (p.journal.parent / "last_fingerprint.json").write_text(
                json.dumps(p.attempt["source_fingerprint_before"]), encoding="utf-8")
            request = p.request()
            self.assertEqual(request["binding_status"], "unavailable")
            self.assertIn("same-attempt source hashes", " ".join(request["blocking_checks"]))

    def test_changed_source_during_compile_and_old_prebuild_hash_fail(self):
        for change in ("flag", "before"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                p = self.project(directory)
                if change == "flag":
                    p.attempt["source_unchanged_during_compile"] = False
                else:
                    p.attempt["source_fingerprint_before"]["main.tex"] = "0" * 64
                p.set_attempt(p.attempt)
                self.assertEqual(p.request()["binding_status"], "unavailable")

    def test_actual_view_and_full_page_assertions_are_required(self):
        for change in ("visual_inspected", "all_pages_inspected", "source", "reason"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                p = self.project(directory)
                request = p.signed()
                request["approvals"][0]["evidence"][change] = "vlm" if change == "source" else "" if change == "reason" else False
                self.assertEqual(p.validate(request)["status"], "manual_review")

    def test_exact_operation_and_comparison_text_cannot_be_reused(self):
        for change in ("operation", "comparison"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                p = self.project(directory)
                request = p.signed()
                if change == "operation":
                    request["approvals"][0]["exact_operation"]["target"] = "another object"
                else:
                    p.repaired += "% passed string is not disk source\n"
                self.assertEqual(p.validate(request)["status"], "manual_review")

    def test_nonpositive_scaling_font_and_body_shrink_are_never_review_exceptions(self):
        for operation in [r"\scalebox{0}{" + TABULAR + "}", r"\scalebox{-1}{" + TABULAR + "}",
                          r"\scalebox{1}[0]{\includegraphics{plot.pdf}}",
                          r"\resizebox{0pt}{!}{" + TABULAR + "}",
                          r"\resizebox{-1\linewidth}{!}{" + TABULAR + "}",
                          r"\fontsize{10pt}{12pt}\selectfont Body words.",
                          TABLE.replace(TABULAR, r"\fontsize{0pt}{5pt}\selectfont " + TABULAR)]:
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as directory:
                p = self.project(directory, operation)
                request = p.signed()
                self.assertTrue(any(a["can_host_approve"] is False for a in request["approvals"]))
                self.assertEqual(p.validate(request)["status"], "blocked")

    def test_unknown_scaling_body_stays_pending_not_autoapproved(self):
        with tempfile.TemporaryDirectory() as directory:
            p = self.project(directory, r"\scalebox{1}{\unknownbody}")
            request = p.signed()
            self.assertFalse(request["approvals"][0]["can_host_approve"])
            self.assertEqual(p.validate(request)["status"], "manual_review")

    def test_known_positive_plus_factor_can_review_but_opaque_factor_cannot(self):
        for factor, expected in [("+0.9", "pass"), ("\\factor", "manual_review")]:
            with self.subTest(factor=factor), tempfile.TemporaryDirectory() as directory:
                p = self.project(directory, "\\scalebox{" + factor + "}{" + TABULAR + "}")
                request = p.signed()
                self.assertEqual(p.validate(request)["status"], expected)

    def test_freshness_and_canonical_compile_identity_are_required(self):
        for field, value in [("success", False), ("pdf_fresh", False), ("timed_out", True),
                             ("restored_baseline_pdf", True), ("partial_pdf_available", True),
                             ("returncode", 1), ("main_tex", "another.tex"),
                             ("pdf_sha256", "0" * 64), ("page_count", 1)]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                p = self.project(directory)
                p.attempt[field] = value
                p.set_attempt(p.attempt)
                self.assertEqual(p.request()["binding_status"], "unavailable")

    def test_current_context_is_not_optional_for_a_signed_request(self):
        with tempfile.TemporaryDirectory() as directory:
            p = self.project(directory)
            request = p.signed()
            result = compare_layout_policy(p.original, p.repaired, file="main.tex",
                                           approvals=request["approvals"])
            self.assertEqual(result["status"], "manual_review")
            self.assertIn("binding_unavailable", result["approval_evaluations"][0]["reason"])

    def test_all_page_metadata_is_bound_not_only_one_representative_image(self):
        with tempfile.TemporaryDirectory() as directory:
            p = self.project(directory)
            request = p.signed()
            request["approvals"][0]["evidence"]["binding"]["page_images"]["pages"].pop()
            self.assertEqual(p.validate(request)["status"], "manual_review")

    def test_external_artifact_paths_are_rejected_before_collector_read(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            with mock.patch("runtime_artifacts.collect_artifact_manifest") as collector:
                result = collect_layout_review_binding(Path(directory), main_tex="main.tex", page_images_dir=outside)
            self.assertEqual(result["status"], "unavailable")
            collector.assert_not_called()

    def test_cli_unsigned_request_then_validate_has_json_output_and_review_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            p = self.project(directory)
            script = Path(__file__).with_name("layout_policy.py")
            common = ["--root", directory, "--main-tex", "main.tex", "--baseline-tex", "baseline.before"]
            flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
            result = subprocess.run([sys.executable, "-B", str(script), "request", *common],
                                    capture_output=True, text=True, **flags)
            self.assertEqual(result.returncode, 3, result.stderr)
            request = json.loads(result.stdout)
            self.assertFalse(request["approvals"][0]["approved"])
            request = p.signed()
            request_path = p.root / "data/review.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            result = subprocess.run([sys.executable, "-B", str(script), "validate", *common,
                                     "--review-json", "data/review.json"], capture_output=True, text=True, **flags)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "pass")
            self.assertIs(report["goal_acceptance_checked"], False)
            self.assertIs(report["scientific_and_prose_preservation_checked"], False)

    def test_duplicate_review_keys_and_missing_compile_receipt_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            p = self.project(directory)
            request_path = p.root / "data/review.json"
            request_path.write_text('{"schema_version":"paperfit.layout_policy_review.v1","approvals":[],"approvals":[]}', encoding="utf-8")
            script = Path(__file__).with_name("layout_policy.py")
            flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
            result = subprocess.run([sys.executable, "-B", str(script), "validate", "--root", directory,
                                    "--main-tex", "main.tex", "--baseline-tex", "baseline.before",
                                    "--review-json", "data/review.json"], capture_output=True, text=True, **flags)
            self.assertEqual(result.returncode, 3)
            self.assertEqual(json.loads(result.stdout)["status"], "manual_review")
            p.journal.unlink()
            self.assertEqual(p.request()["binding_status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
