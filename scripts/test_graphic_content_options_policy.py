"""A retained image filename cannot certify unchanged visible image content."""
import unittest
from pathlib import Path
import tempfile
from content_integrity_check import compute_content_diff
from layout_policy import compare_layout_policy


def doc(options='width=.8\\linewidth'):
    return (r'\documentclass{article}\begin{document}\begin{figure}'
            + r'\includegraphics[' + options + r']{plot.pdf}'
            + r'\caption{Measured results}\label{fig:plot}\end{figure}\end{document}')


class GraphicContentOptionsTest(unittest.TestCase):
    def test_adding_or_changing_crop_is_pending_not_automatic_acceptance(self):
        for before, after in [(doc(), doc('width=.8\\linewidth,trim=0 0 80pt 0,clip')),
                              (doc('trim=0 0 4pt 0,clip'), doc('trim=0 0 80pt 0,clip')),
                              (doc(), doc('viewport=0 0 20 30,clip')),
                              (doc(), doc('opacity=0')),
                              (doc(), doc('page=2'))]:
            with self.subTest(after=after):
                result = compute_content_diff(before, after, layout_only=True)
                self.assertEqual(result['layout_policy']['status'], 'manual_review', result)
                self.assertEqual(result['action_required'], 'manual_review', result)

    def test_zero_direct_dimension_scale_and_draft_cannot_be_approved(self):
        for options in ['width=0pt', 'width=0 pt', 'height=0 pt', 'totalheight=0 pt',
                        'scale=0', 'scale=-1', 'draft', 'draft=true']:
            with self.subTest(options=options):
                result = compute_content_diff(doc(), doc(options), layout_only=True)
                self.assertEqual(result['layout_policy']['status'], 'blocked', result)
                self.assertEqual(result['action_required'], 'auto_rollback', result)

    def test_positive_native_sizing_and_removing_crop_stay_usable(self):
        self.assertEqual(compare_layout_policy(doc(), doc('width=.9\\linewidth'))['status'], 'pass')
        self.assertEqual(compare_layout_policy(doc('trim=0 0 4pt 0,clip'), doc())['status'], 'pass')
        self.assertEqual(compare_layout_policy(doc('trim=0 0 4pt 0,clip'),
                                              doc('trim=0 0 4pt 0,clip'))['status'], 'pass')
        self.assertEqual(compare_layout_policy(doc('draft=true'), doc('draft=false'))['status'], 'pass')

    def test_shadowed_graphic_command_remains_opaque_not_assumed_hidden(self):
        prefix = r'\renewcommand{\includegraphics}[2][]{#1 #2}'
        result = compare_layout_policy(prefix+doc(), prefix+doc('width=0pt'))
        self.assertEqual(result['status'], 'manual_review', result)
        self.assertEqual(result['findings'][0]['rule'], 'opaque_graphic_options')

    def test_comment_and_literal_images_are_not_operations(self):
        for inert in ['% '+r'\includegraphics[width=0pt]{fake.pdf}'+'\n',
                      r'\verb|\includegraphics[width=0pt]{fake.pdf}|']:
            self.assertEqual(compare_layout_policy(doc(), doc()+inert)['status'], 'pass')

    def test_crop_coordinate_boundaries_cannot_collide(self):
        result = compute_content_diff(doc('trim=0 0 1 20,clip'),
                                      doc('trim=0 0 12 0,clip'), layout_only=True)
        self.assertEqual(result['layout_policy']['status'], 'manual_review', result)
        self.assertEqual(result['action_required'], 'manual_review', result)

    def test_current_host_review_requires_the_registered_source_asset(self):
        # Synthetic native receipts exercise binding fields; no compiler or actual
        # human page views are claimed by this fixture.
        from test_layout_policy_host_review import CurrentProject
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            operation = (r'\begin{figure}\includegraphics[trim=0 0 4pt 0,clip]{plot.pdf}'
                         r'\caption{Measured results}\label{fig:plot}\end{figure}')
            project = CurrentProject(root, operation)
            (root / 'plot.pdf').write_bytes(b'synthetic imported scientific graphic')
            project.record_inputs(['main.tex', 'references.bib', 'plot.pdf'])
            project.set_attempt(project.attempt)
            request = project.signed()
            evidence = request['approvals'][0]['evidence']
            evidence.update(source_asset_inspected=True, visible_content_preservation_confirmed=True)
            self.assertEqual(project.validate(request)['status'], 'manual_review')
            evidence['source_asset_input'] = next(row for row in evidence['binding']['imported_inputs']['files']
                                                 if row['path'] == 'plot.pdf')
            self.assertEqual(project.validate(request)['status'], 'pass')
            (root / 'plot.pdf').write_bytes(b'different scientific graphic')
            self.assertEqual(project.validate(request)['status'], 'manual_review')

    def test_same_basename_cannot_review_another_source_asset(self):
        from test_layout_policy_host_review import CurrentProject
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            operation = (r'\begin{figure}\includegraphics[trim=0 0 4pt 0,clip]{a/plot.pdf}'
                         r'\caption{Measured results}\label{fig:plot}\end{figure}')
            project = CurrentProject(root, operation)
            for folder in ('a', 'b'):
                (root / folder).mkdir()
                (root / folder / 'plot.pdf').write_bytes(('synthetic asset '+folder).encode())
            project.record_inputs(['main.tex', 'references.bib', 'a/plot.pdf', 'b/plot.pdf'])
            project.set_attempt(project.attempt)
            request = project.signed()
            evidence = request['approvals'][0]['evidence']
            evidence.update(source_asset_inspected=True, visible_content_preservation_confirmed=True)
            inputs = evidence['binding']['imported_inputs']['files']
            evidence['source_asset_input'] = next(row for row in inputs if row['path'] == 'b/plot.pdf')
            self.assertEqual(project.validate(request)['status'], 'manual_review')
            evidence['source_asset_input'] = next(row for row in inputs if row['path'] == 'a/plot.pdf')
            self.assertEqual(project.validate(request)['status'], 'pass')


if __name__ == '__main__':
    unittest.main()
