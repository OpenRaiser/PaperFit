"""Read-only physical-page visibility evidence from painted PDF text.

Text extraction can substitute accessibility ActualText and clip its bounds to
the page. Texttrace retains the actual glyph placements, including off-page
ones. This checks geometry, not scientific semantics or arbitrary figure ink.
An out-of-page placement requests independent review; it is not an automatic
claim that an intentional clip/bleed is scientific damage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

try:
    import fitz
except ImportError:
    fitz = None


LIMITS = (
    "Painted text glyph bounds only; not a complete scientific-content or image/"
    "vector-figure visibility proof. Bounds approximate font ink. Explicit clips, "
    "occlusion, white-on-white text and intended bleed require host review. "
    "No margin, whitespace-density or blank/grayscale-page rule is applied."
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _box(values: Iterable[float]) -> list[float]:
    return [round(float(value), 4) for value in values]


def _overflow(rect: Any, visible: Any) -> dict[str, float]:
    return {'left': max(0.0, visible.x0 - rect.x0),
            'right': max(0.0, rect.x1 - visible.x1),
            'top': max(0.0, visible.y0 - rect.y0),
            'bottom': max(0.0, rect.y1 - visible.y1)}


def _character_text(codepoint: int) -> str:
    try:
        return chr(codepoint)
    except (ValueError, OverflowError):
        return '\ufffd'


def _page_visibility(page: Any, number: int, tolerance_pt: float) -> dict[str, Any]:
    visible = page.rect
    # Texttrace is in unrotated, CropBox-relative coordinates. Page.rect is the
    # actual rotated view; apply the rotation matrix before testing all 4 edges.
    rotation = page.rotation_matrix
    groups: dict[Any, dict[str, Any]] = {}
    painted_glyphs = 0
    seen = set()
    for index, span in enumerate(page.get_texttrace()):
        if span.get('type') not in {0, 1, 2} or float(span.get('opacity', 1.0)) <= 0:
            continue
        seqno = span.get('seqno', f'span-{index}')
        group = groups.setdefault(seqno, {'glyphs': [], 'outside': [], 'rect': None,
                                          'overflow': dict.fromkeys(('left', 'right', 'top', 'bottom'), 0.0)})
        last_outside = None
        for char in span.get('chars', ()):
            text = _character_text(int(char[0]))
            if text.isspace():
                continue
            rect = fitz.Rect(char[3]) * rotation
            if not all(math.isfinite(v) for v in rect):
                raise ValueError('invalid glyph geometry')
            # A ligature's additional Unicode characters have glyph id -1 and
            # zero-width boxes. They are not separate painted glyphs.
            if rect.is_empty:
                if int(char[1]) == -1:
                    group['glyphs'].append(text)
                    if last_outside is not None:
                        last_outside['text'] += text
                continue
            # Fill-and-stroke can emit two traces of the same glyph placement.
            key = (span.get('font'), int(char[1]), tuple(round(v, 5) for v in rect))
            if key in seen:
                last_outside = None
                continue
            seen.add(key)
            painted_glyphs += 1
            group['glyphs'].append(text)
            group['rect'] = rect if group['rect'] is None else group['rect'] | rect
            overflow = _overflow(rect, visible)
            if max(overflow.values()) <= tolerance_pt:
                last_outside = None
                continue
            for side, amount in overflow.items():
                group['overflow'][side] = max(group['overflow'][side], amount)
            intersection = rect & visible
            last_outside = {'text': text, 'glyph_id': int(char[1]),
                            'bbox': _box(rect), 'fully_outside': intersection.is_empty}
            group['outside'].append(last_outside)
    affected = [(seqno, group) for seqno, group in groups.items() if group['outside']]
    clip_context: Any = False
    if affected:
        # Clip geometry cannot reliably be assigned to a text paint operation by
        # this API. Record uncertainty rather than guessing it proves damage.
        try:
            clip_context = any(path.get('type') == 'clip' for path in page.get_drawings(extended=True))
        except (AttributeError, TypeError, RuntimeError, ValueError):
            clip_context = 'unknown'
    findings = []
    for seqno, group in affected:
        outside = group['outside']
        bounds = fitz.Rect(outside[0]['bbox'])
        for char in outside[1:]:
            bounds |= fitz.Rect(char['bbox'])
        sides = {side: round(value, 4) for side, value in group['overflow'].items() if value > tolerance_pt}
        findings.append({
            'finding_id': f'PDF-VISIBILITY-p{number}-paint{seqno}',
            'page': number, 'kind': 'painted_text_outside_page', 'status': 'needs_review',
            'geometry_confirmed': True, 'scientific_damage_confirmed': False,
            'paint_operation_index': seqno, 'bbox': _box(group['rect']),
            'outside_bbox': _box(bounds), 'overflow_pt': sides,
            'outside_glyph_count': len(outside),
            'fully_outside_glyph_count': sum(char['fully_outside'] for char in outside),
            'painted_text_excerpt': ''.join(group['glyphs'])[:160],
            'outside_text_excerpt': ''.join(char['text'] for char in outside)[:160],
            'glyph_examples': outside[:12], 'explicit_clip_context': clip_context,
            'review_reason': 'Non-whitespace painted glyph bounds exceed the physical visible page; inspect the rendered page and source object.',
        })
    return {'page': number, 'mediabox': _box(page.mediabox), 'cropbox': _box(page.cropbox),
            'visible_rect': _box(visible), 'rotation': page.rotation,
            'painted_glyph_count': painted_glyphs, 'findings': findings}


def check_pdf_visibility(pdf_path: str | Path, *, page_numbers: Iterable[int] | None = None,
                         tolerance_pt: float = 0.75) -> dict[str, Any]:
    """Check all pages by default; explicit selections retain coverage metadata.

    Return pass / needs_review / unavailable. None of these establishes semantic
    equivalence. A caller must not replace an incomplete check with an empty pass.
    No PDF bytes, page boxes or rendering settings are changed or saved.
    """
    if not math.isfinite(tolerance_pt) or tolerance_pt < 0:
        raise ValueError('tolerance_pt must be a finite non-negative number')
    path = Path(pdf_path)
    report: dict[str, Any] = {'schema_version': '1.0', 'source': 'pymupdf_texttrace',
                              'pdf_path': str(path.resolve()), 'pdf_sha256': None,
                              'scope': 'painted_text_physical_page_bounds',
                              'checked': False, 'status': 'unavailable', 'pages': [],
                              'findings': [], 'limits': LIMITS}
    if not path.is_file():
        return {**report, 'reason': 'pdf_missing'}
    if fitz is None:
        return {**report, 'reason': 'pymupdf_unavailable'}
    try:
        initial = path.stat()
        report['pdf_sha256'] = _sha256(path)
        with fitz.open(path) as document:
            report['page_count'] = len(document)
            if document.needs_pass:
                return {**report, 'reason': 'encrypted_pdf_requires_password'}
            numbers = list(range(1, len(document) + 1)) if page_numbers is None else sorted(set(page_numbers))
            if not numbers or any(type(number) is not int or number < 1 or number > len(document) for number in numbers):
                return {**report, 'reason': 'invalid_page_selection'}
            for number in numbers:
                page_report = _page_visibility(document[number - 1], number, tolerance_pt)
                report['pages'].append(page_report)
                report['findings'].extend(page_report['findings'])
        final = path.stat()
        if (initial.st_size, initial.st_mtime_ns) != (final.st_size, final.st_mtime_ns):
            return {**report, 'reason': 'pdf_changed_during_check'}
    except (OSError, RuntimeError, ValueError, TypeError, IndexError, AttributeError) as error:
        return {**report, 'reason': f'pdf_visibility_unavailable:{type(error).__name__}'}
    report.update(checked=True, status='needs_review' if report['findings'] else 'pass',
                  checked_page_count=len(numbers), page_numbers=numbers,
                  complete_document=len(numbers) == report['page_count'], tolerance_pt=tolerance_pt)
    report['summary'] = {'finding_count': len(report['findings']),
                         'affected_pages': sorted({item['page'] for item in report['findings']}),
                         'outside_glyph_count': sum(item['outside_glyph_count'] for item in report['findings']),
                         'fully_outside_glyph_count': sum(item['fully_outside_glyph_count'] for item in report['findings']),
                         'max_overflow_pt': max((max(item['overflow_pt'].values()) for item in report['findings']), default=0.0)}
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf')
    parser.add_argument('--page', type=int, action='append', dest='pages', help='1-based physical page; repeat to select more pages')
    parser.add_argument('--tolerance-pt', type=float, default=0.75)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        report = check_pdf_visibility(args.pdf, page_numbers=args.pages, tolerance_pt=args.tolerance_pt)
    except ValueError as error:
        parser.error(str(error))
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + '\n', encoding='utf-8')
    else:
        print(output)
    return {'pass': 0, 'needs_review': 2, 'unavailable': 3}[report['status']]


if __name__ == '__main__':
    raise SystemExit(main())
