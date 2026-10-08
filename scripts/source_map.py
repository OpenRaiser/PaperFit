"""Position-preserving static LaTeX navigation, without TeX expansion or evaluation."""
from __future__ import annotations
import re
from pathlib import Path
from typing import Any

LITERAL_ENVS = {'verbatim', 'verbatim*', 'Verbatim', 'lstlisting', 'minted', 'comment'}
COMMAND_DEFINITIONS = {'newcommand', 'renewcommand', 'providecommand', 'DeclareRobustCommand'}
ENV_DEFINITIONS = {'newenvironment', 'renewenvironment', 'provideenvironment'}
DOCUMENT_DEFINITIONS = {'NewDocumentCommand', 'RenewDocumentCommand', 'ProvideDocumentCommand', 'DeclareDocumentCommand'}
DOCUMENT_ENV_DEFINITIONS = {'NewDocumentEnvironment', 'RenewDocumentEnvironment', 'ProvideDocumentEnvironment', 'DeclareDocumentEnvironment'}
DEFINITIONS = COMMAND_DEFINITIONS | ENV_DEFINITIONS | DOCUMENT_DEFINITIONS | DOCUMENT_ENV_DEFINITIONS | {'def', 'gdef', 'edef', 'xdef'}


def group(text: str, pos: int, opener: str = '{', closer: str = '}') -> tuple[str, int] | None:
    while pos < len(text) and text[pos].isspace():
        pos += 1
    if pos >= len(text) or text[pos] != opener:
        return None
    start, depth, braces = pos + 1, 1, 0
    pos += 1
    while pos < len(text):
        if text[pos] == '\\' and pos + 1 < len(text):
            # Consume one control symbol, including \\, before interpreting braces.
            pos += 2
            continue
        if opener == '[' and text[pos] == '{':
            braces += 1
        elif opener == '[' and text[pos] == '}' and braces:
            braces -= 1
        elif text[pos] == opener and not braces:
            depth += 1
        elif text[pos] == closer and not braces:
            depth -= 1
            if depth == 0:
                return text[start:pos], pos + 1
        pos += 1
    return None


def _blank(chars: list[str], start: int, end: int) -> None:
    for i in range(start, min(end, len(chars))):
        if chars[i] not in '\r\n':
            chars[i] = ' '


def _comment_syntax(text: str) -> str:
    """Comments masked for parsing definition groups; offsets stay unchanged."""
    chars = list(text)
    i = 0
    while i < len(text):
        if text[i] == '\\':
            i += 2
        elif text[i] == '%':
            end = text.find('\n', i)
            end = len(text) if end < 0 else end
            _blank(chars, i, end)
            i = end
        else:
            i += 1
    return ''.join(chars)


def _definition_end(text: str, pos: int, name: str) -> int | None:
    while pos < len(text) and text[pos].isspace():
        pos += 1
    if pos < len(text) and text[pos] == '*':
        pos += 1
    while pos < len(text) and text[pos].isspace():
        pos += 1
    target = group(text, pos)
    if target:
        pos = target[1]
    else:
        target_command = re.match(r'\\(?:[A-Za-z@]+|.)', text[pos:])
        if not target_command:
            return None
        pos += target_command.end()
    if name in {'def', 'gdef', 'edef', 'xdef'}:
        while pos < len(text) and text[pos] != '{':
            pos += 2 if text[pos] == '\\' else 1
    elif name not in DOCUMENT_DEFINITIONS | DOCUMENT_ENV_DEFINITIONS:
        for _ in range(2):
            optional = group(text, pos, '[', ']')
            if optional:
                pos = optional[1]
    count = 3 if name in DOCUMENT_ENV_DEFINITIONS else 2 if name in ENV_DEFINITIONS | DOCUMENT_DEFINITIONS else 1
    for _ in range(count):
        body = group(text, pos)
        if not body:
            return None
        pos = body[1]
    return pos


def masked_tex(text: str) -> str:
    """Mask nonexecuting literal spans/definitions, preserving original line offsets."""
    chars = list(text)
    syntax = None
    i = 0
    while i < len(text):
        if text[i] == '%':
            end = text.find('\n', i)
            end = len(text) if end < 0 else end
            _blank(chars, i, end)
            i = end
            continue
        if text[i] != '\\':
            i += 1
            continue
        if i + 1 < len(text) and not (text[i + 1].isalpha() or text[i + 1] == '@'):
            if text[i + 1] == '\\':
                _blank(chars, i, i + 2)
            i += 2
            continue
        command = re.match(r'\\([A-Za-z@]+)', text[i:])
        if not command:
            i += 1
            continue
        name = command[1]
        end_command = i + command.end()
        if name == 'begin':
            env = group(text, end_command)
            if env and env[0] in LITERAL_ENVS:
                end = re.search(r'\\end\s*\{' + re.escape(env[0]) + r'\}', text[env[1]:])
                stop = env[1] + end.end() if end else len(text)
                _blank(chars, i, stop)
                i = stop
                continue
        if name in {'verb', 'lstinline'}:
            pos = end_command
            if pos < len(text) and text[pos] == '*':
                pos += 1
            if name == 'lstinline':
                optional = group(text, pos, '[', ']')
                if optional:
                    pos = optional[1]
            if pos < len(text) and not text[pos].isspace():
                newline = text.find('\n', pos + 1)
                end = text.find(text[pos], pos + 1)
                stop = end + 1 if end >= 0 and (newline < 0 or end < newline) else newline if newline >= 0 else len(text)
                _blank(chars, i, stop)
                i = stop
                continue
        if name in {'detokenize', 'unexpanded'}:
            literal = group(text, end_command)
            if literal:
                _blank(chars, i, literal[1])
                i = literal[1]
                continue
        if name in DEFINITIONS:
            if syntax is None:
                syntax = _comment_syntax(text)
            end = _definition_end(syntax, end_command, name)
            if end is not None:
                _blank(chars, i, end)
                i = end
                continue
        if name in {'string', 'newif'}:
            target = re.match(r'\s*\\(?:[A-Za-z@]+|.)', text[end_command:])
            if target:
                stop = end_command + target.end()
                _blank(chars, i, stop)
                i = stop
                continue
        i = end_command
    return ''.join(chars)


def line_at(text: str, pos: int) -> int:
    return text.count('\n', 0, pos) + 1


def within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _conditional_ranges(active: str) -> list[tuple[int, int]]:
    """Conservative regions, without deciding unknown TeX branch conditions."""
    stack: list[int] = []
    spans: list[tuple[int, int]] = []
    for command in re.finditer(r'\\(?:if[A-Za-z@]*|else|fi)(?![A-Za-z@])', active):
        name = command[0][1:]
        if name.startswith('if'):
            stack.append(command.start())
        elif name == 'fi' and stack:
            spans.append((stack.pop(), command.end()))
    spans.extend((start, len(active)) for start in stack)
    return spans


def build_source_map(main: Path, *, root: Path | None = None, max_files: int = 128) -> dict[str, Any]:
    main = main.resolve()
    root = (root or main.parent).resolve()
    if not within(main, root):
        raise ValueError('main is outside project root')
    files: list[dict[str, Any]] = []
    objects: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    visiting: set[Path] = set()
    visited: set[Path] = set()
    uncertain_sources: set[str] = set()
    children: dict[Path, set[Path]] = {}

    def mark_uncertain(path: Path, seen: set[Path] | None = None) -> None:
        seen = set() if seen is None else seen
        if path in seen:
            return
        seen.add(path)
        uncertain_sources.add(path.relative_to(root).as_posix())
        for child in children.get(path, set()):
            mark_uncertain(child, seen)

    def visit(path: Path, uncertain: bool = False) -> None:
        rel = path.relative_to(root).as_posix()
        if uncertain:
            mark_uncertain(path)
        if path in visiting:
            warnings.append({'file': rel, 'reason': 'include_cycle'})
            mark_uncertain(path)
            return
        if path in visited:
            warnings.append({'file': rel, 'reason': 'repeated_include_requires_host_review'})
            mark_uncertain(path)
            return
        if len(visited) >= max_files:
            warnings.append({'file': rel, 'reason': 'source_file_limit'})
            return
        visiting.add(path)
        visited.add(path)
        text = path.read_text(encoding='utf-8-sig', errors='replace')
        active = masked_tex(text)
        conditions = _conditional_ranges(active)
        # Unconditional terminators stop TeX reading; archived alternatives after
        # them must not import unused files. Unknown branches remain conservative.
        for terminal in re.finditer(r'\\(?:end\s*\{document\}|endinput(?![A-Za-z@]))', active):
            if any(a <= terminal.start() < b for a, b in conditions):
                continue
            stop = terminal.start() if terminal[0].startswith(r'\end{') or re.match(r'\\end\s*\{', terminal[0]) else active.find('\n', terminal.end())
            stop = len(active) if stop < 0 else stop
            active = active[:stop] + ''.join(c if c in '\r\n' else ' ' for c in active[stop:])
            break
        files.append({'file': rel, 'lines': text.count('\n') + 1})
        conditions = _conditional_ranges(active)
        if conditions:
            warnings.append({'file': rel, 'reason': 'conditional_tex_requires_host_review'})
        conditional_at = lambda pos: any(a <= pos < b for a, b in conditions)
        stack: list[tuple[str, int, int]] = []
        for m in re.finditer(r'\\(begin|end)\s*\{(figure\*?|table\*?)\}', active):
            kind = m.group(2)
            if m.group(1) == 'begin':
                stack.append((kind, m.start(), m.end()))
                continue
            if not stack or stack[-1][0] != kind:
                warnings.append({'file': rel, 'line': line_at(text, m.start()), 'reason': 'unbalanced_float'})
                continue
            _, start, body_start = stack.pop()
            if stack:
                warnings.append({'file': rel, 'line': line_at(text, start), 'reason': 'nested_float'})
                continue
            body = active[body_start:m.start()]
            raw = text[body_start:m.start()]
            labels = [x.group(1).strip() for x in re.finditer(r'\\label\s*\{([^{}]+)\}', body)]
            caption_spans: list[tuple[int, int]] = []
            captions: list[str] = []
            for cm in re.finditer(r'\\caption\*?(?![A-Za-z@])', body):
                cp = cm.end()
                optional = group(body, cp, '[', ']')
                if optional:
                    cp = optional[1]
                cg = group(body, cp)
                if cg:
                    caption_spans.append((cm.start(), cg[1]))
                    captions.append(raw[cg[1] - len(cg[0]) - 1:cg[1] - 1].strip())
            graphics: list[dict[str, Any]] = []
            for gm in re.finditer(r'\\includegraphics\*?(?![A-Za-z@])', body):
                gp = gm.end()
                options = group(body, gp, '[', ']')
                if options:
                    gp = options[1]
                asset = group(body, gp)
                if not asset:
                    continue
                graphics.append({'file': asset[0].strip(), 'options': options[0] if options else '',
                    'line': line_at(text, body_start + gm.start()),
                    'in_caption': any(a <= gm.start() < b for a, b in caption_spans)})
            placement = group(active, body_start, '[', ']')
            objects.append({'kind': kind.rstrip('*'), 'environment': kind, 'labels': labels,
                'file': rel, 'start_line': line_at(text, start), 'end_line': line_at(text, m.end()),
                'placement': placement[0] if placement else '', 'captions': captions, 'graphics': graphics,
                'conditional_source': any(a < m.end() and b > start for a, b in conditions),
                'dynamic_label_source': any(re.search(r'[\\#$]', label) for label in labels)})
        for kind, start, _ in stack:
            warnings.append({'file': rel, 'line': line_at(text, start), 'reason': 'unclosed_float'})
        for m in re.finditer(r'\\(?:ref|eqref|autoref|cref|Cref|pageref)\*?\s*\{([^{}]+)\}', active):
            for label in m.group(1).split(','):
                references.append({'label': label.strip(), 'file': rel, 'line': line_at(text, m.start())})
        for m in re.finditer(r'\\(?:input|include)(?![A-Za-z@])', active):
            arg = group(active, m.end())
            if arg:
                target = arg[0].strip()
            else:
                token = re.match(r'\s+([^\s%{}]+)', active[m.end():])
                target = token.group(1) if token else ''
            if not target or re.search(r'[\\#$]', target):
                warnings.append({'file': rel, 'line': line_at(text, m.start()), 'reason': 'dynamic_include'})
                continue
            candidates = [root / target, path.parent / target]
            candidates = [p if p.suffix else p.with_suffix('.tex') for p in candidates]
            selected = next(((index, p.resolve()) for index, p in enumerate(candidates) if p.is_file()), None)
            if selected is None:
                warnings.append({'file': rel, 'line': line_at(text, m.start()), 'reason': 'missing_include', 'target': target})
                continue
            index, resolved = selected
            if not within(resolved, root):
                warnings.append({'file': rel, 'line': line_at(text, m.start()), 'reason': 'external_include', 'target': target})
                continue
            fallback = index == 1 and path.parent != root
            if fallback:
                warnings.append({'file': rel, 'line': line_at(text, m.start()), 'reason': 'source_relative_include_fallback', 'target': target})
            children.setdefault(path, set()).add(resolved)
            visit(resolved, uncertain=rel in uncertain_sources or conditional_at(m.start()) or fallback)
        visiting.remove(path)
    visit(main)
    by_label: dict[str, list[dict[str, Any]]] = {}
    for obj in objects:
        for label in obj['labels']:
            by_label.setdefault(label, []).append(obj)
    for obj in objects:
        obj['references'] = [r for r in references if r['label'] in obj['labels']]
        obj['anchor_status'] = 'ambiguous' if any(len(by_label[x]) > 1 for x in obj['labels']) else 'resolved'
        if obj['anchor_status'] != 'ambiguous' and (obj['conditional_source'] or obj['dynamic_label_source'] or obj['file'] in uncertain_sources):
            obj['anchor_status'] = 'requires_host_review'
    for label, hits in by_label.items():
        if len(hits) > 1:
            warnings.append({'label': label, 'reason': 'duplicate_float_label', 'files': [o['file'] for o in hits]})
    return {'schema_version': '1.0', 'root': str(root), 'main': main.relative_to(root).as_posix(),
        'files': files, 'objects': objects, 'references': references, 'warnings': warnings,
        'limitations': 'Static source navigation, not TeX expansion, catcode changes, or conditional evaluation. Macro-generated objects and conditional/include fallbacks require host verification; include paths use the declared project root first.'}