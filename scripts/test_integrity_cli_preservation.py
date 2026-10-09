"""A read-only integrity check must not overwrite its own input files."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class IntegrityOutputTest(unittest.TestCase):
    def test_diff_report_cannot_replace_either_manuscript(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            before, after = directory / 'before.tex', directory / 'after.tex'
            source = r'\documentclass{article}\begin{document}Result is 91.2.\end{document}'
            for path in (before, after):
                path.write_text(source, encoding='utf-8')
            command = [sys.executable, '-X', 'utf8', '-B',
                       str(Path(__file__).with_name('content_integrity_check.py')),
                       'diff', str(before), str(after)]
            for path in (before, after):
                with self.subTest(output=path):
                    result = subprocess.run(command + ['--output', str(path)],
                                            capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(before.read_text(encoding='utf-8'), source)
                    self.assertEqual(after.read_text(encoding='utf-8'), source)
            output = directory / '.paperfit/reports/diff.json'
            result = subprocess.run(command + ['--output', str(output)],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(output.read_text())['scientific_payload']['status'], 'pass')

    def test_post_report_cannot_replace_provided_baseline(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            tex, baseline = directory / 'paper.tex', directory / 'baseline.json'
            tex.write_text('Original scientific content.', encoding='utf-8')
            baseline.write_text('{"original": "retained bytes"}', encoding='utf-8')
            prior = baseline.read_bytes()
            result = subprocess.run([sys.executable, '-X', 'utf8', '-B',
                str(Path(__file__).with_name('content_integrity_check.py')), 'post',
                str(tex), '1', '--baseline', str(baseline), '--output', str(baseline)],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(baseline.read_bytes(), prior)


if __name__ == '__main__':
    unittest.main()
