"""Bind physical-page text evidence and explicit host review to the live PDF.

An intentional crop can be reviewed without weakening scientific guards. Review
must identify the measured finding, current PDF, and an inspected page image.
This is a geometry disposition, never permission to change scientific payload.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from typing import Any
try:
    from pdf_visibility_check import check_pdf_visibility, fitz
except ImportError:
    from .pdf_visibility_check import check_pdf_visibility, fitz


def sha256(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def finding_id(finding: dict[str, Any]) -> str:
    evidence = {k: finding.get(k) for k in ('page', 'bbox', 'outside_bbox',
                'overflow_pt', 'outside_glyph_count', 'fully_outside_glyph_count',
                'paint_operation_index', 'painted_text_excerpt', 'outside_text_excerpt')}
    return 'visibility:' + hashlib.sha256(json.dumps(evidence, sort_keys=True,
                        ensure_ascii=False).encode()).hexdigest()[:24]


def _current_page_image(visual: dict[str, Any], pdf: Path, root: Path, page: int) -> bool:
    """Verify a readable full-page PNG against this PDF's deterministic render.

    A file hash alone proves file identity, not that it depicts the stated page.
    Re-render at the recorded DPI so unrelated, cropped or stale images cannot
    authorize geometry. PNG avoids lossy JPEG ambiguity for this narrow path.
    """
    try:
        from PIL import Image
        dpi = visual.get('render_dpi')
        if type(dpi) is not int or not 36 <= dpi <= 300 or visual.get('page') != page or fitz is None:
            return False
        image = (root / visual['path']).resolve()
        if (not image.is_relative_to(root) or image.suffix.lower() != '.png'
                or not image.is_file() or sha256(image) != visual.get('sha256')):
            return False
        with fitz.open(pdf) as document:
            physical = document[page-1]
            if max(physical.rect.width, physical.rect.height) * dpi / 72 > 5000:
                return False
            expected = physical.get_pixmap(matrix=fitz.Matrix(dpi/72, dpi/72), alpha=False)
        with Image.open(image) as opened:
            opened.load()
            if opened.size != (expected.width, expected.height):
                return False
            actual = opened.convert('RGB')
            return hashlib.sha256(actual.tobytes()).digest() == hashlib.sha256(expected.samples).digest()
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, IndexError, ImportError):
        return False


def review_pdf_visibility(pdf: Path, *, root: Path,
                          reviews_path: Path | None = None,
                          allow_external_measurement: bool = False) -> dict[str, Any]:
    try:
        root, pdf = root.resolve(), pdf.resolve()
    except (OSError, RuntimeError, ValueError):
        return {'checked': False, 'status': 'unavailable', 'pass': False,
                'reason': 'pdf_path_unavailable', 'findings': []}
    external = not pdf.is_relative_to(root)
    if external and not allow_external_measurement:
        return {'checked': False, 'status': 'unavailable', 'pass': False,
                'reason': 'pdf_outside_project', 'findings': []}
    report = check_pdf_visibility(pdf)
    report['artifact_location'] = 'external_explicit_pdf' if external else 'project_pdf'
    for finding in report['findings']:
        finding['finding_id'] = finding_id(finding)
        finding['review_status'] = 'pending'
    entries: list[Any] = []
    review_status = 'external_measurement_only' if external else 'not_provided'
    if reviews_path is not None and not external:
        try:
            target = reviews_path.resolve()
            if target.is_relative_to(root) and target.is_file() and target.stat().st_size <= 1024 * 1024:
                payload = json.loads(target.read_text(encoding='utf-8-sig'))
                if (isinstance(payload, dict) and payload.get('schema') == 'paperfit_visibility_reviews.v1'
                        and payload.get('pdf_sha256') == report.get('pdf_sha256')
                        and isinstance(payload.get('reviews'), list)):
                    entries = payload['reviews']
                    review_status = 'current'
                else:
                    review_status = 'invalid_or_stale'
            else:
                review_status = 'missing_or_external'
        except (OSError, RuntimeError, ValueError):
            review_status = 'invalid_or_unavailable'
    for finding in report['findings']:
        for entry in entries:
            if not isinstance(entry, dict) or entry.get('finding_id') != finding['finding_id']:
                continue
            if entry.get('disposition') != 'intentional_clipping' or not str(entry.get('reason') or '').strip():
                continue
            visual = entry.get('inspected_page_image')
            if not isinstance(visual, dict) or not _current_page_image(visual, pdf, root, finding['page']):
                continue
            finding['review_status'] = 'reviewed_intentional_clipping'
            finding['review_reason'] = entry['reason']
            finding['review_image'] = visual
            break
    unresolved = [f for f in report['findings'] if f['review_status'] == 'pending']
    report['review_report_status'] = review_status
    report['unresolved_finding_count'] = len(unresolved)
    report['reviewed_finding_count'] = len(report['findings']) - len(unresolved)
    report['pass'] = bool(report.get('checked') and report.get('complete_document') and not unresolved)
    report['review_scope'] = 'physical_geometry_only; scientific/prose/template guards still apply'
    return report
