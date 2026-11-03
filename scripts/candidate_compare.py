"""Compare preserved candidate PDFs without ranking overall quality or editing."""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path
from typing import Any
try:
    from pdf_visibility_check import check_pdf_visibility
    from pdf_layout_profile import profile_pdf_layout
except ImportError:
    from .pdf_visibility_check import check_pdf_visibility
    from .pdf_layout_profile import profile_pdf_layout


def _measure(path: Path) -> dict[str, Any]:
    raw = check_pdf_visibility(path)
    complete = raw.get('checked') is True and raw.get('complete_document') is True
    count = raw.get('page_count')
    outside = raw.get('summary', {}).get('outside_glyph_count')
    identity = raw.get('pdf_sha256')
    valid = (complete and type(count) is int and count > 0 and type(outside) is int and outside >= 0
             and isinstance(identity, str) and re.fullmatch(r'[0-9a-f]{64}', identity) is not None)
    profile = profile_pdf_layout(path)
    profile_bound = profile.get('checked') is True and profile.get('pdf_sha256') == identity
    tail = profile['pages'][-1] if profile_bound and profile['pages'] else {}
    return {'pdf_path': str(path.resolve()), 'pdf_sha256': raw.get('pdf_sha256'),
            'measurement_available': valid, 'page_count': count if valid else None,
            'painted_outside_glyphs': outside if valid else None,
            'painted_bounds_status': raw.get('status'), 'reason': raw.get('reason'),
            'source_pdf_binding': 'not_checked', 'actual_host_page_views': 'not_established',
            'layout_observations': {'available': profile_bound,
                'font_size_p10_pt': profile.get('font_size_p10_pt') if profile_bound else None,
                'font_size_median_pt': profile.get('font_size_median_pt') if profile_bound else None,
                'last_page_text_words': tail.get('text_words'),
                'last_page_text_extent_bbox': tail.get('text_extent_bbox'),
                'short_terminal_line_candidates': sum(len(p['short_terminal_line_candidates']) for p in profile['pages']) if profile_bound else None,
                'scope': 'Extracted type size/text extent only; not a readability, whitespace, or widow verdict.'}}


def compare_candidates(before_pdf: str | Path, after_pdf: str | Path, *,
                       target_pages: int | None = None, page_policy: str = 'none') -> dict[str, Any]:
    """Return measured differences; all decisions remain with the editing host."""
    if page_policy not in {'none', 'at_most', 'exact'}:
        raise ValueError('page_policy must be none, at_most or exact')
    if target_pages is not None and (type(target_pages) is not int or target_pages < 1):
        raise ValueError('target_pages must be a positive integer')
    if page_policy != 'none' and target_pages is None:
        raise ValueError('a page policy requires target_pages')
    before = _measure(Path(before_pdf))
    after = _measure(Path(after_pdf))
    available = before['measurement_available'] and after['measurement_available']
    report: dict[str, Any] = {'schema_version': '1.0', 'scope': 'preserved_pdf_measured_changes',
        'before': before, 'after': after, 'page_policy': page_policy, 'target_pages': target_pages,
        'status': 'unavailable', 'changes': {}, 'warnings': [], 'improvements': [],
        'selected_candidate': None, 'automatic_acceptance': False,
        'review_prompts': [
            'Check the actual requested page policy before trading it for local table/formula improvements.',
            'Inspect small table/figure type at readable zoom and compare retained checkpoints, not just full-page thumbnails.',
            'Review short terminal lines and final-page text extent against source and actual pages; they are not certified defects.'],
        'limits': [
            'No source/PDF binding, compiler success, scientific preservation or actual host views established.',
            'Painted text physical bounds exclude image/vector visibility, internal overlap, font readability and template compliance.',
            'Raw glyph bounds can include intentional crop cases; inspect the current full pages and evidence-bound reviews.',
            'Page policy is an explicit user objective; exact equality and an upper bound are different.',
            'Measured improvement does not prove overall quality. Keep checkpoints and assess tradeoffs before nomination.']}
    if not available:
        report['warnings'].append({'kind': 'measurement_unavailable',
            'evidence': 'One or both PDFs are unreadable, missing or incompletely measured; missing counts are unknown.'})
        return report
    report['same_pdf_identity'] = before['pdf_sha256'] == after['pdf_sha256']
    report['changes'] = {'page_count_delta': after['page_count'] - before['page_count'],
                        'painted_outside_glyph_delta': after['painted_outside_glyphs'] - before['painted_outside_glyphs']}
    if page_policy != 'none':
        def meets(count: int) -> bool:
            return count == target_pages if page_policy == 'exact' else count <= target_pages
        old_met, new_met = meets(before['page_count']), meets(after['page_count'])
        distance = lambda count: abs(count-target_pages) if page_policy == 'exact' else max(0, count-target_pages)
        old_distance, new_distance = distance(before['page_count']), distance(after['page_count'])
        report['page_constraint'] = {'before_met': old_met, 'after_met': new_met}
        report['page_distance'] = {'before': old_distance, 'after': new_distance,
                                   'delta': new_distance-old_distance}
        if old_met and not new_met:
            report['warnings'].append({'kind': 'page_constraint_lost',
                'evidence': f"Preserved candidate had {before['page_count']} pages; current has {after['page_count']}; requested {page_policy} {target_pages}."})
        elif not old_met and new_met:
            report['improvements'].append({'kind': 'page_constraint_gained'})
        elif not new_met:
            report['warnings'].append({'kind': 'page_constraint_still_unmet'})
            if new_distance > old_distance:
                report['warnings'].append({'kind': 'page_constraint_distance_increased',
                    'evidence': f'Distance from the requested page objective increased from {old_distance} to {new_distance}.'})
            elif new_distance < old_distance:
                report['improvements'].append({'kind': 'page_constraint_distance_reduced',
                                              'before': old_distance, 'after': new_distance})
    delta = report['changes']['painted_outside_glyph_delta']
    if delta > 0:
        report['warnings'].append({'kind': 'painted_outside_glyphs_increased',
                                  'evidence': f'Raw physical-bound count increased by {delta}; inspect actual pages and crop intent.'})
    elif delta < 0:
        report['improvements'].append({'kind': 'painted_outside_glyphs_decreased', 'count': -delta})
    report['status'] = 'needs_host_review' if report['warnings'] else 'measured_no_regression'
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before-pdf', type=Path, required=True)
    parser.add_argument('--after-pdf', type=Path, required=True)
    parser.add_argument('--target-pages', type=int)
    parser.add_argument('--page-policy', choices=['none', 'at_most', 'exact'], default='none')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        report = compare_candidates(args.before_pdf, args.after_pdf,
                                    target_pages=args.target_pages, page_policy=args.page_policy)
    except ValueError as error:
        parser.error(str(error))
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        output = args.output.resolve()
        if output in {args.before_pdf.resolve(), args.after_pdf.resolve()}:
            parser.error('output cannot replace either input PDF')
        if output.exists():
            parser.error('output exists; preserve each comparison in a new report')
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            with output.open('x', encoding='utf-8') as handle:
                handle.write(encoded)
        except FileExistsError:
            parser.error('output exists; preserve each comparison in a new report')
    print(encoded)
    return {'measured_no_regression': 0, 'needs_host_review': 2, 'unavailable': 3}[report['status']]


if __name__ == '__main__':
    raise SystemExit(main())
