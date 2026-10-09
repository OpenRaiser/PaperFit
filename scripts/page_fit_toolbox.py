"""Read-only source inventory and measured page-fit trials for the editing host.

This tool does not edit, run a repair loop, approve candidates, or inspect images.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any
try:
    from candidate_compare import compare_candidates
    from pdf_layout_profile import profile_pdf_layout
    from host_context import evidence_freshness
    from page_objective import evaluate_page_objective
    from source_map import build_source_map, group, masked_tex, within, _conditional_ranges
    from scientific_integrity import arraystretch_declarations
    from isolated_build import is_included_input_path, verify_isolated_build
except ImportError:
    from .candidate_compare import compare_candidates
    from .pdf_layout_profile import profile_pdf_layout
    from .host_context import evidence_freshness
    from .page_objective import evaluate_page_objective
    from .source_map import build_source_map, group, masked_tex, within, _conditional_ranges
    from .scientific_integrity import arraystretch_declarations
    from .isolated_build import is_included_input_path, verify_isolated_build

REGISTERS = {
    'textfloatsep', 'floatsep', 'intextsep', 'dbltextfloatsep', 'dblfloatsep',
    'abovecaptionskip', 'belowcaptionskip', 'abovedisplayskip', 'belowdisplayskip',
    'abovedisplayshortskip', 'belowdisplayshortskip', 'itemsep', 'topsep',
    'parsep', 'partopsep', 'parskip', 'tabcolsep', 'bibsep',
}
DISPLAY_ENVS = {'equation', 'equation*', 'align', 'align*', 'gather', 'gather*',
                'multline', 'multline*', 'displaymath'}
LIST_ENVS = {'itemize', 'enumerate', 'description'}
TIERS = [
    (1, 'float_flow', 'Inspect restrictive placement, barriers, spans and float composition.',
     'Keep assets, captions, labels and reading order; compare label readability before combining figures.'),
    (2, 'local_spacing', 'Adjust one existing float/caption gap by a small measured step.',
     'Start with about 1-2pt at one actual gap; negative space needs visible separation, never overlap.'),
    (3, 'template_spacing', 'Adjust one display/list/paragraph spacing family within template rules.',
     'Use the current template value, preserve stretch/shrink, and test a small delta; body font stays fixed.'),
    (4, 'native_reflow', 'Review existing breaks and use local paragraph or operator-aware formula reflow.',
     r'End a local looseness group with \par inside it; preserve every formula operand, sign and label.'),
    (5, 'table_spacing', 'Review row padding, column padding and template-permitted local table type.',
     'Preserve ordered cells and units; inspect subscripts at reading zoom; no unreadable whole-table scaling.'),
    (6, 'equivalent_rearrangement', 'Combine retained safe layout changes, then consider information-equivalent prose reflow.',
     'Prose rewriting needs task authorization and independent semantic review; never add filler or remove information.'),
]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_inventory(main: Path, root: Path) -> dict[str, Any]:
    """Literal source anchors, including includes, without TeX expansion."""
    source_map = build_source_map(main, root=root)
    entries: list[dict[str, Any]] = []
    identities = []
    counts = {'display_environments': 0, 'list_environments': 0, 'table_environments': 0}
    # The source map does not export a per-file include-uncertainty graph.
    # Conservatively retain navigation while requiring review for the entire
    # project if a reachable path is conditional, repeated or a static fallback.
    # This also covers conditional children containing only display/list glue.
    dependency_reasons = sorted({warning.get('reason') for warning in source_map['warnings']
        if warning.get('reason') in {'conditional_tex_requires_host_review',
            'repeated_include_requires_host_review', 'source_relative_include_fallback',
            'include_cycle', 'source_file_limit', 'dynamic_include', 'external_include'}})
    uncertain = bool(dependency_reasons)
    for source in source_map['files']:
        path = (root / source['file']).resolve()
        if not within(path, root):
            raise ValueError('source path escaped project root')
        identity = _sha(path)
        text = path.read_text(encoding='utf-8-sig', errors='replace')
        active = masked_tex(text)
        conditions = _conditional_ranges(active)
        for terminal in re.finditer(r'\\(?:end\s*\{document\}|endinput(?![A-Za-z@]))', active):
            if any(start <= terminal.start() < end for start, end in conditions):
                continue
            if re.match(r'\\end\s*\{', terminal[0]):
                stop = terminal.start()
            else:
                stop = active.find('\n', terminal.end())
                stop = len(active) if stop < 0 else stop
            active = active[:stop]
            break
        def add(kind: str, start: int, end: int, **data: Any) -> None:
            entries.append({'kind': kind, 'file': source['file'],
                            'line': text.count('\n', 0, start)+1,
                            'source_sha256': identity, 'syntax': text[start:end],
                            'anchor_status': 'requires_host_review' if uncertain else 'literal',
                            **data})
        for match in re.finditer(r'\\begin\s*\{([^{}]+)\}', active):
            env = match[1]
            if env in DISPLAY_ENVS:
                counts['display_environments'] += 1
                add('display', match.start(), match.end(), environment=env)
            elif env in LIST_ENVS:
                counts['list_environments'] += 1
                add('list', match.start(), match.end(), environment=env)
            elif env in {'tabular', 'tabular*', 'tabularx', 'longtable', 'array'}:
                counts['table_environments'] += 1
                add('table_structure', match.start(), match.end(), environment=env)
        for match in re.finditer(r'\\(?:vspace\*?|setlength|addtolength)(?![A-Za-z@])', active):
            first = group(active, match.end())
            if not first:
                continue
            if match[0].startswith(r'\vspace'):
                add('local_spacing', match.start(), first[1], value=first[0])
                continue
            register = first[0].strip().lstrip('\\')
            second = group(active, first[1])
            if register in REGISTERS and second:
                add('spacing_register', match.start(), second[1],
                    register=register, value=second[0])
        for match in re.finditer(r'\\('+'|'.join(sorted(REGISTERS))+r')(?![A-Za-z@])\s*=\s*([^\n%{}]+)', active):
            # Lexical hint only: a register may instead be read by \the or
            # consumed by a custom macro. Do not certify an assignment here.
            add('direct_spacing_assignment', match.start(), match.end(), register=match[1],
                anchor_status='requires_host_review', assignment_status='unverified_context',
                context_caution='Inspect the full source context; this occurrence may be a register read or macro argument, and the hint can include following body text.')
        for match in re.finditer(r'\\(?:FloatBarrier|clearpage|cleardoublepage|newpage|pagebreak|nopagebreak|looseness)(?![A-Za-z@])', active):
            add('flow_control', match.start(), match.end())
        for declaration in arraystretch_declarations(text[:len(active)]):
            positive = declaration['value'] > 0
            add('table_spacing', declaration['start'], declaration['end'],
                register='arraystretch', value=declaration['value_text'],
                numeric_value=declaration['value'], command=declaration['command'],
                value_status='positive_literal' if positive else 'nonpositive_requires_review',
                anchor_status='requires_host_review' if uncertain or not positive else 'literal')
        if _sha(path) != identity:
            raise ValueError('source changed while inventory was being measured')
        identities.append({'file': source['file'], 'sha256': identity,
                           'anchor_status': 'requires_host_review' if uncertain else 'literal'})
    return {'files': identities, 'objects': source_map['objects'], 'entries': entries,
            'counts': counts, 'warnings': source_map['warnings'],
            'coverage': source_map['object_marker_coverage'],
            'navigation_scope': {'status': 'requires_host_review' if uncertain else 'literal',
                'uncertainty_reasons': dependency_reasons,
                'policy': 'Conservative project-wide review when include reachability is uncertain.'},
            'counts_scope': 'Literal markers in statically reachable sources; uncertain entries do not establish executed TeX counts.',
            'limitations': source_map['limitations']}


def _log_hints(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {'status': 'missing', 'path': str(path), 'blockers': [], 'review': []}
    identity = _sha(path)
    content = path.read_text(encoding='utf-8', errors='replace')
    blockers, review = [], []
    for number, line in enumerate(content.splitlines(), 1):
        if (line.startswith('!') or re.search(
            r'Fatal error|Emergency stop|undefined references|undefined citations|(?:Reference|Citation).+undefined', line, re.I)):
            blockers.append({'line': number, 'text': line[:400]})
        elif re.search(r'Overfull \\[hv]box|Float too large|Too many unprocessed floats', line):
            review.append({'line': number, 'text': line[:400]})
    if _sha(path) != identity:
        return {'status': 'changed_during_read', 'path': str(path), 'blockers': [], 'review': []}
    return {'status': 'available', 'path': str(path), 'sha256': identity,
            'blockers': blockers, 'review': review,
            'source_pdf_binding': 'not_verified', 'compile_success': 'not_established'}


def build_toolbox(main: Path, *, root: Path | None = None, pdf: Path | None = None,
                  target_pages: int, page_policy: str = 'exact',
                  page_budget_scope: str = 'with_appendix', log: Path | None = None,
                  defects_stable: bool = False, before_pdf: Path | None = None,
                  attempt_tier: int | None = None, data_dir: Path | None = None,
                  build_dir: Path | None = None) -> dict[str, Any]:
    if type(target_pages) is not int or target_pages < 1:
        raise ValueError('target_pages must be a positive integer')
    if page_policy not in {'exact', 'at_most'}:
        raise ValueError('page_policy must be exact or at_most')
    if page_budget_scope not in {'main_body', 'with_refs', 'with_appendix'}:
        raise ValueError('unsupported page_budget_scope')
    if attempt_tier is not None and (type(attempt_tier) is not int or attempt_tier not in range(1, 7) or before_pdf is None):
        raise ValueError('attempt_tier needs a before PDF and must be 1-6')
    main = main.resolve()
    if root is not None:
        root = root.resolve()
        root_resolution = 'explicit'
    elif within(main, Path.cwd().resolve()):
        root = Path.cwd().resolve()
        root_resolution = 'working_directory'
    else:
        root = main.parent
        root_resolution = 'main_parent_fallback'
    pdf = (pdf or (build_dir.resolve() / 'build' / (main.stem + '.pdf')
                   if build_dir is not None else main.with_suffix('.pdf'))).resolve()
    inventory = source_inventory(main, root)
    profile = profile_pdf_layout(pdf)
    count = profile.get('page_count') if profile.get('checked') and profile.get('complete_document') else None
    # Body and reference boundaries are not inferable from text extents.
    comparable = count is not None and page_budget_scope == 'with_appendix'
    met = (count == target_pages if page_policy == 'exact' else count <= target_pages) if comparable else None
    direction = ('compress' if count > target_pages else 'expand' if count < target_pages and page_policy == 'exact' else 'hold') if comparable else 'unknown'
    freshness = evidence_freshness(main, root, (data_dir or root/'data').resolve())
    identity = freshness.get('identity', {})
    bound = (freshness.get('manifest_status') == 'available' and
             all(identity.get(name+'_matches_snapshot') is True for name in ('main_tex','source_set','pdf')) and
             freshness.get('checks', {}).get('pdf_not_older_than_source_set', {}).get('pass') is True and
             bool(profile.get('pdf_sha256')) and profile['pdf_sha256'] == identity.get('pdf', {}).get('sha256') and
             pdf == main.with_suffix('.pdf'))
    isolated = (verify_isolated_build(main, root=root, pdf=pdf, build_dir=build_dir)
                if build_dir is not None else None)
    if isolated is not None:
        # An explicitly selected build must stand on its own evidence. A failed
        # isolated binding cannot inherit a neighboring PDF's runtime clearance.
        bound = (isolated.get('status') == 'current' and
                 profile.get('pdf_sha256') == isolated.get('pdf_sha256'))
    selected_log = (Path(isolated['log_path']) if isolated and bound else
                    log or main.with_suffix('.log'))
    log_hints = _log_hints(selected_log.resolve())
    additional_log_hints = (_log_hints(log.resolve()) if log is not None
                            and log.resolve() != selected_log.resolve() else None)
    if isolated and bound:
        if log_hints.get('sha256') != isolated.get('log_sha256'):
            bound = False
            isolated = dict(isolated, status='unknown', source_pdf_binding='unverified',
                            reason='compile_log_changed_during_diagnostic_read')
        else:
            log_hints['source_pdf_binding'] = 'verified_isolated_build'
    objective = evaluate_page_objective(target_pages=target_pages, page_policy=page_policy,
        page_budget_scope=page_budget_scope, page_count=count, measured_scope='with_appendix',
        evidence_status='current' if bound else 'unverified_source_binding',
        pdf_sha256=profile.get('pdf_sha256'), measurement_source='complete_pdf_layout_profile')
    phase = ('repair_defects' if log_hints['blockers'] else
             'needs_current_measurement' if count is None else
             'needs_scope_measurement' if not comparable else
             'needs_current_build' if not bound else
             'requires_host_defect_review' if not defects_stable else
             'verify_goal' if objective['target_met'] is True else 'page_fit')
    steps = []
    for tier, family, action, caution in TIERS:
        kinds = {1: {'flow_control'}, 2: {'local_spacing'},
                 3: {'spacing_register', 'direct_spacing_assignment', 'display', 'list'},
                 4: {'flow_control', 'display'}, 5: {'table_structure', 'table_spacing'}, 6: set()}[tier]
        anchors = [entry for entry in inventory['entries'] if entry['kind'] in kinds]
        objects = inventory['objects'] if tier == 1 else [o for o in inventory['objects'] if o['kind'] == 'table'] if tier == 5 else []
        steps.append({'tier': tier, 'family': family, 'action': action, 'caution': caution,
                      'anchors': anchors, 'objects': objects,
                      'status': 'requires_host_selection', 'predicted_pages_saved': None})
    report: dict[str, Any] = {
        'schema_version': '1.0', 'scope': 'host_page_fit_toolbox',
        'main': str(main), 'root': str(root), 'root_resolution': root_resolution,
        'source_inventory': inventory,
        'pdf_identity': {'path': str(pdf), 'sha256': profile.get('pdf_sha256'),
                         'source_pdf_binding': ('verified_isolated_build' if isolated else 'verified_runtime_snapshot') if bound else 'not_verified'},
        'page_objective': objective, 'artifact_freshness': freshness,
        'isolated_build_binding': isolated,
        'observed_pdf_objective': {'policy': page_policy, 'scope': page_budget_scope,
                           'target_pages': target_pages, 'physical_pdf_pages': count,
                           'measured_scope_pages': count if comparable else None,
                           'met': met, 'direction': direction,
                           'status': 'measured_pdf_only' if comparable else 'unknown',
                           'reason': None if comparable else 'Missing complete PDF or unverified body/reference/appendix boundaries.'},
        'phase': phase, 'defects_stable': {'host_asserted': defects_stable,
                                         'automatically_verified': False},
        'log_hints': log_hints, 'additional_log_hints': additional_log_hints,
        'layout_profile': profile, 'tiers': steps,
        'trial': None, 'automatic_acceptance': False,
        'review_required': {
            'physical_pages': list(range(1, count+1)) if count is not None else [],
            'actual_host_page_views': 'not_established',
            'checks': ['Fresh compile and full render of this source with the original engine.',
                       'Scientific payload, prose, assets, labels, citations and template preserved.',
                       'Every page viewed; changed objects at reading zoom and neighboring pages compared.',
                       'No new overflow, unresolved references, hidden content or cramped/unreadable objects.',
                       'Compare with previous and useful retained candidates before keep/rollback.']},
        'discipline': ['Fix other defects first; page-fit is the final repair phase.',
                       'Change one tier at a time, then compile/render/review the entire paper.',
                       'Rollback a regressive trial only; keep useful proven fixes and try a different intervention.',
                       'An exact target is bidirectional. A one-page miss is unfinished; continue within the authorized task budget.',
                       'Expansion restores natural spacing/readability or float flow; never pad pages or add filler.',
                       'A 1-2pt repeated gap or one-line reflow is a micro trial, not a promised 0.1-page saving. Measure actual PDFs.'],
        'limits': ['Inventory is literal source navigation, not expanded TeX or proof of template permission.',
                   'Physical PDF page equality does not prove a current source build, visible content, readability or success.',
                   ('The selected isolated build log is hash-bound with its compiled inputs and PDF; this is not independent compiler authentication.'
                    if isolated and bound else
                    'An explicit or neighboring log is a diagnostic hint, not current-source clearance; inspect its identity independently.'),
                   'This tool never substitutes CV or a clean report for actual host visual inspection.'],
    }
    if before_pdf is not None:
        comparison = compare_candidates(before_pdf, pdf, target_pages=target_pages,
                                        page_policy=page_policy if page_budget_scope == 'with_appendix' else 'none')
        report['trial'] = {'tier': attempt_tier, 'comparison': comparison,
                           'decision': 'pending_host_content_template_and_full_visual_review'}
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--main', type=Path, required=True)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--pdf', type=Path)
    parser.add_argument('--data-dir', type=Path, help='Existing runtime evidence directory for source/PDF binding')
    parser.add_argument('--build-dir', type=Path, help='Explicit isolated-build directory with compiled input/PDF hashes')
    parser.add_argument('--target-pages', type=int, required=True)
    parser.add_argument('--page-policy', choices=['exact', 'at_most'], default='exact')
    parser.add_argument('--page-budget-scope', choices=['main_body', 'with_refs', 'with_appendix'], default='with_appendix')
    parser.add_argument('--log', type=Path)
    parser.add_argument('--defects-stable', action='store_true', help='Host assertion after defect review; not automatic clearance')
    parser.add_argument('--before-pdf', type=Path)
    parser.add_argument('--attempt-tier', type=int)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        report = build_toolbox(args.main, root=args.root, pdf=args.pdf,
                               target_pages=args.target_pages, page_policy=args.page_policy,
                               page_budget_scope=args.page_budget_scope, log=args.log,
                               defects_stable=args.defects_stable, before_pdf=args.before_pdf,
                               attempt_tier=args.attempt_tier, data_dir=args.data_dir,
                               build_dir=args.build_dir)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        output = args.output.resolve()
        inputs = {args.main.resolve(), args.main.with_suffix('.pdf').resolve(),
                  Path(report['pdf_identity']['path']).resolve(),
                  (args.log or args.main.with_suffix('.log')).resolve()}
        if report['log_hints'].get('path'):
            inputs.add(Path(report['log_hints']['path']).resolve())
        if report.get('additional_log_hints'):
            inputs.add(Path(report['additional_log_hints']['path']).resolve())
        if args.build_dir is not None:
            inputs.update((args.build_dir / name).resolve() for name in (
                'build_record.json', 'stdout.txt', 'stderr.txt',
                'build/' + args.main.stem + '.pdf', 'build/' + args.main.stem + '.log'))
        inputs.update((Path(report['root'])/item['file']).resolve() for item in report['source_inventory']['files'])
        if args.before_pdf:
            inputs.add(args.before_pdf.resolve())
        if output in inputs or output.exists():
            parser.error('use a new report path; never overwrite input or previous evidence')
        if args.build_dir is not None and (
                is_included_input_path(output, root=Path(report['root'])) or
                is_included_input_path(output, root=args.build_dir / 'source')):
            parser.error('report would change the compiled input set; use .paperfit/reports/ or an external report directory')
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('x', encoding='utf-8') as stream:
            stream.write(encoded)
    print(encoded)
    return 0 if report['observed_pdf_objective']['physical_pdf_pages'] is not None else 3


if __name__ == '__main__':
    raise SystemExit(main())
