"""Compile a copied paper and bind its PDF to the current complete input set.

The caller chooses a new directory outside the paper. No repair, model call,
candidate selection or visual acceptance is performed. System TeX dependencies
and absolute external includes are not certified by this input-set binding.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
from typing import Any

SCHEMA = 'paperfit_isolated_build.v1'
IGNORED_DIRS = {'.git', '.paperfit', '.venv', 'venv', 'node_modules', '__pycache__'}
GENERATED_SUFFIXES = {'.aux', '.log', '.fls', '.fdb_latexmk', '.synctex', '.blg', '.toc', '.out', '.nav', '.snm'}


def sha(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def is_included_input_path(path: Path, *, root: Path) -> bool:
    """Whether an ordinary path belongs to the copied input namespace.

    This also works before a report exists: writing analysis into an inventoried
    namespace would change that input set immediately after verification.
    """
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return (bool(relative.parts)
            and not any(part in IGNORED_DIRS or part.startswith('.') for part in relative.parts)
            and path.suffix.lower() not in GENERATED_SUFFIXES)


def inventory(root: Path) -> list[dict[str, Any]]:
    """Complete ordinary project inputs, including styles, bibliography and assets."""
    rows = []
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if any(part in IGNORED_DIRS or part.startswith('.') for part in relative.parts):
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('Input links outside the copied project are unsupported')
        if path.is_file() and path.suffix.lower() not in GENERATED_SUFFIXES:
            rows.append({'path': relative.as_posix(), 'bytes': path.stat().st_size, 'sha256': sha(path)})
    return rows


def _write_new(path: Path, value: Any) -> None:
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def _terminate_tree(child: subprocess.Popen) -> None:
    # TeX can leave a compiler or bibliography child running after latexmk
    # times out. Use operating-system process trees without an optional Python
    # dependency; the POSIX child is launched in its own session below.
    if os.name == 'nt':
        try:
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(child.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            child.kill()
    else:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=10)


def compile_isolated(main: Path, *, root: Path, build_dir: Path,
                     engine: str = 'pdflatex', timeout: int = 600,
                     working_directory: str = 'main_parent') -> dict[str, Any]:
    main, root, build_dir = main.resolve(), root.resolve(), build_dir.resolve()
    if engine not in {'pdflatex', 'xelatex', 'lualatex'} or type(timeout) is not int or timeout < 1:
        raise ValueError('Invalid engine or timeout')
    if working_directory not in {'main_parent', 'project_root'}:
        raise ValueError('working_directory must be main_parent or project_root')
    if not main.is_file() or not main.is_relative_to(root) or main.suffix.lower() != '.tex':
        raise ValueError('Main must be an existing project TeX file')
    if build_dir.exists() or build_dir.is_relative_to(root) or root.is_relative_to(build_dir):
        raise ValueError('Choose a new isolated directory outside the source project')
    main_rel = main.relative_to(root).as_posix()
    before = inventory(root)
    if main_rel not in {row['path'] for row in before}:
        raise ValueError('Main is excluded from the input inventory')
    compiler = shutil.which('latexmk')
    if compiler is None:
        raise ValueError('latexmk is unavailable')
    build_dir.mkdir(parents=True, exist_ok=False)
    source, output = build_dir / 'source', build_dir / 'build'
    source.mkdir(); output.mkdir()
    for row in before:
        destination = source / row['path']
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / row['path'], destination)
    if inventory(source) != before or inventory(root) != before:
        raise ValueError('Inputs changed while copying; retained directory is an incomplete attempt')
    copied_main = source / main_rel
    compiler_cwd = copied_main.parent if working_directory == 'main_parent' else source
    switch = {'pdflatex': '-pdf', 'xelatex': '-xelatex', 'lualatex': '-lualatex'}[engine]
    command = [compiler, '-norc', switch, '-g', '-interaction=nonstopmode',
               '-halt-on-error', '-file-line-error', '-no-shell-escape', '-synctex=0',
               '-outdir=' + str(output), str(copied_main)]
    started = datetime.now(timezone.utc).isoformat()
    monotonic = time.monotonic()
    code, timed_out, launch_error, termination_error = None, False, None, None
    stdout_path, stderr_path = build_dir / 'stdout.txt', build_dir / 'stderr.txt'
    environment = dict(os.environ)
    environment['MIKTEX_MPM_AUTO_INSTALL'] = '0'
    # Prevent an inherited project-specific TeX search path from silently
    # selecting a different local project instead of the copied input set.
    for key in ('TEXINPUTS', 'BIBINPUTS', 'BSTINPUTS'):
        environment.pop(key, None)
    for key in ('LC_ALL', 'LC_CTYPE', 'LC_NUMERIC', 'LC_COLLATE', 'LC_TIME'):
        environment.pop(key, None)
    with stdout_path.open('wb') as stdout, stderr_path.open('wb') as stderr:
        try:
            options = ({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt'
                       else {'start_new_session': True})
            child = subprocess.Popen(command, cwd=compiler_cwd, env=environment, stdout=stdout, stderr=stderr, **options)
            try:
                code = child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    _terminate_tree(child)
                except (OSError, subprocess.TimeoutExpired) as error:
                    # Preserve the failed attempt even when the OS cannot
                    # confirm termination; do not claim its child tree died.
                    termination_error = type(error).__name__
                code = child.returncode
        except OSError as error:
            launch_error = type(error).__name__
    try:
        stable = inventory(source) == before and inventory(root) == before
    except (OSError, ValueError):
        stable = False
    pdf = output / (main.stem + '.pdf')
    pdf_hash = sha(pdf) if pdf.is_file() else None
    log = output / (main.stem + '.log')
    record = {
        'schema': SCHEMA, 'project_root': str(root), 'main_relpath': main_rel,
        'started_utc': started, 'finished_utc': datetime.now(timezone.utc).isoformat(),
        'elapsed_seconds': round(time.monotonic() - monotonic, 3), 'engine': engine,
        'command': command, 'cwd': str(compiler_cwd), 'working_directory': working_directory,
        'returncode': code, 'timed_out': timed_out,
        'launch_error': launch_error, 'termination_error': termination_error,
        'source_inventory': before, 'source_unchanged_by_tools': stable,
        'compile_success': code == 0 and not timed_out and stable and pdf_hash is not None,
        'pdf': {'path': 'build/' + main.stem + '.pdf', 'sha256': pdf_hash},
        'log': {'path': 'build/' + main.stem + '.log', 'sha256': sha(log) if log.is_file() else None},
        'stdout_sha256': sha(stdout_path), 'stderr_sha256': sha(stderr_path),
        'freshness_basis': 'New empty build directory and complete copied project input hashes',
        'input_inventory_scope': 'All ordinary nonhidden files except documented generated suffixes and environment directories; scientific data directories are included',
        'ignored_directories': sorted(IGNORED_DIRS), 'ignored_generated_suffixes': sorted(GENERATED_SUFFIXES),
        'external_dependencies_verified': False, 'source_edits': False,
        'host_actual_page_views_established': False, 'automatic_acceptance': False,
    }
    _write_new(build_dir / 'build_record.json', record)
    return record


def verify_isolated_build(main: Path, *, root: Path, pdf: Path, build_dir: Path) -> dict[str, Any]:
    """Recheck explicit build inputs; never follow paths supplied by a receipt."""
    main, root, pdf, build_dir = main.resolve(), root.resolve(), pdf.resolve(), build_dir.resolve()
    report: dict[str, Any] = {'status': 'unknown', 'source_pdf_binding': 'unverified',
        'reason': None, 'record_path': str(build_dir / 'build_record.json'),
        'pdf_sha256': None, 'log_path': None, 'automatic_acceptance': False,
        'external_dependencies_verified': False, 'host_actual_page_views_established': False}
    record_path = build_dir / 'build_record.json'
    try:
        if not main.is_relative_to(root) or build_dir.is_relative_to(root) or root.is_relative_to(build_dir):
            raise ValueError('isolated_build_path_invalid')
        if (not record_path.is_file() or not record_path.resolve().is_relative_to(build_dir)
                or record_path.stat().st_size > 32 * 1024 * 1024):
            raise ValueError('build_record_missing_or_too_large')
        record_hash = sha(record_path)
        record = json.loads(record_path.read_text(encoding='utf-8-sig'))
        expected_pdf = build_dir / 'build' / (main.stem + '.pdf')
        expected_log = build_dir / 'build' / (main.stem + '.log')
        if not isinstance(record, dict):
            raise ValueError('build_record_identity_mismatch')
        # Earlier version1 receipts compiled at the explicitly supplied project
        # root. Keep them valid without silently reinterpreting their cwd.
        working_directory = record.get('working_directory', 'project_root')
        if working_directory not in {'main_parent', 'project_root'}:
            raise ValueError('build_record_working_directory_invalid')
        expected_cwd = (build_dir / 'source' / main.relative_to(root)).parent if working_directory == 'main_parent' else build_dir / 'source'
        if (not isinstance(record, dict) or record.get('schema') != SCHEMA
                or record.get('project_root') != str(root)
                or record.get('main_relpath') != main.relative_to(root).as_posix()
                or record.get('cwd') != str(expected_cwd)):
            raise ValueError('build_record_identity_mismatch')
        if (record.get('compile_success') is not True or type(record.get('returncode')) is not int
                or record['returncode'] != 0 or record.get('timed_out') is not False
                or record.get('launch_error') is not None or record.get('termination_error') is not None
                or record.get('source_unchanged_by_tools') is not True):
            raise ValueError('compile_not_successful')
        rows = record.get('source_inventory')
        source = build_dir / 'source'
        if (not source.is_dir() or not source.resolve().is_relative_to(build_dir)
                or not isinstance(rows, list) or not rows or rows != inventory(root) or rows != inventory(source)):
            raise ValueError('source_inventory_changed')
        if pdf != expected_pdf.resolve() or not pdf.is_file() or not pdf.is_relative_to(build_dir):
            raise ValueError('selected_pdf_not_this_build')
        pdf_hash = sha(pdf)
        if record.get('pdf') != {'path': 'build/' + main.stem + '.pdf', 'sha256': pdf_hash}:
            raise ValueError('pdf_changed')
        if not expected_log.is_file() or not expected_log.resolve().is_relative_to(build_dir):
            raise ValueError('compile_log_changed_or_missing')
        log_hash = sha(expected_log)
        if record.get('log') != {'path': 'build/' + main.stem + '.log', 'sha256': log_hash}:
            raise ValueError('compile_log_changed_or_missing')
        measured = {pdf: pdf_hash, expected_log: log_hash}
        for name in ('stdout', 'stderr'):
            path = build_dir / (name + '.txt')
            if not path.is_file() or not path.resolve().is_relative_to(build_dir) or sha(path) != record.get(name + '_sha256'):
                raise ValueError('compiler_output_changed')
            measured[path] = record[name + '_sha256']
        # Snapshot files are hashed from bounded paths generated by inventory,
        # rather than arbitrary file paths read from the JSON document.
        if sha(record_path) != record_hash or inventory(root) != rows or inventory(source) != rows:
            raise ValueError('build_changed_during_verification')
        if any(sha(path) != identity for path, identity in measured.items()):
            raise ValueError('build_artifact_changed_during_verification')
        report.update(status='current', source_pdf_binding='verified_isolated_build',
                      record_sha256=record_hash, pdf_sha256=pdf_hash,
                      log_path=str(expected_log), log_sha256=log_hash,
                      source_inventory_count=len(rows),
                      proof_scope='Current local inputs equal the retained compiled snapshot; PDF/log/output hashes equal the local compiler receipt. This is not independent authentication of a receipt or certification of external TeX dependencies.')
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as error:
        report['reason'] = str(error)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--main', required=True, type=Path)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--build-dir', required=True, type=Path)
    parser.add_argument('--engine', choices=['pdflatex', 'xelatex', 'lualatex'], default='pdflatex')
    parser.add_argument('--timeout', type=int, default=600)
    parser.add_argument('--working-directory', choices=['main_parent', 'project_root'], default='main_parent',
                        help='Relative input lookup: main directory by default, or explicit project root')
    args = parser.parse_args()
    try:
        record = compile_isolated(args.main, root=args.root, build_dir=args.build_dir,
                                  engine=args.engine, timeout=args.timeout, working_directory=args.working_directory)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps({'build_record': str(args.build_dir.resolve() / 'build_record.json'),
                      'compile_success': record['compile_success'],
                      'pdf': str(args.build_dir.resolve() / record['pdf']['path']),
                      'returncode': record['returncode'], 'automatic_acceptance': False}, indent=2))
    return 0 if record['compile_success'] else 3


if __name__ == '__main__':
    raise SystemExit(main())
