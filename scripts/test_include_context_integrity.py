"""Include wrapper movement must retain science checks and actual body scope."""
from __future__ import annotations

from pathlib import Path
import hashlib
import os
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from include_context import compare_include_contexts
from layout_policy import snapshot_source_edit_checks
from runtime_snapshots import create_pre_repair_snapshot

TABLE = r"\caption{Measured accuracy}\label{tab:r}\small\setlength{\tabcolsep}{2pt}\begin{tabular}{lr}Method&Score\\A&91.2\\B&89.3\\\end{tabular}"
DOCUMENT = r"\documentclass{article}\begin{document}Intro.\input{sections/results}Afterwards.\end{document}"


def source_pair():
    before = {"main.tex": DOCUMENT, "sections/results.tex": r"\input{tables/results}",
              "tables/results.tex": r"\begin{table}[t]" + TABLE + r"\end{table}"}
    after = {**before, "sections/results.tex": r"\begin{table}[t]\input{tables/results}\end{table}",
             "tables/results.tex": TABLE}
    return before, after


def check_snapshot(before, after, *, main_tex="main.tex"):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for file, source in before.items():
            target = root / file
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source, encoding="utf-8")
        snapshot = create_pre_repair_snapshot(project_root=root, main_tex=main_tex)
        for file, source in after.items():
            target = root / file
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source, encoding="utf-8")
        return snapshot_source_edit_checks(root, snapshot["rollback_target"])


class IncludeContextIntegrityTest(unittest.TestCase):
    def test_parent_float_context_corrects_local_small_without_automatic_science_pass(self):
        before, after = source_pair()
        report = check_snapshot(before, after)
        self.assertEqual(report["include_context"]["status"], "available")
        context = report["include_context"]["contexts"][0]
        self.assertEqual(context["root_file"], "sections/results.tex")
        self.assertTrue(context["float_wrapper_residual_equal"])
        self.assertEqual(report["layout_policy"]["status"], "pass")
        self.assertEqual(report["scientific_payload"]["status"], "manual_review")
        self.assertEqual(report["status"], "manual_review")
        corrected = [item for item in report["layout_policy"]["file_reports"] if item.get("include_context_corrections")]
        self.assertEqual(len(corrected), 1)
        self.assertEqual(corrected[0]["file_local_report"]["status"], "blocked")
        self.assertTrue(any(item.get("file_local_report", {}).get("status") == "failed"
                            for item in report["scientific_payload"]["file_reports"]))
        self.assertIn("before", context["source_identities"])

    def test_cells_captions_labels_and_prose_changes_still_block(self):
        for old, new in (("91.2", "90.2"), (r"\label{tab:r}", ""),
                         ("Measured accuracy", "Different claim"), ("Intro.", "Opposite claim.")):
            with self.subTest(change=(old, new)):
                before, after = source_pair()
                after = {file: source.replace(old, new) for file, source in after.items()}
                report = check_snapshot(before, after)
                self.assertEqual(report["status"], "blocked")
                self.assertNotEqual(report["scientific_payload"]["status"], "pass")
                if old != "Intro.":
                    self.assertEqual(report["scientific_payload"]["status"], "blocked")
                    self.assertFalse(report["include_context"]["contexts"][0]["float_wrapper_residual_equal"])

    def test_removed_caption_is_not_wrapper_equivalence(self):
        before, after = source_pair()
        after["tables/results.tex"] = after["tables/results.tex"].replace(r"\caption{Measured accuracy}", "")
        report = check_snapshot(before, after)
        self.assertEqual(report["scientific_payload"]["status"], "blocked")

    def test_font_leaking_after_included_table_remains_blocked(self):
        before, after = source_pair()
        # Restore a bare include and let the fragment's small font cover later
        # ordinary prose; reconstructing the owner must not bless that leak.
        after["sections/results.tex"] = r"\input{tables/results}Body in a smaller font."
        report = check_snapshot(before, after)
        self.assertEqual(report["layout_policy"]["status"], "blocked")
        self.assertTrue(any(item["rule"] == "body_font_shrink" for item in report["layout_policy"]["findings"]))

    def test_ordinary_body_small_added_in_parent_remains_blocked(self):
        before, after = source_pair()
        after["sections/results.tex"] += r"\small New body prose."
        report = check_snapshot(before, after)
        self.assertEqual(report["layout_policy"]["status"], "blocked")

    def test_changed_graph_and_duplicate_ownership_are_not_source_proofs(self):
        before, after = source_pair()
        after["sections/results.tex"] += r"\input{tables/results}"
        result = compare_include_contexts(before, after, main_tex="main.tex", files_to_check=["tables/results.tex"])
        self.assertEqual(result["status"], "unavailable")
        self.assertTrue(result["reasons"])
        before, after = source_pair()
        after["main.tex"] = after["main.tex"].replace(r"\input{sections/results}", "")
        result = compare_include_contexts(before, after, main_tex="main.tex", files_to_check=["tables/results.tex"])
        self.assertEqual(result["status"], "unavailable")

    def test_unsafe_missing_dynamic_cyclic_and_conditional_inputs_are_unavailable(self):
        controls = (r"\input{../outside}", r"\input{missing}", r"\input{\filename}",
                    r"\input{main}", r"\iftrue\input{tables/results}\fi", r"\input tables/results")
        for source in controls:
            with self.subTest(source=source):
                before, after = source_pair()
                before["sections/results.tex"] = source
                after["sections/results.tex"] = source
                result = compare_include_contexts(before, after, main_tex="main.tex", files_to_check=["tables/results.tex"])
                self.assertEqual(result["status"], "unavailable")

    def test_shadowed_reader_font_and_float_cannot_self_authorize(self):
        controls = (r"\renewcommand{\input}[1]{#1}", r"\def\small{\tiny}",
                    r"\newenvironment{table}{}{}", r"\newcommand{\setup}{\let\input\relax}")
        for declaration in controls:
            with self.subTest(declaration=declaration):
                before, after = source_pair()
                before["main.tex"] = declaration + before["main.tex"]
                after["main.tex"] = declaration + after["main.tex"]
                result = compare_include_contexts(before, after, main_tex="main.tex", files_to_check=["tables/results.tex"])
                self.assertEqual(result["status"], "unavailable")

    def test_inactive_include_examples_do_not_expand(self):
        before, after = source_pair()
        for source in ("% \\input{missing}\n", r"\verb|\input{missing}|", r"\newcommand{\example}{\input{missing}}"):
            before["main.tex"] = source + before["main.tex"]
            after["main.tex"] = source + after["main.tex"]
        result = compare_include_contexts(before, after, main_tex="main.tex", files_to_check=["tables/results.tex"])
        self.assertEqual(result["status"], "available")

    def test_ambiguous_project_and_parent_resolution_is_unavailable(self):
        before, after = source_pair()
        before["sections/tables/results.tex"] = TABLE
        after["sections/tables/results.tex"] = TABLE
        result = compare_include_contexts(before, after, main_tex="main.tex", files_to_check=["tables/results.tex"])
        self.assertEqual(result["status"], "unavailable")

    def test_complex_float_neighbour_is_retained_and_cannot_be_normalized_away(self):
        before, after = source_pair()
        neighbour = r"\begin{wraptable}{r}{.3\textwidth}Other table 97.2.\end{wraptable}"
        before["sections/results.tex"] += neighbour
        after["sections/results.tex"] += neighbour
        result = compare_include_contexts(before, after, main_tex="main.tex", files_to_check=["tables/results.tex"])
        self.assertTrue(result["contexts"][0]["float_wrapper_residual_equal"])
        after["sections/results.tex"] = after["sections/results.tex"].replace(neighbour, "Other table 97.2.")
        result = compare_include_contexts(before, after, main_tex="main.tex", files_to_check=["tables/results.tex"])
        self.assertFalse(result["contexts"][0]["float_wrapper_residual_equal"])

    def test_verbatim_space_and_percent_payload_changes_are_not_wrapper_only(self):
        for original, changed in ((r"\verb|A  B|", r"\verb|A B|"), (r"\verb|A%B|", r"\verb|A%C|")):
            with self.subTest(original=original):
                before, after = source_pair()
                before["sections/results.tex"] += original
                after["sections/results.tex"] += changed
                result = compare_include_contexts(before, after, main_tex="main.tex", files_to_check=["tables/results.tex"])
                self.assertFalse(result["contexts"][0]["float_wrapper_residual_equal"])

    def test_crlf_evidence_identities_match_original_bytes(self):
        before, after = source_pair()
        before = {file: "\r\n" + source + "\r\n" for file, source in before.items()}
        after = {file: "\r\n" + source + "\r\n" for file, source in after.items()}
        result = compare_include_contexts(before, after, main_tex="main.tex", files_to_check=["tables/results.tex"])
        context = result["contexts"][0]
        self.assertTrue(context["float_wrapper_residual_equal"])
        for file in context["files"]:
            self.assertEqual(context["source_identities"]["before"][file], hashlib.sha256(before[file].encode("utf-8")).hexdigest())

    def test_post_document_and_post_endinput_tails_do_not_import_dead_sources(self):
        before, after = source_pair()
        for sources in (before, after):
            sources["main.tex"] += r"\input{missing}\def\small{\tiny}"
            sources["tables/results.tex"] += "\n" + r"\endinput" + "\n" + r"\input{missing}\small Inactive tail."
        report = check_snapshot(before, after)
        self.assertEqual(report["include_context"]["status"], "available")
        self.assertEqual(report["layout_policy"]["status"], "pass")
        self.assertEqual(report["scientific_payload"]["status"], "manual_review")

    def test_same_line_endinput_keeps_active_commands(self):
        for tail in (r"\endinput\input{missing}", r"\endinput\def\small{\tiny}"):
            with self.subTest(tail=tail):
                before, after = source_pair()
                for sources in (before, after):
                    sources["tables/results.tex"] += "\n" + tail + "\nInactive tail."
                result = compare_include_contexts(before, after, main_tex="main.tex", files_to_check=["tables/results.tex"])
                self.assertEqual(result["status"], "unavailable")

    def test_conditional_or_redefined_terminals_do_not_hide_input(self):
        for prefix in (r"\iftrue\endinput\fi", r"\def\endinput{}\endinput"):
            with self.subTest(prefix=prefix):
                before, after = source_pair()
                for sources in (before, after):
                    sources["tables/results.tex"] += "\n" + prefix + "\n" + r"\input{missing}"
                result = compare_include_contexts(before, after, main_tex="main.tex", files_to_check=["tables/results.tex"])
                self.assertEqual(result["status"], "unavailable")

    @unittest.skipUnless(os.name == "nt", "Windows snapshot metadata path convention")
    def test_windows_main_metadata_separator_does_not_become_a_tex_escape(self):
        before, after = source_pair()
        before["paper/main.tex"] = before.pop("main.tex")
        after["paper/main.tex"] = after.pop("main.tex")
        report = check_snapshot(before, after, main_tex=r"paper\main.tex")
        self.assertEqual(report["include_context"]["status"], "available")
        self.assertEqual(report["layout_policy"]["status"], "pass")
        self.assertEqual(report["scientific_payload"]["status"], "manual_review")


if __name__ == "__main__":
    unittest.main()
