"""Decision report paths must not replace the files being reviewed."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class GatekeeperOutputPreservationTest(unittest.TestCase):
    def test_report_cannot_replace_main_include_or_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = r'\documentclass{article}\begin{document}\input{results}\end{document}'
            (root / 'main.tex').write_text(source, encoding='utf-8')
            (root / 'results.tex').write_text('Measured accuracy is 91.2.', encoding='utf-8')
            (root / 'state.json').write_text(json.dumps({'main_tex': 'main.tex'}), encoding='utf-8')
            prior = {name: (root / name).read_bytes()
                     for name in ('main.tex', 'results.tex', 'state.json')}
            command = [sys.executable, '-X', 'utf8', '-B',
                       str(Path(__file__).with_name('gatekeeper_enforcer.py')),
                       '--root', str(root), '--state', 'state.json']
            for name in prior:
                with self.subTest(output=name):
                    result = subprocess.run(command + ['--output', name],
                                            capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    for original, data in prior.items():
                        self.assertEqual((root / original).read_bytes(), data)
            result = subprocess.run(command + ['--output', '.paperfit/reports/decision.json'],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads((root / '.paperfit/reports/decision.json').read_text())['decision'],
                             'CONTINUE')

    def test_repair_report_cannot_replace_source_plan_or_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = r'\documentclass{article}\begin{document}\input{results}\end{document}'
            (root / 'main.tex').write_text(source, encoding='utf-8')
            (root / 'results.tex').write_text('Measured accuracy is 91.2.', encoding='utf-8')
            (root / 'plan.json').write_text('{"candidates": []}', encoding='utf-8')
            (root / 'review.json').write_text('{"review": "retain"}', encoding='utf-8')
            prior = {name: (root / name).read_bytes()
                     for name in ('main.tex', 'results.tex', 'plan.json', 'review.json')}
            command = [sys.executable, '-X', 'utf8', '-B',
                       str(Path(__file__).with_name('repair_plan_executor.py')),
                       'plan.json', 'main.tex', '--root', str(root)]
            for name in prior:
                with self.subTest(output=name):
                    extra = ['--source-review-json', 'review.json'] if name == 'review.json' else []
                    result = subprocess.run(command + extra + ['--output', name],
                                            capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    for original, data in prior.items():
                        self.assertEqual((root / original).read_bytes(), data)
            result = subprocess.run(command + ['--output', '.paperfit/reports/repair.json'],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads((root / '.paperfit/reports/repair.json').read_text())['status'], 'noop')

    def test_repair_api_rejects_report_overwrite_before_applying_any_candidate(self):
        from repair_plan_executor import execute_repair_plan
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            main, plan = root / 'main.tex', root / 'plan.json'
            source = r'\documentclass{article}\begin{document}Result is 91.2.\end{document}'
            main.write_text(source, encoding='utf-8')
            plan.write_text('{"candidates": []}', encoding='utf-8')
            for output in (main, plan):
                with self.subTest(output=output), self.assertRaisesRegex(ValueError, 'repair report cannot replace'):
                    execute_repair_plan(str(plan), str(main), str(output), project_root=root)
                self.assertEqual(main.read_text(encoding='utf-8'), source)
                self.assertEqual(plan.read_text(encoding='utf-8'), '{"candidates": []}')


if __name__ == '__main__':
    unittest.main()
