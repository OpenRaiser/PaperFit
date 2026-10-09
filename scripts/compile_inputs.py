"""Bind a compiled PDF to actual local TeX inputs, including image and template files."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
try:
    from compile_result import CompileAttempt, diff_source_fingerprints, source_fingerprint
except ModuleNotFoundError:
    from .compile_result import CompileAttempt, diff_source_fingerprints, source_fingerprint

_GENERATED = {'.aux','.log','.out','.blg','.toc','.lof','.lot','.nav','.snm',
              '.vrb','.bcf','.fls','.xdv','.dvi','.fdb_latexmk'}
_EXCLUDED_DIRS = {'.git','.venv','node_modules','__pycache__','.paperfit'}

def _sha(path: Path) -> str:
    with path.open('rb') as handle:
        return hashlib.file_digest(handle,'sha256').hexdigest()

def snapshot_project_inputs(project_root: Path, main_tex: Path) -> dict[str,str]:
    """Pre-build byte hashes, held in memory; only imported paths enter the receipt."""
    root=project_root.resolve();main=main_tex.resolve();result={}
    for path in root.rglob('*'):
        relative=path.relative_to(root)
        if set(relative.parts)&_EXCLUDED_DIRS or not path.is_file():
            continue
        resolved=path.resolve()
        if not resolved.is_relative_to(root) or path==main.with_suffix('.pdf'):
            continue
        if path.suffix.lower() in _GENERATED or path.name.endswith(('.run.xml','.synctex.gz')):
            continue
        result[relative.as_posix()]=_sha(path)
    return result

def live_compile_inputs(project_root: Path, main_tex: Path) -> dict:
    """Read the canonical recorder and the complete local inputs it actually lists."""
    root=project_root.resolve();main=main_tex.resolve();fls=main.with_suffix('.fls')
    result={'schema':'compile_live_inputs.v1','files':{},'fls_path':str(fls),
            'fls_sha256':None,'unavailable':[]}
    if not fls.is_file():
        result['unavailable'].append('missing_current_recorder');return result
    result['fls_sha256']=_sha(fls)
    working=None
    inputs=[]
    saw_pwd=False
    for line in fls.read_text(encoding='utf-8',errors='replace').splitlines():
        if line.startswith('PWD '):
            pwd=Path(line[4:].strip())
            if not pwd.is_absolute() or not pwd.is_dir():
                result['unavailable'].append('invalid_recorder_pwd');working=None
            else:
                working=pwd.resolve();saw_pwd=True
        elif line.startswith('INPUT '):
            value=line[6:].strip()
            path=Path(value)
            if not path.is_absolute():
                if working is None:
                    result['unavailable'].append('input_without_recorder_pwd');continue
                path=working/path
            inputs.append(path.resolve())
    if not saw_pwd:result['unavailable'].append('missing_recorder_pwd')
    if main not in inputs:result['unavailable'].append('main_not_recorded')
    for path in inputs:
        if not path.is_relative_to(root):
            continue
        if path.suffix.lower() in _GENERATED or path.name.endswith(('.run.xml','.synctex.gz')):
            continue
        relative=path.relative_to(root).as_posix()
        if not path.is_file():
            result['unavailable'].append('missing_input:'+relative);continue
        result['files'][relative]=_sha(path)
    for relative,digest in source_fingerprint(root,main.relative_to(root).as_posix()).items():
        path=(root/relative).resolve()
        if not path.is_file() or not digest:
            result['unavailable'].append('missing_source:'+relative);continue
        result['files'][path.relative_to(root).as_posix()]=digest
    result['files']=dict(sorted(result['files'].items()))
    return result

def bind_compile_inputs(project_root: Path, main_tex: Path,
                        before: dict[str,str]) -> dict:
    current=live_compile_inputs(project_root,main_tex)
    after=current['files']
    selected_before={relative:before.get(relative) for relative in after}
    missing=[relative for relative,value in selected_before.items() if not value]
    stable_bib = [{'path':relative, 'sha256':digest, 'unchanged':True}
                  for relative,digest in after.items()
                  if Path(relative).suffix.lower() == '.bib' and before.get(relative) == digest]
    generated_bibliography = [
        {'path':relative, 'sha256':after[relative], 'status':'created_after_snapshot',
         'derivation_status':'unverified', 'source_databases':stable_bib}
        for relative in missing if Path(relative).suffix.lower() == '.bbl']
    return {'schema':'compile_input_binding.v1',
            'bound':bool(after) and not current['unavailable'] and not missing and selected_before==after,
            'before':selected_before,'after':after,
            'fls_path':current['fls_path'],'fls_sha256':current['fls_sha256'],
            'unavailable':current['unavailable'],'missing_prebuild_inputs':missing,
            'generated_bibliography':generated_bibliography,
            'scope':'Actual imported local files, including existing bibliography inputs, plus reachable TeX/Bib. Installed external dependencies excluded. New bibliography files have no pre-build hash and do not establish verified derivation.'}


def verify_native_build(main_tex: Path, *, root: Path, pdf: Path | None = None) -> dict:
    """Read the latest native attempt and recheck local inputs without approving it.

    Runtime snapshots, image views and isolated receipts are not native compile
    evidence. This needs no render/VLM/review, writes nothing, and cannot
    authenticate a compiler independently of the recorded native attempt.
    """
    root, main = root.resolve(), main_tex.resolve()
    selected = (pdf or main.with_suffix('.pdf')).resolve()
    result = {'schema':'native_build_binding.v1', 'status':'unknown',
              'source_pdf_binding':'unverified', 'pdf_path':str(selected),
              'pdf_sha256':None, 'reason':None,
              'scope':'Latest native attempt and actual local imported bytes; observation binding only, not visual/scientific acceptance or independent compiler authentication.'}
    try:
        def local(value):
            if not isinstance(value, str) or not value:
                raise ValueError('native input path unavailable')
            path = (root / value).resolve()
            if not path.is_relative_to(root):
                raise ValueError('native input path outside project')
            return path
        def hashes(values):
            if not isinstance(values, dict) or not values:
                raise ValueError('native input hashes unavailable')
            normalized = {}
            for name, digest in values.items():
                path = local(name)
                if (not isinstance(digest, str) or len(digest) != 64 or
                        any(char not in '0123456789abcdef' for char in digest)):
                    raise ValueError('native input hash invalid')
                relative = path.relative_to(root).as_posix()
                if relative in normalized:
                    raise ValueError('native input path duplicated')
                normalized[relative] = digest
            return normalized
        if not main.is_relative_to(root) or selected != main.with_suffix('.pdf'):
            raise ValueError('selected PDF is not the native main-neighbor artifact')
        journal = local('data/compile/journal.jsonl')
        if not journal.is_file() or journal.stat().st_size > 32 * 1024 * 1024:
            raise ValueError('current native compile journal unavailable')
        journal_bytes = journal.read_bytes()
        lines = journal_bytes.decode('utf-8-sig').splitlines()
        if not lines or not lines[-1].strip():
            raise ValueError('latest native compile attempt unavailable')
        attempt = json.loads(lines[-1])
        if not isinstance(attempt, dict) or attempt.get('schema_version') != 'compile_result.v2':
            raise ValueError('latest native compile attempt schema unsupported')
        if (attempt.get('success') is not True or type(attempt.get('returncode')) is not int
                or attempt['returncode'] != 0 or attempt.get('timed_out') is not False
                or attempt.get('pdf_fresh') is not True or attempt.get('restored_baseline_pdf') is True
                or attempt.get('restored_good_pdf') is True or attempt.get('partial_pdf_available') is True
                or attempt.get('current_source_compile_success') is not True):
            raise ValueError('latest native attempt is not a successful bound fresh build')
        if (local(attempt.get('main_tex')) != main or local(attempt.get('pdf_path')) != selected
                or local(attempt.get('cwd')) != main.parent
                or attempt.get('compiler') not in {'latexmk','pdflatex','xelatex','lualatex'}
                or attempt.get('engine') not in {'pdflatex','xelatex','lualatex'}
                or not isinstance(attempt.get('command'), list) or not attempt['command']):
            raise ValueError('latest native attempt identity or invocation unsupported')
        pdf_hash = _sha(selected)
        if attempt.get('pdf_sha256') != pdf_hash:
            raise ValueError('current PDF differs from latest native attempt')
        sources = hashes(source_fingerprint(root, main.relative_to(root).as_posix()))
        if (attempt.get('source_unchanged_during_compile') is not True or
                hashes(attempt.get('source_fingerprint_before')) != sources or
                hashes(attempt.get('source_fingerprint')) != sources):
            raise ValueError('current sources differ from same-attempt native hashes')
        resources = attempt.get('compile_inputs')
        if (not isinstance(resources, dict) or resources.get('schema') != 'compile_input_binding.v1'
                or resources.get('bound') is not True or resources.get('unavailable') != []
                or resources.get('missing_prebuild_inputs') != []):
            raise ValueError('latest native attempt has no complete bound imported-input receipt')
        imported = hashes(resources.get('after'))
        if hashes(resources.get('before')) != imported:
            raise ValueError('imported inputs changed during native compilation')
        def live_matches():
            live = live_compile_inputs(root, main)
            return (live.get('unavailable') == [] and local(resources.get('fls_path')) == main.with_suffix('.fls')
                    and local(live.get('fls_path')) == main.with_suffix('.fls')
                    and live.get('fls_sha256') == resources.get('fls_sha256')
                    and isinstance(resources.get('fls_sha256'), str) and len(resources['fls_sha256']) == 64
                    and hashes(live.get('files')) == imported)
        if not live_matches():
            raise ValueError('current recorder or imported bytes differ from native compilation')
        # Late barrier: a changed journal/PDF/source/resource cannot inherit an
        # earlier read's binding while this read-only report is being prepared.
        if (journal.read_bytes() != journal_bytes or _sha(selected) != pdf_hash or
                hashes(source_fingerprint(root, main.relative_to(root).as_posix())) != sources or not live_matches()):
            raise ValueError('native evidence changed during verification')
        result.update(status='current', source_pdf_binding='verified_native_inputs', pdf_sha256=pdf_hash,
                      attempt_id=attempt.get('attempt_id'), journal_path=str(journal),
                      journal_sha256=hashlib.sha256(journal_bytes).hexdigest(),
                      fls_sha256=resources['fls_sha256'], imported_input_count=len(imported))
    except (OSError, ValueError, TypeError, RuntimeError, UnicodeError) as error:
        result['reason'] = str(error)
    return result


class NativeCompileEvidence:
    """Same-attempt checks and a real native journal, shared by compiler paths.

    Instantiate only for a native compilation being run by the caller. This
    never imports an isolated-build receipt or invents a compiler invocation.
    A missing recorder can retain process success and a readable observation;
    it cannot establish current-source applicability.
    """
    def __init__(self, root: Path, main: Path, *, compiler: str):
        self.root, self.main = root.resolve(), main.resolve()
        self.main_relative = self.main.relative_to(self.root).as_posix()
        self.source_before = source_fingerprint(self.root, self.main_relative)
        self.inputs_before = snapshot_project_inputs(self.root, self.main)
        self.attempt = CompileAttempt(self.root, main_tex=self.main_relative, compiler=compiler)

    def finalize(self, report: dict, *, page_count: int | None = None,
                 stdout: str | None = None, stderr: str | None = None) -> dict:
        result = dict(report)
        process_success = result.get('success') is True
        current_source = source_fingerprint(self.root, self.main_relative)
        try:
            binding = bind_compile_inputs(self.root, self.main, self.inputs_before)
            changed = [name for name,digest in binding['after'].items()
                       if self.inputs_before.get(name) and self.inputs_before[name] != digest]
            changed += [item.split(':',1)[1] for item in binding['unavailable']
                        if item.startswith('missing_input:') and item.split(':',1)[1] in self.inputs_before]
            # Author-provided bibliography remains an input even if a failed
            # recorder no longer lists it. Do not hide cleanup/deletion drift.
            for name,digest in self.inputs_before.items():
                if Path(name).suffix.lower() == '.bbl':
                    path = self.root / name
                    if not path.is_file() or _sha(path) != digest:
                        changed.append(name)
            changed = sorted(set(changed))
        except (OSError, ValueError, RuntimeError) as error:
            binding = {'schema':'compile_input_binding.v1', 'bound':False,
                       'before':{}, 'after':{}, 'unavailable':[str(error)],
                       'missing_prebuild_inputs':[], 'generated_bibliography':[]}
            changed = []
        source_unchanged = bool(self.source_before) and self.source_before == current_source
        success = bool(process_success and source_unchanged and not changed)
        pdf = self.main.with_suffix('.pdf')
        current = bool(success and pdf.is_file() and binding.get('bound') is True)
        result.update(success=success, compile_process_success=process_success,
                      source_fingerprint=current_source, source_fingerprint_before=self.source_before,
                      source_unchanged_during_compile=source_unchanged, compile_inputs=binding,
                      imported_inputs_changed_during_compile=bool(changed),
                      changed_imported_inputs=changed, current_source_compile_success=current,
                      source_pdf_binding='verified_native_inputs' if current else 'unverified',
                      pdf_path=str(pdf) if success and pdf.is_file() else None,
                      preview_pdf_path=str(pdf) if pdf.is_file() and not current else None,
                      compile_observation_available=pdf.is_file(),
                      compile_evidence_scope='Native local-input receipt; process success, source applicability and visual acceptance are separate.')
        if process_success and not success:
            result['case_status'] = 'source_inputs_changed_during_compile'
        fingerprint_store = self.root / 'data' / 'compile' / 'last_fingerprint.json'
        previous = {}
        try:
            if fingerprint_store.is_file():
                previous = json.loads(fingerprint_store.read_text(encoding='utf-8'))
            if not isinstance(previous, dict):
                previous = {}
        except (OSError, ValueError):
            previous = {}
        native_log = self.main.with_suffix('.log')
        full_stdout = stdout if stdout is not None else result.get('stdout_tail', '')
        full_stderr = stderr if stderr is not None else result.get('stderr_tail', '')
        try:
            v2 = self.attempt.finalize(
                command=result.get('command', []), cwd=result.get('cwd', str(self.main.parent)),
                returncode=result.get('returncode', 1), timed_out=bool(result.get('timeout')),
                stdout=full_stdout, stderr=full_stderr,
                log_source=native_log if native_log.is_file() else None,
                log_text=full_stdout + '\n' + full_stderr,
                pdf_path=pdf if pdf.is_file() else None, success=success,
                page_count=page_count if page_count is not None else 0,
                changed_files=diff_source_fingerprints(previous, current_source) if previous else [],
                extra=result)
            result.update(v2)
            fingerprint_store.parent.mkdir(parents=True, exist_ok=True)
            fingerprint_store.write_text(json.dumps(current_source, ensure_ascii=False), encoding='utf-8')
        except (OSError, ValueError, TypeError) as error:
            result.update(compile_evidence_status='unavailable', compile_evidence_error=str(error),
                          current_source_compile_success=False, source_pdf_binding='unverified',
                          preview_pdf_path=str(pdf) if pdf.is_file() else None)
        return result
