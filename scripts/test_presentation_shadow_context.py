"""Regression contracts for document shadows and retained color review.

Scientific/native counterexamples are preserved outside the repository by the
audit. These tests exercise actual source snapshots and the enforcement chain;
artifact-backed host approvals use the existing explicitly synthetic fixture.
"""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from content_integrity_check import compute_content_diff
from layout_policy import compare_layout_policy, snapshot_source_edit_checks
from repair_plan_executor import _enforce_structure_integrity
from runtime_snapshots import create_pre_repair_snapshot, discover_source_files
from scientific_integrity import scientific_payload_diff, presentation_shadow_context
from source_review import reviewable_uncertainty
from test_layout_policy_host_review import CurrentProject


def document(body: str, preamble: str = "") -> str:
    return "\\documentclass{article}\n" + preamble + "\n\\begin{document}\n" + body + "\n\\end{document}\n"


class PresentationShadowContextTest(unittest.TestCase):
    def project(self, before: str, after: str, definitions: str | None = None, resources: dict[str, str] | None = None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        main = root / "main.tex"
        main.write_text(before, encoding="utf-8")
        if definitions is not None:
            (root / "defs.tex").write_text(definitions, encoding="utf-8")
        for filename, content in (resources or {}).items():
            (root / filename).write_text(content, encoding="utf-8")
        sources = {path: path.read_bytes() for path in discover_source_files(root, "main.tex")}
        snapshot = create_pre_repair_snapshot(project_root=root, main_tex="main.tex")
        main.write_text(after, encoding="utf-8")
        return root, sources, snapshot

    def enforce(self, root, sources, before):
        return _enforce_structure_integrity(
            main_tex="main.tex", original_tex=before, original_bytes=sources[root / "main.tex"],
            original_sources=sources, project_root=root,
            report={"status": "success", "success": True, "applied_count": 1},
        )

    def test_unchanged_include_redefined_spacing_cannot_pass_or_retain_data_change(self):
        before = document(r"Result: \hspace{42pt}.", r"\input{defs}")
        after = before.replace("42pt", "17pt")
        root, sources, snapshot = self.project(before, after, r"\renewcommand{\hspace}[1]{#1}")
        diff = compute_content_diff(before, after, file="main.tex", project_root=root, layout_only=True)
        self.assertEqual(diff["scientific_payload"]["status"], "failed")
        self.assertEqual(diff["layout_prose"]["status"], "failed")
        self.assertEqual(diff["action_required"], "auto_rollback")
        checks = snapshot_source_edit_checks(root, snapshot["rollback_target"])
        self.assertEqual(checks["status"], "blocked")
        result = self.enforce(root, sources, before)
        self.assertFalse(result["success"])
        self.assertEqual((root / "main.tex").read_bytes(), sources[root / "main.tex"])
        self.assertEqual((root / "defs.tex").read_bytes(), sources[root / "defs.tex"])

    def test_removing_unchanged_include_font_macro_output_cannot_pass(self):
        before = document(r"\small Stable prose.", r"\input{defs}")
        after = before.replace(r"\small", r"\normalsize")
        root, sources, snapshot = self.project(before, after, r"\renewcommand{\small}{The result is 42.}")
        checks = snapshot_source_edit_checks(root, snapshot["rollback_target"])
        self.assertEqual(checks["layout_prose"]["status"], "blocked")
        self.assertEqual(self.enforce(root, sources, before)["status"], "failed")
        self.assertEqual((root / "main.tex").read_bytes(), sources[root / "main.tex"])

    def test_local_style_class_and_definition_sources_are_real_snapshot_context(self):
        fixtures = (
            (r"\usepackage{local}", {"local.sty": r"\renewcommand{\hspace}[1]{#1}"}),
            (r"\usepackage{local}", {"local.sty": r"\input{local.def}", "local.def": r"\renewcommand{\hspace}[1]{#1}"}),
            (r"\documentclass{local}", {"local.cls": r"\LoadClass{article}\renewcommand{\hspace}[1]{#1}"}),
        )
        for preamble, resources in fixtures:
            with self.subTest(preamble=preamble, resources=resources):
                before = document(r"Result: \hspace{42pt}.", preamble)
                if preamble.startswith(r"\documentclass"):
                    before = before.replace(r"\documentclass{article}" + "\n", "", 1)
                after = before.replace("42pt", "17pt")
                root, sources, snapshot = self.project(before, after, resources=resources)
                self.assertTrue({root / filename for filename in resources} <= set(sources))
                self.assertEqual(snapshot_source_edit_checks(root, snapshot["rollback_target"])["status"], "blocked")
                self.assertEqual(self.enforce(root, sources, before)["status"], "failed")
                self.assertEqual((root / "main.tex").read_bytes(), sources[root / "main.tex"])

    def test_discovery_ignores_unreachable_or_nonexecuted_style_references(self):
        before = document(r"Stable\hspace{1pt}prose.", "% \\usepackage{unused}\n\\newcommand{\\unusedload}{\\usepackage{unused}}")
        after = before.replace("1pt", "2pt")
        root, sources, snapshot = self.project(before, after, resources={"unused.sty": r"\renewcommand{\hspace}[1]{#1}"})
        self.assertNotIn(root / "unused.sty", sources)
        self.assertEqual(snapshot_source_edit_checks(root, snapshot["rollback_target"])["status"], "pass")

    def test_real_local_llncs_constructed_context_retains_finite_spacing_for_host(self):
        template = (Path(__file__).resolve().parent.parent / "config/host_assets/template_kits_prepared/"
                    "ECCV2026_TEMPLATE/payload/paper-template-Latest/llncs.cls")
        if not template.is_file():
            self.skipTest("bundled llncs source is unavailable in this distribution")
        before = document(r"Stable\hspace{1pt}prose. $x=42$").replace(r"\documentclass{article}", r"\documentclass{llncs}")
        after = before.replace("1pt", "2pt")
        root, sources, snapshot = self.project(before, after, resources={"llncs.cls": template.read_text(encoding="utf-8")})
        checks = snapshot_source_edit_checks(root, snapshot["rollback_target"])
        self.assertEqual(checks["status"], "manual_review")
        result = self.enforce(root, sources, before)
        self.assertEqual(result["status"], "manual_review")
        self.assertEqual((root / "main.tex").read_text(encoding="utf-8"), after)
        raw = next(item for item in result["scientific_payload"]["file_reports"] if item["file"] == "main.tex")
        self.assertEqual(raw["automated_status"], "failed")

    def test_constructed_context_does_not_relax_explicit_data_emitting_shadow(self):
        before = document(r"Result \hspace{42pt}.", r"\usepackage{local}")
        after = before.replace("42pt", "17pt")
        definitions = r"\renewcommand{\hspace}[1]{#1}\expandafter\def\csname unrelated\endcsname{}"
        root, sources, snapshot = self.project(before, after, resources={"local.sty": definitions})
        self.assertEqual(snapshot_source_edit_checks(root, snapshot["rollback_target"])["status"], "blocked")
        self.assertEqual(self.enforce(root, sources, before)["status"], "failed")

    def test_constructed_template_spacing_review_cannot_mask_other_changed_payload(self):
        before = document(r"Result 42\hspace{1pt}retained.", r"\usepackage{local}")
        after = before.replace("1pt", "2pt").replace("Result 42", "Result 17")
        root, sources, snapshot = self.project(before, after, resources={"local.sty": r"\expandafter\def\csname unrelated\endcsname{}"})
        self.assertEqual(snapshot_source_edit_checks(root, snapshot["rollback_target"])["status"], "blocked")
        self.assertEqual(self.enforce(root, sources, before)["status"], "failed")

    def test_all_modern_command_declarations_preserve_shadowed_arguments(self):
        for declaration in ("NewDocumentCommand", "RenewDocumentCommand", "ProvideDocumentCommand", "DeclareDocumentCommand"):
            with self.subTest(declaration=declaration):
                before = document(r"Result: \hspace{42pt}.", "\\" + declaration + r"{\hspace}{m}{#1}")
                after = before.replace("42pt", "17pt")
                diff = compute_content_diff(before, after, layout_only=True)
                self.assertNotEqual(diff["scientific_payload"]["status"], "pass")
                self.assertNotEqual(diff["layout_prose"]["status"], "pass")

    def test_modern_definition_bodies_and_environment_signatures_remain_protected(self):
        for declaration in (r"\NewDocumentCommand{\result}{m}{42}", r"\NewDocumentEnvironment{result}{m}{42}{}"):
            with self.subTest(declaration=declaration):
                before = document("Stable prose.", declaration)
                after = before.replace("{42}", "{17}")
                science = scientific_payload_diff(before, after)
                self.assertIn("macro_definitions", science["changed_components"])
                self.assertEqual(science["status"], "failed")

    def test_math_spacing_honors_classic_and_modern_shadows(self):
        for declaration in (r"\renewcommand{\hspace}[1]{#1}", r"\RenewDocumentCommand{\hspace}{m}{#1}"):
            with self.subTest(declaration=declaration):
                before = document(r"$x=\hspace{42pt}$", declaration)
                after = before.replace("42pt", "17pt")
                science = scientific_payload_diff(before, after)
                self.assertEqual(science["status"], "failed")
                self.assertIn("math_body", science["changed_components"])

    def test_both_baseline_and_current_definition_inventories_supply_shadows(self):
        shadows = presentation_shadow_context((r"\renewcommand{\hspace}[1]{#1}", ""))
        self.assertIn("hspace", shadows)
        before, after = document(r"Result \hspace{42pt}."), document(r"Result \hspace{17pt}.")
        self.assertNotEqual(scientific_payload_diff(before, after, opaque_commands=shadows)["status"], "pass")
        self.assertFalse(reviewable_uncertainty(before, after, opaque_commands=shadows)["eligible"])

    def test_ordinary_spacing_with_unrelated_included_macro_still_passes(self):
        before = document(r"Stable\hspace{1pt}prose. $x\hspace{1pt}=42$", r"\input{defs}")
        after = before.replace("1pt", "2pt")
        root, sources, snapshot = self.project(before, after, r"\newcommand{\claim}{Retained unrelated text}")
        self.assertEqual(snapshot_source_edit_checks(root, snapshot["rollback_target"])["status"], "pass")
        self.assertEqual(self.enforce(root, sources, before)["status"], "success")
        self.assertEqual((root / "main.tex").read_text(encoding="utf-8"), after)

    def test_existing_finite_macro_spacing_uncertainty_is_retained_without_auto_pass(self):
        before = document(r"\gap Stable prose.", r"\newcommand{\gap}{\hspace{1pt}}")
        after = before.replace("1pt", "2pt")
        root, sources, snapshot = self.project(before, after)
        checks = snapshot_source_edit_checks(root, snapshot["rollback_target"])
        self.assertEqual(checks["scientific_payload"]["status"], "manual_review")
        self.assertEqual(self.enforce(root, sources, before)["status"], "manual_review")
        self.assertEqual((root / "main.tex").read_text(encoding="utf-8"), after)


class ColorVisibilityReviewTest(unittest.TestCase):
    project = PresentationShadowContextTest.project
    enforce = PresentationShadowContextTest.enforce
    def test_new_white_prose_and_cells_require_review_and_remain_retained(self):
        for body, colored in (("The result is 42.", r"{\color{white}The result is 42.}"),
                              (r"\begin{tabular}{c}42\\\end{tabular}", r"\begin{tabular}{c}{\color{white}42}\\\end{tabular}")):
            with self.subTest(body=body):
                before, after = document(body), document(colored)
                root, sources, snapshot = self.project(before, after)
                diff = compute_content_diff(before, after, layout_only=True)
                self.assertEqual(diff["scientific_payload"]["status"], "pass")
                self.assertEqual(diff["layout_policy"]["status"], "manual_review")
                self.assertEqual(diff["action_required"], "manual_review")
                self.assertEqual(snapshot_source_edit_checks(root, snapshot["rollback_target"])["status"], "manual_review")
                self.assertEqual(self.enforce(root, sources, before)["status"], "manual_review")
                self.assertEqual((root / "main.tex").read_text(encoding="utf-8"), after)

    def test_changed_background_requires_review_even_when_white_foreground_is_inherited(self):
        before = document(r"\begin{tabular}{c}\cellcolor{black}\color{white}42\\\end{tabular}")
        after = before.replace(r"\cellcolor{black}", r"\cellcolor{white}")
        policy = compare_layout_policy(before, after)
        self.assertEqual(policy["status"], "manual_review")
        self.assertIn("background_color_visibility", [finding["rule"] for finding in policy["findings"]])

    def test_inherited_color_does_not_block_unrelated_normal_spacing(self):
        before = document(r"{\color{blue}Stable\hspace{1pt}prose.}")
        after = before.replace("1pt", "2pt")
        self.assertEqual(compute_content_diff(before, after, layout_only=True)["action_required"], "none")

    def test_valid_contrast_can_use_existing_exact_current_build_host_review(self):
        with tempfile.TemporaryDirectory() as directory:
            p = CurrentProject(Path(directory), r"\begin{tabular}{c}\cellcolor{black}\color{white}42\\\end{tabular}")
            request = p.request()
            self.assertEqual(request["policy"]["status"], "manual_review")
            for approval in request["approvals"]:
                self.assertTrue(approval["can_host_approve"])
                approval["approved"] = True
                approval["evidence"].update(
                    reason="Host inspected this exact table and every current page.",
                    visual_inspected=True, all_pages_inspected=True,
                    readability_confirmed=True, visible_content_preservation_confirmed=True,
                    measurement_context="White cell text over the black cell background remains visible at the current rendered size.",
                )
            self.assertEqual(p.validate(request)["status"], "pass")
            request["approvals"][0]["evidence"]["measurement_context"] = ""
            self.assertEqual(p.validate(request)["status"], "manual_review")


if __name__ == "__main__":
    unittest.main()
