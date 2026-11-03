"""Exact local original-source evidence permits one restored inactive graphic."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from content_integrity_check import compute_content_diff, has_structure_regression


def sha(data):
    return hashlib.sha256(data).hexdigest()


class MissingGraphicRestorationTest(unittest.TestCase):
    def test_non_object_evidence_stays_fail_closed(self):
        self.evidence_path.write_text('[]', encoding='utf-8')
        result = self.check()
        self.assertNotEqual(result['source_restoration']['status'], 'verified')
        self.assertTrue(has_structure_regression(result))
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.before_path = self.root / 'before.tex'
        self.after_path = self.project / 'main.tex'
        self.reference_path = self.root / 'reference.tex'
        self.probe_path = self.root / 'probe.json'
        self.evidence_path = self.root / 'evidence.json'
        self.archive_path = self.root / 'same-version.tar.gz'
        self.reference = ('\\documentclass{article}\n\\begin{document}\nThe method does not improve recall.\n'
                          '% \\begin{figure}[t]\n% \\centering\n'
                          '% \\includegraphics[width=1\\linewidth]{Images/7.pdf}\n'
                          '% \\caption{Original inactive result 91.2}\n% \\label{fig:inactive}\n'
                          '% \\end{figure}\n'
                          '\\begin{figure}\\includegraphics[width=1\\linewidth]{real.pdf}\\caption{Result 91.2}\\label{fig:real}\\end{figure}\n'
                          '\\begin{equation}x=a+b\\end{equation}\n\\end{document}\n')
        self.before = self.reference.replace('% \\includegraphics[width=1\\linewidth]{Images/7.pdf}',
                                             '\\includegraphics[width=1.18\\textwidth]{Images/7.pdf}')
        self.after = self.reference
        (self.project / 'real.pdf').write_bytes(b'pre-existing scientific graphic fixture')
        self.evidence = {'schema': 'paperfit_source_restoration_evidence.v1', 'project_root': str(self.project),
                         'before_source_path': str(self.before_path), 'after_source_path': str(self.after_path),
                         'reference_source_path': str(self.reference_path), 'provenance_probe_path': str(self.probe_path),
                         'reference_archive_member': 'main.tex', 'before_line': 6, 'after_line': 6, 'reference_line': 6}
        self.write_sources()
        self.write_archive()
        self.write_probe()
        self.write_evidence()

    def write_sources(self):
        for role, text, path in [('before', self.before, self.before_path), ('after', self.after, self.after_path),
                                 ('reference', self.reference, self.reference_path)]:
            path.write_text(text, encoding='utf-8', newline='')
            self.evidence[role + '_source_sha256'] = sha(path.read_bytes())

    def write_archive(self, *, reference_bytes=None, include_missing=False):
        with tarfile.open(self.archive_path, 'w:gz') as archive:
            body = self.reference.encode('utf-8') if reference_bytes is None else reference_bytes
            entry = tarfile.TarInfo('main.tex'); entry.size = len(body)
            archive.addfile(entry, io.BytesIO(body))
            if include_missing:
                entry = tarfile.TarInfo('Images/7.pdf'); entry.size = 3
                archive.addfile(entry, io.BytesIO(b'pdf'))

    def write_probe(self):
        probe = {'schema': 'paperfit_asset_recovery_probe.v1', 'download': str(self.archive_path),
                 'sha256': sha(self.archive_path.read_bytes()), 'asset': 'Images/7.pdf',
                 'requested_url': 'https://example.test/same-version-source', 'final_url': 'https://example.test/same-version-source',
                 'main_tex_asset_reference': [{'line': 6, 'text': self.reference.splitlines()[5]}],
                 'asset_available': False}
        self.probe_path.write_text(json.dumps(probe), encoding='utf-8')
        self.evidence['provenance_probe_sha256'] = sha(self.probe_path.read_bytes())

    def write_evidence(self):
        self.evidence_path.write_text(json.dumps(self.evidence), encoding='utf-8')

    def check(self, *, evidence=True):
        return compute_content_diff(self.before, self.after, layout_only=True,
                                    source_restoration_evidence_path=str(self.evidence_path) if evidence else None)

    def assert_blocked(self):
        result = self.check()
        self.assertEqual(result['action_required'], 'auto_rollback', result)
        self.assertTrue(has_structure_regression(result) or result['scientific_payload']['status'] != 'pass'
                        or result['layout_prose']['status'] != 'pass', result)
        return result

    def test_exact_local_original_comment_restoration_passes_and_keeps_raw_inventory(self):
        result = self.check()
        self.assertEqual(result['source_restoration']['status'], 'verified', result)
        self.assertEqual(result['action_required'], 'none', result)
        self.assertFalse(has_structure_regression(result))
        self.assertEqual(result['structure_changes']['before']['includegraphics_count'], 2)
        self.assertEqual(result['structure_changes']['after']['includegraphics_count'], 1)
        self.assertEqual(result['structure_changes']['removed_graphics_files'], ['Images/7.pdf'])
        self.assertIn('no network-origin authentication', result['source_restoration']['limits'])

    def test_default_without_evidence_still_rejects_graphic_removal(self):
        result = self.check(evidence=False)
        self.assertEqual(result['action_required'], 'auto_rollback')
        self.assertTrue(has_structure_regression(result))

    def test_combined_legitimate_layout_change_is_checked_and_allowed(self):
        self.after = self.after.replace('x=a+b', 'x=a+{}b').replace('[width=1\\linewidth]{real.pdf}', '[width=.9\\linewidth]{real.pdf}')
        self.write_sources(); self.write_evidence()
        self.assertEqual(self.check()['action_required'], 'none')

    def test_existing_identical_comment_elsewhere_cannot_authorize_block_deletion(self):
        lines = self.before.splitlines(keepends=True)
        second_block = ''.join(self.reference.splitlines(keepends=True)[3:9])
        self.before = ''.join(lines[:9]) + second_block + ''.join(lines[9:])
        self.after = self.reference
        self.write_sources(); self.write_evidence()
        self.assertEqual(self.assert_blocked()['source_restoration']['reason'], 'restoration_line_not_matched_change')

    def test_other_real_figure_deletion_still_rolls_back(self):
        self.after = self.after.replace(self.after.splitlines()[9], '')
        self.write_sources(); self.write_evidence()
        self.assert_blocked()

    def test_other_real_graphic_deletion_still_rolls_back(self):
        self.after = self.after.replace(r'\includegraphics[width=1\linewidth]{real.pdf}', '')
        self.write_sources(); self.write_evidence()
        self.assert_blocked()

    def test_number_sign_label_and_prose_mutations_still_roll_back(self):
        baseline = self.after
        for old, new in [('Result 91.2', 'Result 19.2'), ('x=a+b', 'x=a-b'),
                         ('fig:real', 'fig:altered'), ('does not improve', 'does now improve')]:
            with self.subTest(old=old):
                self.after = baseline.replace(old, new)
                self.write_sources(); self.write_evidence()
                self.assert_blocked()

    def test_asset_must_actually_be_missing(self):
        (self.project / 'Images').mkdir()
        (self.project / 'Images/7.pdf').write_bytes(b'existing scientific content')
        self.assertEqual(self.assert_blocked()['source_restoration']['reason'], 'asset_not_confirmed_absent_inside_project')

    def test_nested_source_uses_actual_source_directory_for_asset_lookup(self):
        nested = self.project / 'nested'
        (nested / 'Images').mkdir(parents=True)
        self.after_path = nested / 'main.tex'
        self.evidence['after_source_path'] = str(self.after_path)
        self.write_sources(); self.write_evidence()
        (nested / 'Images/7.pdf').write_bytes(b'actual nested scientific asset')
        self.assertFalse((self.project / 'Images/7.pdf').exists())
        self.assertEqual(self.assert_blocked()['source_restoration']['reason'], 'asset_not_confirmed_absent_inside_project')

    def test_missing_asset_present_in_archived_original_is_not_approved(self):
        self.write_archive(include_missing=True); self.write_probe(); self.write_evidence()
        self.assertEqual(self.assert_blocked()['source_restoration']['reason'], 'asset_present_in_reference_archive')

    def test_stale_or_forged_source_hash_does_not_bind_current_sources(self):
        self.evidence['after_source_sha256'] = '0' * 64; self.evidence['approved'] = True
        self.write_evidence()
        self.assertEqual(self.assert_blocked()['source_restoration']['reason'], 'after_source_hash_mismatch')

    def test_passed_source_pair_must_match_actual_hashed_files(self):
        self.after_path.write_text(self.after + '% different bytes\n', encoding='utf-8')
        self.evidence['after_source_sha256'] = sha(self.after_path.read_bytes()); self.write_evidence()
        self.assertEqual(self.assert_blocked()['source_restoration']['reason'], 'evidence_does_not_bind_current_source_pair')

    def test_archive_bytes_reference_member_and_probe_are_verified(self):
        self.write_archive(reference_bytes=b'not the trusted source'); self.write_probe(); self.write_evidence()
        self.assertEqual(self.assert_blocked()['source_restoration']['reason'], 'reference_source_not_archive_member_bytes')

    def test_archive_or_provenance_tampering_is_blocked(self):
        self.archive_path.write_bytes(self.archive_path.read_bytes() + b'tampered')
        self.assertEqual(self.assert_blocked()['source_restoration']['reason'], 'reference_archive_hash_mismatch')
        self.write_archive(); self.write_probe(); self.write_evidence()
        self.probe_path.write_text('{}', encoding='utf-8')
        self.assertEqual(self.assert_blocked()['source_restoration']['reason'], 'provenance_record_hash_mismatch')

    def test_commented_caption_or_label_context_must_match_original(self):
        for old, new in [('Original inactive result', 'Invented inactive result'), ('fig:inactive', 'fig:other')]:
            with self.subTest(old=old):
                self.after = self.reference.replace(old, new)
                self.write_sources(); self.write_evidence()
                self.assertEqual(self.assert_blocked()['source_restoration']['reason'], 'original_comment_context_mismatch')

    def test_active_figure_environment_caption_and_label_cannot_be_removed(self):
        self.before = self.before.replace('% \\begin{figure}', '\\begin{figure}')
        self.write_sources(); self.write_evidence()
        self.assert_blocked()

    def test_simply_prefixing_comment_to_mutated_invocation_is_not_exact_restoration(self):
        self.after = self.before.replace('\\includegraphics[width=1.18', '% \\includegraphics[width=1.18')
        self.write_sources(); self.write_evidence()
        self.assertEqual(self.assert_blocked()['source_restoration']['reason'], 'after_line_not_exact_original_comment')

    def test_dynamic_asset_and_graphics_search_configuration_require_review(self):
        self.before = self.before.replace('{Images/7.pdf}', r'{\AssetName}')
        self.write_sources(); self.write_evidence()
        self.assertEqual(self.assert_blocked()['source_restoration']['status'], 'manual_review')
        self.before = self.reference.replace('% \\includegraphics[width=1\\linewidth]{Images/7.pdf}',
                                             '\\includegraphics[width=1.18\\textwidth]{Images/7.pdf}')
        self.before = '\\graphicspath{{alternative/}}\n' + self.before
        self.after = '\\graphicspath{{alternative/}}\n' + self.reference
        self.evidence['before_line'] = 7; self.evidence['after_line'] = 7
        self.write_sources(); self.write_evidence()
        self.assertEqual(self.assert_blocked()['source_restoration']['reason'], 'graphics_search_configuration_requires_review')

    def test_path_escape_stays_blocked_even_if_target_absent(self):
        self.reference = self.reference.replace('Images/7.pdf', 'Images/../../7.pdf')
        self.before = self.before.replace('Images/7.pdf', 'Images/../../7.pdf')
        self.after = self.reference
        self.write_sources(); self.write_archive(); self.write_probe(); self.write_evidence()
        self.assertEqual(self.assert_blocked()['source_restoration']['reason'], 'asset_not_confirmed_absent_inside_project')

    def test_directly_redefined_graphic_command_requires_review(self):
        original_before, original_after = self.before, self.after
        for definition in (r'\def\includegraphics{\ScientificClaim}',
                           r'\renewcommand{\includegraphics}[1]{\ScientificClaim}',
                           r'\let\includegraphics=\ScientificClaim'):
            with self.subTest(definition=definition):
                self.before = definition + '\n' + original_before
                self.after = definition + '\n' + original_after
                self.evidence['before_line'] = 7; self.evidence['after_line'] = 7
                self.write_sources(); self.write_evidence()
                self.assertEqual(self.assert_blocked()['source_restoration']['reason'], 'includegraphics_redefinition_requires_review')

    def test_diff_cli_requires_explicit_evidence_and_preserves_exit_code(self):
        script = Path(__file__).with_name('content_integrity_check.py')
        output = self.root / 'cli.json'
        args = [sys.executable, str(script), 'diff', str(self.before_path), str(self.after_path), '--output', str(output)]
        self.assertEqual(subprocess.run(args, capture_output=True).returncode, 3)
        self.assertEqual(subprocess.run(args + ['--source-restoration-evidence', str(self.evidence_path)], capture_output=True).returncode, 0)
        self.assertEqual(json.loads(output.read_text())['source_restoration']['status'], 'verified')


if __name__ == '__main__':
    unittest.main()
