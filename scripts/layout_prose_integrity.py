"""Ordinary-prose token preservation for declared layout-only edits.

This is a static, ordered token check, not semantic equivalence. Scientific
math, table, float, declaration and protected-argument checks remain independent.
Unknown invocations stay opaque: their spelling and argument boundaries cannot
be discarded as if they were known presentation controls.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

try:
    import scientific_integrity as scientific
except ModuleNotFoundError:
    from . import scientific_integrity as scientific


_DECLARATIONS = {
    "newcommand", "renewcommand", "providecommand", "DeclareRobustCommand",
    "DeclareMathOperator", "newenvironment", "renewenvironment",
    "provideenvironment", "newcolumntype",
}
_PRIMITIVES = {"def", "gdef", "edef", "xdef"}
_ORDINARY_ENVS = {
    "document", "abstract", "itemize", "enumerate", "description", "center",
    "flushleft", "flushright", "quote", "quotation",
}
_KNOWN_COMMANDS = (
    scientific.FONT_DECL | scientific.RULES | scientific.TEXT_WRAPPERS | scientific.TEXT_SPACE_COMMANDS
    | scientific.ZERO_ARG_LAYOUT_COMMANDS
    | scientific.LAYOUT_ONE | scientific.LAYOUT_LENGTH_REGISTERS | scientific.ID_COMMANDS | scientific.PROTECTED_ARGS
    | {"begin", "end", "setlength", "addtolength", "setcounter", "captionsetup",
       "usepackage", "RequirePackage", "documentclass", "bibliographystyle",
       "includegraphics", "resizebox", "scalebox", "rotatebox", "parbox",
       "raisebox", "shortstack", "multirow", "multicolumn", "footnotemark",
       "allowbreak", "linebreak", "nolinebreak", "looseness", "selectfont", "relax",
       "tabcolsep", "widowpenalty", "clubpenalty", "displaywidowpenalty",
       "brokenpenalty", "interlinepenalty", "item", "begingroup", "endgroup", "\\", "-"}
)
_TOKEN = re.compile(r"\w+|[^\w\s]", re.UNICODE)
_LIMITS = (
    "Ordered ordinary-body words/punctuation and opaque invocations only; "
    "not semantic equivalence, macro expansion, conditional evaluation or "
    "complete visible-content proof. Masked scientific/literal regions need "
    "their independent guards and host PDF review."
)


def _declaration_spans(source: str) -> list[tuple[int, int]]:
    """Mask only definition forms already recorded by scientific_integrity."""
    spans = []
    index = 0
    while index < len(source):
        if source[index] != "\\":
            index += 1
            continue
        start = index
        name, pos = scientific._command(source, index)
        if name in _DECLARATIONS:
            if pos < len(source) and source[pos] == "*":
                pos += 1
            while pos < len(source) and source[pos].isspace():
                pos += 1
            _, target_end = scientific._group(source, pos)
            if target_end == pos:
                target_end = pos
                while target_end < len(source) and source[target_end].isspace():
                    target_end += 1
                if target_end < len(source) and source[target_end] == "\\":
                    _, target_end = scientific._command(source, target_end)
            body_pos = target_end
            for _ in range(2):
                body_pos = scientific._optional(source, body_pos)
            _, end = scientific._group(source, body_pos)
            if end > body_pos:
                if name.endswith("environment"):
                    _, tail = scientific._group(source, end)
                    if tail > end:
                        end = tail
                spans.append((start, end))
                index = end
                continue
        elif name in _PRIMITIVES:
            while pos < len(source) and source[pos].isspace():
                pos += 1
            if pos < len(source) and source[pos] == "\\":
                _, target_end = scientific._command(source, pos)
                brace = source.find("{", target_end)
                if brace >= 0:
                    _, end = scientific._group(source, brace)
                    if end > brace:
                        spans.append((start, end))
                        index = end
                        continue
        elif name == "let":
            while pos < len(source) and source[pos].isspace():
                pos += 1
            if pos < len(source) and source[pos] == "\\":
                _, pos = scientific._command(source, pos)
                while pos < len(source) and (source[pos].isspace() or source[pos] == "="):
                    pos += 1
                _, end = scientific._command(source, pos) if pos < len(source) and source[pos] == "\\" else ("", min(pos + 1, len(source)))
                spans.append((start, end))
                index = end
                continue
        index = scientific._command(source, start)[1]
    return spans


def _ordinary_body(source: str) -> str:
    active = scientific.active_source(source)
    envs = scientific._envs(active)
    spans = [
        (env["start"], env["end"]) for env in envs
        if env["name"] in scientific.FLOAT_ENVS | scientific.TABLE_ENVS
    ]
    spans += [(item["start"], item["end"]) for item in scientific._math_spans(active, envs)]
    # Independently bound standard objects may leave a float for a minipage or
    # literal group. Scientific guards now retain their ordered payload and
    # ordinary residual words, so only these explicit complete local objects
    # can be masked here. Unsupported/ambiguous scopes remain ordinary/opaque.
    inventory = scientific.standard_layout_object_inventory(source)
    spans += [(item["start"], item["end"]) for item in inventory["objects"]]
    spans += [(item["start"], item["end"]) for item in scientific.standard_caption_declarations(source)
              if item["supported"]]
    spans += _declaration_spans(active)
    begin = re.search(r"(?<!\\)\\begin\s*\{document\}", active)
    end = re.search(r"(?<!\\)\\end\s*\{document\}", active)
    if begin:
        spans.append((0, begin.end()))
    if end:
        spans.append((end.start(), len(active)))
    index = 0
    while index < len(active):
        if active[index] != "\\":
            index += 1
            continue
        start = index
        name, pos = scientific._command(active, index)
        if name in scientific.PROTECTED_ARGS:
            if pos < len(active) and active[pos] == "*":
                pos += 1
            pos = scientific._optional(active, pos)
            _, last = scientific._group(active, pos)
            if last > pos:
                spans.append((start, last))
                index = last
                continue
        index = scientific._command(active, start)[1]
    chars = list(active)
    for start, end in spans:
        chars[start:end] = " " * (end - start)
    return "".join(chars)


def _opaque_end(source: str, pos: int) -> int:
    if pos < len(source) and source[pos] == "*":
        pos += 1
    # Preserve the grouping as well as the text for unknown argument signatures.
    while True:
        argument = scientific._optional(source, pos)
        if argument > pos and source[pos:argument].strip().startswith("["):
            pos = argument
            continue
        _, end = scientific._group(source, pos)
        if end > pos:
            pos = end
            continue
        return max(pos, scientific.opaque_layout_child_end(source, pos))


def _prose_tokens(source: str) -> tuple[str, ...]:
    shadowed = scientific._shadowed_presentation_commands(scientific.active_source(source))
    body = _ordinary_body(source)
    # Inspect scope effects before definitions/scientific regions are masked.
    # A local definition may alter later macro rendering even with identical
    # declaration inventories and ordinary words.
    group_offsets = scientific.standard_group_offsets(scientific.active_source(source),
        opaque_commands=shadowed, allowed_commands=frozenset(_KNOWN_COMMANDS))
    # Literal minipage dimensions/alignment change presentation. Preserve the
    # complete body; dynamic headers and unbalanced environments stay opaque.
    container_markers = []
    for env in scientific._envs(body):
        if env['name'] == 'minipage':
            end = scientific.minipage_layout_end(body, env['body'])
            if end is not None and end <= env['tail']:
                container_markers.extend(((env['start'], end), (env['tail'], env['end'])))
    for start, end in sorted(container_markers, reverse=True):
        body = body[:start] + ' ' + body[end:]
        group_offsets = frozenset(offset if offset < start else offset + 1 - (end - start)
                                  for offset in group_offsets if not start <= offset < end)
    pieces = []
    opaque: dict[str, str] = {}
    index = 0
    # Markers absent from the input are restored as single opaque tokens later.
    marker_prefix = "PAPERFITOPAQUE"
    while marker_prefix in body:
        marker_prefix += "X"
    while index < len(body):
        if body[index] != "\\":
            pieces.append(body[index])
            index += 1
            continue
        name, pos = scientific._command(body, index)
        star = pos < len(body) and body[pos] == '*'
        if name in scientific.LAYOUT_LENGTH_REGISTERS and not star:
            end = scientific.layout_assignment_end(body, pos, name, opaque_commands=shadowed)
            if end is not None:
                index = end
                continue
        if name in scientific.TEXT_SPACE_COMMANDS and name not in shadowed and not star:
            # Standard text spaces take no argument. Keep following words,
            # groups and signs, and do not concatenate the adjacent tokens.
            pieces.append(' ')
            index = pos
            continue
        if name in {'setlength', 'addtolength'}:
            end = scientific.layout_length_end(body, pos, opaque_commands=shadowed) if name not in shadowed and not star else None
            if end is not None:
                # Consume the complete known invocation before its token
                # register can become a separate prose macro.
                index = end
                continue
            end = scientific.opaque_length_args_end(body, pos + int(star))
            value = body[index:end]
            marker = f"{marker_prefix}{len(opaque)}"
            opaque[marker] = re.sub(r"\s+", " ", value).strip()
            pieces.extend((" ", marker, " "))
            index = end
            continue
        if name in {'begingroup', 'endgroup'} and index in group_offsets:
            # Existing literal brace normalization keeps the body tokens and
            # ordered claims. Unknown/redefined/unbalanced scopes stay opaque.
            pieces.append('{' if name == 'begingroup' else '}')
            index = pos
            continue
        unknown = name not in _KNOWN_COMMANDS or name in shadowed
        if name in scientific.TEXT_SPACE_COMMANDS | scientific.ZERO_ARG_LAYOUT_COMMANDS and star:
            unknown = True
        if name in {'begingroup', 'endgroup'}:
            unknown = True
        if name in {'nolinkurl', 'urlstyle'}:
            value, end = scientific._group(body, pos)
            unknown = not (name not in shadowed and end > pos and ((name == 'nolinkurl' and scientific.literal_long_identifier(value)) or
                                         (name == 'urlstyle' and value == 'same')))
        if name in {"begin", "end"}:
            env, _ = scientific._group(body, pos)
            unknown = env not in _ORDINARY_ENVS
        if unknown:
            end = _opaque_end(body, pos)
            value = body[index:end]
            marker = f"{marker_prefix}{len(opaque)}"
            opaque[marker] = re.sub(r"\s+", " ", value).strip()
            pieces.extend((" ", marker, " "))
            index = end
            continue
        if name == "allowbreak":
            # A parsed control word, not an escaped command or name prefix.
            while pos < len(body) and body[pos].isspace():
                pos += 1
            index = pos
            continue
        if name == "-":
            index = pos
            continue
        if name == "\\":
            pieces.append(" ")
            index = scientific._optional(body, pos)
            continue
        if name == "selectfont":
            index = scientific._optional(body, pos)
            continue
        if name == "item":
            # Optional item labels render text; preserve their contents.
            optional = scientific._optional(body, pos)
            if optional > pos and body[pos:optional].strip().startswith("["):
                pieces.append(body[pos:optional])
            pieces.append(" ")
            index = optional
            continue
        pieces.append(body[index:pos])
        index = pos
    normalized = scientific._strip_layout("".join(pieces), text_wrappers=True, opaque_commands=shadowed)
    normalized = re.sub(r"(?<!\\)[{}]", "", normalized)
    normalized = re.sub(r"(?<!\\)~", " ", normalized)
    return tuple(opaque.get(token, token) for token in _TOKEN.findall(normalized))


def _summary(tokens: tuple[str, ...]) -> dict[str, Any]:
    payload = json.dumps(tokens, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return {"token_count": len(tokens), "sha256": hashlib.sha256(payload).hexdigest()}


def compare_layout_prose(original: str, repaired: str) -> dict[str, Any]:
    """Compare ordinary-body tokens; a non-pass needs caller rejection/review."""
    try:
        before, after = _prose_tokens(original), _prose_tokens(repaired)
    except (TypeError, ValueError, IndexError, RecursionError) as exc:
        return {"checked": False, "status": "unavailable", "changed_tokens": [],
                "reasons": [f"Ordinary-prose extraction unavailable: {type(exc).__name__}"],
                "limits": _LIMITS}
    changes = []
    if before != after:
        start = 0
        while start < min(len(before), len(after)) and before[start] == after[start]:
            start += 1
        old_end, new_end = len(before), len(after)
        while old_end > start and new_end > start and before[old_end - 1] == after[new_end - 1]:
            old_end -= 1
            new_end -= 1
        changes.append({"before_range": [start, old_end], "after_range": [start, new_end],
                        "before_tokens": [token[:160] for token in before[start:old_end][:12]],
                        "after_tokens": [token[:160] for token in after[start:new_end][:12]],
                        "before_changed_count": old_end - start,
                        "after_changed_count": new_end - start})
    return {"checked": True, "status": "failed" if changes else "pass",
            "before": _summary(before), "after": _summary(after),
            "changed_tokens": changes,
            "reasons": ["Ordinary prose changed during a layout-only edit"] if changes else [],
            "limits": _LIMITS}
