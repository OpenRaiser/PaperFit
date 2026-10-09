"""Bind a compiled PDF to actual local TeX inputs, including image and template files."""
from __future__ import annotations
import hashlib
from pathlib import Path
try:
    from compile_result import source_fingerprint
except ModuleNotFoundError:
    from .compile_result import source_fingerprint

_GENERATED = {'.aux','.log','.out','.bbl','.blg','.toc','.lof','.lot','.nav','.snm',
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
    return {'schema':'compile_input_binding.v1',
            'bound':bool(after) and not current['unavailable'] and not missing and selected_before==after,
            'before':selected_before,'after':after,
            'fls_path':current['fls_path'],'fls_sha256':current['fls_sha256'],
            'unavailable':current['unavailable'],'missing_prebuild_inputs':missing,
            'scope':'Actual imported files within project plus reachable TeX/Bib; installed external dependencies excluded. Generated bibliography is checked through its source database.'}
