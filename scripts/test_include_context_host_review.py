"""Offline synthetic native-receipt fixtures for explicit cross-file review.

Actual TeX and manuscript quality are independently verified outside this suite.
These tests only exercise receipt, source and runtime acceptance boundaries.
"""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gatekeeper_enforcer import make_decision
from layout_policy import snapshot_source_edit_checks
from repair_plan_executor import _explicit_review_inputs
from source_review import (build_include_context_review_request, validate_include_context_review,
                           validate_source_review, load_source_review)
from test_host_review_runtime_integration import rebind, snapshot, state_for, sign_source
from test_include_context_integrity import source_pair
from test_layout_policy_host_review import CurrentProject


class IncludeContextHostReviewTest(unittest.TestCase):
    def fixture(self, directory, *, style=r"\newcommand{\fixturestyle}{Retained}", cell=None):
        root = Path(directory)
        p = CurrentProject(root, "")
        before, after = source_pair()
        for sources in (before, after):
            sources["main.tex"] = sources["main.tex"].replace(r"\begin{document}", r"\usepackage{localstyle}\begin{document}")
            if cell is not None:
                sources["tables/results.tex"] = sources["tables/results.tex"].replace("91.2", cell)
        (root / "localstyle.sty").write_text(style, encoding="utf-8")
        for file, source in after.items():
            target = root / file
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source, encoding="utf-8")
        p.original, p.repaired = before["main.tex"], after["main.tex"]
        rebind(p, files=[*after, "references.bib", "localstyle.sty"])
        baseline = snapshot(p, before_files=before)
        return p, before, after, baseline

    def request(self, p, baseline):
        return build_include_context_review_request(project_root=p.root, rollback_target=baseline,
                    file="sections/results.tex", **p.context)

    def signed(self, p, baseline):
        request = sign_source(self.request(p, baseline))
        request["caption_label_bindings_checked"] = True
        request["reason"] = "宿主核验源码、全部当前页、标题编号及引用对象。"
        return request

    def validate(self, p, baseline, request):
        return validate_include_context_review(project_root=p.root, rollback_target=baseline,
                    file="sections/results.tex", review=request)

    def test_unsigned_request_is_read_only_and_pending_then_explicit_review_unlocks_live_channels(self):
        with tempfile.TemporaryDirectory() as directory:
            p, _, _, baseline = self.fixture(directory)
            original = {path: path.read_bytes() for path in p.root.rglob("*") if path.is_file()}
            request = self.request(p, baseline)
            self.assertEqual(request["binding_status"], "available")
            self.assertFalse(request["approved"])
            self.assertEqual(self.validate(p, baseline, request)["status"], "manual_review")
            self.assertEqual(original, {path: path.read_bytes() for path in original})
            pending = make_decision(state_for(p, baseline), {}, False, None, project_root=p.root)
            self.assertEqual(pending["decision"], "CONTINUE")
            self.assertFalse(pending["checks"]["scientific_payload"]["pass"])
            request = self.signed(p, baseline)
            self.assertEqual(self.validate(p, baseline, request)["status"], "verified")
            checked = snapshot_source_edit_checks(p.root, baseline, source_reviews=[request])
            self.assertEqual(checked["status"], "pass")
            self.assertTrue(any(item.get("file_local_report", {}).get("status") == "failed"
                                for item in checked["scientific_payload"]["file_reports"]))
            self.assertTrue(all(not item.get("assessment", {}).get("requires_host_review", False)
                                for item in checked["scientific_payload"]["file_reports"] if item.get("host_review", {}).get("status") == "verified"))
            gate = make_decision(state_for(p, baseline), {}, False, None, project_root=p.root, source_reviews=[request])
            self.assertTrue(gate["checks"]["scientific_payload"]["pass"])
            self.assertTrue(gate["checks"]["layout_prose"]["pass"])
            self.assertTrue(gate["checks"]["layout_policy"]["pass"])

    def test_duplicate_review_and_machine_advice_never_select_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            p, _, _, baseline = self.fixture(directory)
            signed = self.signed(p, baseline)
            duplicate = snapshot_source_edit_checks(p.root, baseline, source_reviews=[signed, copy.deepcopy(signed)])
            self.assertEqual(duplicate["scientific_payload"]["status"], "manual_review")
            advice = self.request(p, baseline)
            advice["host_advice"] = {"recommended": "accept", "verified": True}
            self.assertEqual(self.validate(p, baseline, advice)["status"], "manual_review")

    def test_local_style_remains_in_verified_baseline_but_not_literal_include_graph(self):
        with tempfile.TemporaryDirectory() as directory:
            p, _, _, baseline = self.fixture(directory)
            manifest = json.loads((p.root / baseline).read_text(encoding="utf-8"))
            self.assertIn("localstyle.sty", [item["path"] for item in manifest["files"]])
            report = snapshot_source_edit_checks(p.root, baseline)
            self.assertEqual(report["include_context"]["status"], "available")
            self.assertTrue(any(item["file"] == "localstyle.sty" for item in report["scientific_payload"]["file_reports"]))
            context = report["include_context"]["contexts"][0]
            self.assertNotIn("localstyle.sty", context["files"])
            self.assertEqual(self.validate(p, baseline, self.signed(p, baseline))["status"], "verified")

    def test_signed_include_review_cannot_accept_data_emitted_by_local_spacing_definition(self):
        with tempfile.TemporaryDirectory() as directory:
            p, _, after, baseline = self.fixture(directory,
                style=r"\renewcommand{\hspace}[1]{#1}", cell=r"\hspace{42pt}")
            signed = self.signed(p, baseline)
            path = p.root / "tables/results.tex"
            path.write_text(path.read_text(encoding="utf-8").replace("42pt", "17pt"), encoding="utf-8")
            # Bind a current synthetic native receipt too: rejection must arise
            # from the preserved payload, not only from stale build evidence.
            rebind(p, files=[*after, "references.bib", "localstyle.sty"])
            with self.assertRaises(ValueError):
                self.request(p, baseline)
            self.assertEqual(self.validate(p, baseline, signed)["status"], "manual_review")
            report = snapshot_source_edit_checks(p.root, baseline, source_reviews=[signed])
            self.assertEqual(report["scientific_payload"]["status"], "blocked")
            self.assertFalse(report["include_context"]["contexts"][0]["float_wrapper_residual_equal"])
            gate = make_decision(state_for(p, baseline), {}, False, None, project_root=p.root, source_reviews=[signed])
            self.assertFalse(gate["checks"]["scientific_payload"]["pass"])

    def test_each_host_confirmation_and_reason_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            p, _, _, baseline = self.fixture(directory)
            signed = self.signed(p, baseline)
            for key in ("approved", "source_reviewed", "all_pages_inspected", "content_preservation_confirmed",
                        "template_checked", "intended_object_checked", "caption_label_bindings_checked", "reason"):
                with self.subTest(key=key):
                    request = copy.deepcopy(signed)
                    request[key] = "" if key == "reason" else False
                    self.assertEqual(self.validate(p, baseline, request)["status"], "manual_review")

    def test_stale_source_pdf_pages_imported_style_and_compile_evidence_cannot_reuse_review(self):
        for attack in ("source", "pdf", "page", "style", "recorder", "journal", "baseline"):
            with self.subTest(attack=attack), tempfile.TemporaryDirectory() as directory:
                p, _, _, baseline = self.fixture(directory)
                request = self.signed(p, baseline)
                path = {"source": p.root / "tables/results.tex", "pdf": p.pdf,
                        "page": next(p.pages.glob("*.png")), "style": p.root / "localstyle.sty",
                        "recorder": p.root / "main.fls", "journal": p.journal,
                        "baseline": p.root / baseline}[attack]
                info = path.stat()
                path.write_bytes(path.read_bytes() + (b"\n% stale" if attack != "journal" else b"\n{}\n"))
                os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
                self.assertEqual(self.validate(p, baseline, request)["status"], "manual_review")
                gate = make_decision(state_for(p, baseline), {}, False, None, project_root=p.root, source_reviews=[request])
                self.assertFalse(gate["checks"]["scientific_payload"]["pass"])

    def test_modified_cells_labels_captions_and_real_body_font_leaks_cannot_be_reviewed(self):
        for old, new in (("91.2", "90.2"), (r"\label{tab:r}", ""), ("Measured accuracy", "Wrong claim")):
            with self.subTest(change=(old, new)), tempfile.TemporaryDirectory() as directory:
                p, _, _, baseline = self.fixture(directory)
                request = self.signed(p, baseline)
                path = p.root / "tables/results.tex"
                path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
                self.assertEqual(self.validate(p, baseline, request)["status"], "manual_review")
                with self.assertRaises(ValueError):
                    self.request(p, baseline)
                report = snapshot_source_edit_checks(p.root, baseline, source_reviews=[request])
                self.assertEqual(report["scientific_payload"]["status"], "blocked")
        with tempfile.TemporaryDirectory() as directory:
            p, _, _, baseline = self.fixture(directory)
            request = self.signed(p, baseline)
            (p.root / "sections/results.tex").write_text(r"\input{tables/results} Leaked small body.", encoding="utf-8")
            self.assertEqual(self.validate(p, baseline, request)["status"], "manual_review")
            with self.assertRaises(ValueError):
                self.request(p, baseline)
            report = snapshot_source_edit_checks(p.root, baseline, source_reviews=[request])
            self.assertEqual(report["layout_policy"]["status"], "blocked")

    def test_forged_classification_wrong_main_or_snapshot_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            p, _, _, baseline = self.fixture(directory)
            request = self.signed(p, baseline)
            for key, value in (("classification", {"eligible": True}), ("main_tex", "other.tex"),
                               ("rollback_target", "another.json"), ("snapshot_identity", {"sha256": "0" * 64})):
                with self.subTest(key=key):
                    forged = copy.deepcopy(request)
                    forged[key] = value
                    self.assertEqual(self.validate(p, baseline, forged)["status"], "manual_review")
            wrong = copy.deepcopy(request)
            wrong["context"]["main_tex"] = "other.tex"
            self.assertEqual(self.validate(p, baseline, wrong)["status"], "manual_review")
            with self.assertRaises(ValueError):
                build_include_context_review_request(project_root=p.root, rollback_target=baseline,
                    file="sections/results.tex", main_tex="other.tex", page_images_dir="data/pages")
            # The same receipt must never be applied as a single-file bypass.
            self.assertEqual(validate_source_review("", "", project_root=p.root,
                    file="sections/results.tex", review=request)["status"], "manual_review")

    def test_final_artifact_read_barrier_is_repeated(self):
        with tempfile.TemporaryDirectory() as directory:
            p, _, _, baseline = self.fixture(directory)
            request = self.signed(p, baseline)
            with mock.patch("source_review.verify_layout_review_binding_current", side_effect=ValueError("changed at final barrier")):
                self.assertEqual(self.validate(p, baseline, request)["status"], "manual_review")

    def test_cli_existing_loader_and_live_gate_accept_explicit_receipt_with_gbk_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            p, _, _, baseline = self.fixture(directory)
            script = Path(__file__).resolve().parent / "source_review.py"
            args = [sys.executable, "-B", str(script), "request", "--root", str(p.root),
                    "--file", "sections/results.tex", "--main-tex", "main.tex", "--rollback-target", baseline]
            env = dict(os.environ, PYTHONIOENCODING="gbk")
            run = subprocess.run(args, capture_output=True, env=env)
            self.assertEqual(run.returncode, 3, run.stderr.decode("utf-8", errors="replace"))
            request = json.loads(run.stdout.decode("utf-8"))
            sign_source(request)
            request.update(caption_label_bindings_checked=True, reason="明确核验全部页和编号。")
            path = p.root / "include-review.json"
            path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(load_source_review(path)["review_scope"], "literal_include_context")
            loaded = _explicit_review_inputs(p.root, "main.tex", source_paths=["include-review.json"])[2]
            validate = args.copy()
            validate[3] = "validate"
            validate.extend(["--review-json", "include-review.json"])
            run = subprocess.run(validate, capture_output=True, env=env)
            self.assertEqual(run.returncode, 0, run.stdout.decode("utf-8", errors="replace") + run.stderr.decode("utf-8", errors="replace"))
            self.assertEqual(json.loads(run.stdout.decode("utf-8"))["status"], "verified")
            gate = make_decision(state_for(p, baseline), {}, False, None, project_root=p.root, source_reviews=loaded)
            self.assertTrue(gate["checks"]["scientific_payload"]["pass"])


if __name__ == "__main__":
    unittest.main()
