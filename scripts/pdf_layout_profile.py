"""Read-only page and type-size observations to guide actual host inspection.

Short terminal lines are inspection candidates, not certified widows/orphans.
PDF text blocks do not reliably represent source paragraphs or table cells.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any
import fitz


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _quantile(sizes: Counter, proportion: float) -> float | None:
    total = sum(sizes.values())
    if not total:
        return None
    threshold = total * proportion
    count = 0
    for size, weight in sorted(sizes.items()):
        count += weight
        if count >= threshold:
            return size
    return max(sizes)


def profile_pdf_layout(pdf: str | Path) -> dict[str, Any]:
    """Bind measurements to a stable actual PDF, without acceptance decisions."""
    path = Path(pdf).resolve()
    result: dict[str, Any] = {
        'schema_version': '1.0', 'pdf_path': str(path), 'pdf_sha256': None,
        'checked': False, 'complete_document': False, 'page_count': None,
        'font_size_p10_pt': None, 'font_size_median_pt': None, 'pages': [],
        'automatic_acceptance': False,
        'limits': [
            'Extracted text geometry and size are inspection hints, not painted-bound or semantic verification.',
            'Short terminal lines are not certified paragraph endings, widows, or orphans.',
            'The restricted Latin-word terminal-line heuristic misses other languages and line patterns; zero candidates is not proof of absence.',
            'Small text may legitimately belong to footnotes, figures, or mathematics; inspect its actual page and template.',
            'Text extent and word counts do not measure all image/vector space or certify an empty/sparse page.',
            'No source/PDF binding, source preservation, or actual host page views are established.'],
    }
    try:
        identity = _sha(path)
        all_sizes: Counter = Counter()
        pages = []
        with fitz.open(path) as document:
            if document.needs_pass or document.page_count < 1:
                raise ValueError('PDF is encrypted or has no pages')
            for number, page in enumerate(document, 1):
                sizes: Counter = Counter()
                short_lines = []
                extent = None
                characters = 0
                words = 0
                for block in page.get_text('dict').get('blocks', []):
                    if block.get('type') != 0:
                        continue
                    lines = []
                    for line in block.get('lines', []):
                        spans = [s for s in line.get('spans', []) if s.get('text', '').strip()]
                        text = ''.join(s.get('text', '') for s in spans).strip()
                        if not text:
                            continue
                        raw_rect = fitz.Rect(line['bbox'])
                        rect = raw_rect * page.rotation_matrix
                        if rect.is_empty or not all(math.isfinite(x) for x in rect):
                            continue
                        extent = rect if extent is None else extent | rect
                        characters += len(re.sub(r'\s+', '', text))
                        words += len(re.findall(r'\S+', text))
                        for span in spans:
                            size = float(span.get('size', 0))
                            if math.isfinite(size) and size > 0:
                                sizes[round(size, 2)] += len(re.sub(r'\s+', '', span['text']))
                        dx, dy = line.get('dir', (1.0, 0.0))
                        magnitude = math.hypot(dx, dy)
                        advance = ((abs(dx) * raw_rect.width + abs(dy) * raw_rect.height) / magnitude
                                   if magnitude > 0 and math.isfinite(magnitude) else None)
                        lines.append((text, rect, spans, advance))
                    # Restrict to multi-line text blocks with ordinary word-like
                    # final text. Equation numbers, single captions and headings
                    # should not become automatic paragraph defects.
                    if len(lines) >= 3:
                        text, rect, spans, advance = lines[-1]
                        if re.fullmatch(r"[A-Za-z][A-Za-z'’\-]{2,}[.,;:!?]?", text):
                            # Reading advance is independent of page rotation.
                            # Physical bbox width becomes line height at 90°.
                            width = max((a for _, _, _, a in lines if a is not None), default=0)
                            if advance is not None and width > 0 and advance < 0.35 * width:
                                short_lines.append({'text': text, 'bbox': list(rect),
                                    'width_fraction_of_block': round(advance / width, 3),
                                    'classification': 'short_terminal_line_requires_page_and_source_review'})
                all_sizes.update(sizes)
                pages.append({'physical_page': number, 'width_pt': page.rect.width,
                    'height_pt': page.rect.height, 'text_characters': characters,
                    'text_words': words, 'text_extent_bbox': list(extent) if extent is not None else None,
                    'font_size_p10_pt': _quantile(sizes, 0.1),
                    'font_size_median_pt': _quantile(sizes, 0.5),
                    'font_sizes_by_text_characters': [{'size_pt': s, 'characters': n} for s, n in sorted(sizes.items())],
                    'short_terminal_line_candidates': short_lines})
        if _sha(path) != identity:
            raise ValueError('PDF changed during profiling')
        result.update(pdf_sha256=identity, checked=True, complete_document=True,
                      page_count=len(pages), pages=pages,
                      font_size_p10_pt=_quantile(all_sizes, 0.1),
                      font_size_median_pt=_quantile(all_sizes, 0.5))
    except (OSError, ValueError, RuntimeError) as error:
        result['reason'] = str(error)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = profile_pdf_layout(args.pdf)
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        path = args.output.resolve()
        if path == args.pdf.resolve() or path.exists():
            parser.error('use a new report path; do not replace input or earlier evidence')
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8') as stream:
            stream.write(encoded)
    print(encoded)
    return 0 if report['checked'] else 3


if __name__ == '__main__':
    raise SystemExit(main())
