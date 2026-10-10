"""Diff-based layout policy, separate from scientific-content preservation.

This is static source inspection, not TeX execution or a proof of visual quality.
Existing operations are grandfathered; removals are always allowed. Approvals
are separate caller evidence and are never inferred from a VLM finding or comment.
"""
from __future__ import annotations

from collections import Counter
import argparse
import json
import math
import hashlib
from pathlib import Path
import re
from typing import Any, Iterable

from source_map import group, line_at, masked_tex, within
import scientific_integrity as scientific

_COMMAND = re.compile(r"\\(resizebox|scalebox|newpage|clearpage|pagebreak|vspace)(?![A-Za-z@])")
_TABLE = re.compile(r"\\begin\s*\{(?:tabular\*?|tabularx|tabulary|longtable|table\*?)\}")
_GRAPHIC = re.compile(r"\\includegraphics\*?(?:\s*\[[^\]]*\])?\s*\{([^{}]+)\}")
_FLOAT = re.compile(r"\\(begin|end)\s*\{((?:(?:wrap|sideways)?(?:table|figure)|longtable)\*?)\}")


def _active(source: str) -> str:
    # source_map masks comments, literals and macro definitions. filecontents is
    # another literal source container, not an executed part of the manuscript.
    active = masked_tex(source)
    chars = list(active)
    for match in re.finditer(r"\\begin\s*\{(filecontents\*?)\}", active):
        ending = re.search(r"\\end\s*\{" + re.escape(match[1]) + r"\}", active[match.end():])
        end = match.end() + ending.end() if ending else len(active)
        for index in range(match.start(), end):
            if chars[index] not in "\r\n":
                chars[index] = " "
    return "".join(chars)


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _scale_body(body: str) -> str:
    # Nested scale arguments have their own policy findings. Keep the outer
    # wrapper attached to its content when only an inner factor changes.
    edits: list[tuple[int, int]] = []
    for match in _COMMAND.finditer(body):
        if match[1] not in {"resizebox", "scalebox"}:
            continue
        first = group(body, match.end())
        if not first:
            continue
        end = first[1]
        second = group(body, end) if match[1] == "resizebox" else group(body, end, "[", "]")
        if second:
            end = second[1]
        edits.append((match.end(), end))
    for start, end in reversed(edits):
        body = body[:start] + body[end:]
    return _normalize(body)


def _target(active: str, offset: int, body: str) -> tuple[str, str]:
    graphics = tuple(_normalize(value) for value in _GRAPHIC.findall(body))
    # Only a globally unique, single label can identify a float across layout
    # environments. A compound float may instead contain a uniquely labelled
    # standard minipage object. Keep its exact container parameters/environment
    # so unrelated float ordinals cannot transfer its inherited scale allowance.
    stack: list[tuple[str, int]] = []
    containing: list[tuple[str, int, int]] = []
    for marker in _FLOAT.finditer(active):
        if marker[1] == "begin":
            stack.append((marker[2], marker.start()))
        elif stack and stack[-1][0] == marker[2]:
            environment, start = stack.pop()
            if start <= offset < marker.end():
                containing.append((environment, start, marker.end()))
    if len(containing) == 1:
        environment, start, end = containing[0]
        labels = re.findall(r"\\label\s*\{([^{}]+)\}", active[start:end])
        all_labels = re.findall(r"\\label\s*\{([^{}]+)\}", active)
        if len(labels) == 1 and all_labels.count(labels[0]) == 1:
            family = "figure" if "figure" in environment else "table"
            return "labels:" + labels[0], family
        local = active[start:end]
        minipages = [item for item in scientific._envs(local)
                     if item["name"] == "minipage"
                     and item["body"] <= offset - start < item["tail"]]
        if len(minipages) == 1:
            item = minipages[0]
            local_labels = re.findall(r"\\label\s*\{([^{}]+)\}", local[item["body"]:item["tail"]])
            if len(local_labels) == 1 and all_labels.count(local_labels[0]) == 1:
                pos = item["body"]
                for _ in range(3):
                    optional = group(local, pos, "[", "]")
                    if not optional:
                        break
                    pos = optional[1]
                width = group(local, pos)
                if width:
                    header = _normalize(local[item["start"]:width[1]])
                    context = environment + ":minipage:" + hashlib.sha256(header.encode("utf-8")).hexdigest()
                    return "labels:" + local_labels[0], context
    if containing:
        contexts = []
        for environment, start, _ in sorted(containing, key=lambda item: item[1]):
            occurrence = sum(marker[1] == "begin" for marker in _FLOAT.finditer(active[:start]))
            contexts.append(f"{environment}:{occurrence}")
        context = "/".join(contexts)
    else:
        context = ""
    target = "graphics:" + ",".join(graphics) if graphics else "body:" + hashlib.sha256(_scale_body(body).encode("utf-8")).hexdigest()
    return target, context


# Scientific token preservation deliberately normalizes font declarations.
# This separate channel prevents a new page-fit shortcut from shrinking body
# prose, while retaining conventional local table/float/caption typography.
_SMALL_BODY_FONTS = {"tiny", "scriptsize", "footnotesize", "small"}
_FONT_COMMANDS = _SMALL_BODY_FONTS | {"fontsize"}
_LOCAL_FONT_ENVS = scientific.FLOAT_ENVS | scientific.TABLE_ENVS | {
    "tabulary", "longtable*", "wrapfigure*", "wraptable*",
}
_LOCAL_FONT_ARGUMENTS = {
    "caption", "subcaption", "footnote", "footnotetext",
    "title", "author", "affiliation", "thanks",
}
_REVIEW_LENGTHS = {
    "parskip", "baselineskip", "abovedisplayskip", "belowdisplayskip",
    "abovedisplayshortskip", "belowdisplayshortskip",
    "itemsep", "topsep", "parsep", "partopsep",
}


def _groups(active: str) -> list[tuple[int, int]]:
    """Balanced literal/standard groups only, without executing TeX."""
    spans: list[tuple[int, int]] = []
    stack: list[tuple[int, str]] = []
    index = 0
    while index < len(active):
        if active[index] == "\\":
            name, end = scientific._command(active, index)
            if name == "begingroup":
                stack.append((index, "standard"))
            elif name == "endgroup" and stack and stack[-1][1] == "standard":
                spans.append((stack.pop()[0], index))
            index = end
            continue
        if active[index] == "{":
            stack.append((index, "literal"))
        elif active[index] == "}" and stack and stack[-1][1] == "literal":
            spans.append((stack.pop()[0], index))
        index += 1
    return spans


def _font_context(active: str, offset: int, envs: list[dict[str, Any]],
                  groups: list[tuple[int, int]]) -> tuple[bool, int]:
    local = any(e["start"] <= offset < e["end"] and e["name"] in _LOCAL_FONT_ENVS for e in envs)
    for marker in re.finditer(r"\\([A-Za-z@]+)", active):
        if marker[1] not in _LOCAL_FONT_ARGUMENTS:
            continue
        pos = marker.end()
        if pos < len(active) and active[pos] == "*":
            pos += 1
        optional = group(active, pos, "[", "]")
        if optional:
            pos = optional[1]
        argument = group(active, pos)
        if argument and pos <= offset < argument[1]:
            local = True
            break
    ends = [end for start, end in groups if start < offset < end]
    ends += [e["tail"] for e in envs if e["body"] <= offset < e["tail"]]
    return local, min(ends, default=len(active))


def _normal_reset_end(active: str, start: int, end: int,
                      envs: list[dict[str, Any]],
                      groups: list[tuple[int, int]], shadowed: set[str]) -> int:
    if "normalsize" in shadowed:
        return end
    def scope(offset: int) -> tuple[Any, ...]:
        return (tuple((left, right) for left, right in groups if left < offset < right),
                tuple((e["start"], e["end"]) for e in envs if e["body"] <= offset < e["tail"]))
    origin = scope(start)
    for marker in re.finditer(r"\\normalsize(?![A-Za-z@])", active[start:end]):
        offset = start + marker.start()
        if scope(offset) == origin:
            return offset
    return end


def _font_redefinitions(source: str) -> set[str]:
    names: set[str] = set()
    for record in scientific._macro_definitions(scientific.active_source(source)):
        target = record[1] if record[0] in {"def", "gdef", "edef", "xdef", "let"} else record[2]
        name = target.lstrip("\\")
        if name in _FONT_COMMANDS | {"normalsize"}:
            names.add(name)
    if re.search(r"\\(?:csname|catcode)(?![A-Za-z@])", _active(source)):
        names.update(_FONT_COMMANDS | {"normalsize"})
    from template_parameter_review import _shadowed, _constructed_usage
    for name in _FONT_COMMANDS | {"normalsize"}:
        if _shadowed(source, {name}) or _constructed_usage(source, name):
            names.add(name)
    return names


def _table_only_scope(active: str, start: int, end: int,
                      envs: list[dict[str, Any]]) -> bool:
    """A font before a bare table is local only in a bounded table-only group."""
    tables = [e for e in envs if e["name"] in _LOCAL_FONT_ENVS
              and start <= e["start"] and e["end"] <= end]
    if not tables:
        return False
    chars = list(active[start:end])
    for table in tables:
        for index in range(table["start"] - start, table["end"] - start):
            chars[index] = " "
    remainder = "".join(chars)
    remainder = re.sub(r"\\(?:centering|raggedright|raggedleft|arraybackslash|"
                       r"selectfont|normalsize|small|footnotesize|scriptsize|tiny|par)"
                       r"(?![A-Za-z@])", "", remainder)
    return not re.sub(r"[\s{}]", "", remainder)


def _presentation_operation(source: str, start: int, end: int, name: str,
                            arguments: list[str], rule: str, status: str,
                            target: str = "") -> dict[str, Any]:
    return {
        "command": name, "arguments": arguments, "target": target,
        "rule": rule, "status": status, "start_offset": start, "end_offset": end,
        "line": line_at(source, start), "source": source[start:end],
    }


def _body_presentation_operations(source: str, active: str) -> list[dict[str, Any]]:
    envs = scientific._envs(active)
    groups = _groups(active)
    shadowed_fonts = _font_redefinitions(source)
    # Only a finite, standard preamble hook is bibliography-local. Unknown
    # hook code and body font declarations keep their existing hard boundary.
    reference_hooks = scientific.reference_font_hooks(source)
    hook_code_ranges = []
    for marker in re.finditer(r"\\AddToHook(?![A-Za-z@])", active):
        key = group(active, marker.end())
        if not key:
            continue
        optional = group(active, key[1], "[", "]")
        body = group(active, optional[1] if optional else key[1])
        if body:
            hook_code_ranges.append((key[1], body[1]))
    operations: list[dict[str, Any]] = []
    index = 0
    while index < len(active):
        if active[index] != "\\":
            index += 1
            continue
        start = index
        name, pos = scientific._command(active, index)
        index = pos
        if name not in _FONT_COMMANDS | _REVIEW_LENGTHS | {"setlength", "addtolength", "linespread"}:
            continue
        local, scope_end = _font_context(active, start, envs, groups)
        reference_local = any(hook["start"] <= start < hook["end"] for hook in reference_hooks)
        opaque_hook = not reference_local and any(left <= start < right for left, right in hook_code_ranges)
        local = local or reference_local
        if opaque_hook:
            local = False
        arguments: list[str] = []
        end = pos
        if name in _FONT_COMMANDS:
            lexical_end = scope_end
            if not opaque_hook:
                scope_end = _normal_reset_end(active, start, scope_end, envs, groups, shadowed_fonts)
            normal_reset = scope_end < lexical_end
            if name == "fontsize":
                first = group(active, pos)
                second = group(active, first[1]) if first else None
                if first:
                    arguments.append(_normalize(first[0]))
                    end = first[1]
                if second:
                    arguments.append(_normalize(second[0]))
                    end = second[1]
                index = max(index, end)
                size = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))(?:pt)?", arguments[0]) if arguments else None
                baseline = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))(?:pt)?", arguments[1]) if len(arguments) == 2 else None
                # Positive point sizes have no universal readability cutoff.
                # Local explicit typography needs measured current-page host
                # review; body shrink and nonpositive/hidden text stay blocked.
                if size and float(size[1]) <= 0:
                    operations.append(_presentation_operation(
                        source, start, end, name, arguments,
                        "nonpositive_explicit_font_size", "blocked"))
                    continue
                if baseline and float(baseline[1]) <= 0:
                    operations.append(_presentation_operation(
                        source, start, end, name, arguments,
                        "nonpositive_font_baseline", "blocked"))
                    continue
                # A new near-zero font over actual tabular cells conceals the
                # data. This narrow boundary is not a universal text-size or
                # caption/footnote readability theorem.
                table_data_scope = any(e["name"] in scientific.TABLE_ENVS and
                                       (e["body"] <= start < e["tail"] or end <= e["body"] < scope_end)
                                       for e in envs)
                if (size and 0 < float(size[1]) <= 1 and table_data_scope
                        and name not in shadowed_fonts and not opaque_hook):
                    operations.append(_presentation_operation(
                        source, start, end, name, arguments,
                        "near_zero_table_data_font", "blocked"))
                    continue
                table_only = not opaque_hook and _table_only_scope(active, end, scope_end, envs)
                if (local or table_only) and name not in shadowed_fonts and size and baseline and all(math.isfinite(float(value[1])) and float(value[1]) > 0 for value in (size, baseline)):
                    operations.append(_presentation_operation(
                        source, start, end, name, arguments,
                        "local_explicit_font_size", "manual_review"))
                    continue
                if local and name not in shadowed_fonts:
                    operations.append(_presentation_operation(
                        source, start, end, name, arguments,
                        "opaque_local_font_size", "manual_review"))
                    continue
            elif local and name not in shadowed_fonts:
                continue
            if not opaque_hook and name not in shadowed_fonts and not local and ((normal_reset and not active[end:scope_end].strip()) or _table_only_scope(active, end, scope_end, envs)):
                continue
            affected = re.sub(r"\\(?:tiny|scriptsize|footnotesize|small|normalsize|selectfont)"
                              r"(?![A-Za-z@])", "", active[end:scope_end])
            target = "body_font:" + hashlib.sha256(_normalize(affected).encode("utf-8")).hexdigest()
            operations.append(_presentation_operation(
                source, start, end, name, arguments,
                "opaque_font_definition" if name in shadowed_fonts else "body_font_shrink" if name in _SMALL_BODY_FONTS else "body_font_size",
                "blocked" if name not in shadowed_fonts else "manual_review", target))
            continue
        register = name
        value = ""
        if name in {"setlength", "addtolength"}:
            arg = group(active, pos)
            if arg:
                register = arg[0].strip().lstrip("\\")
                pos = arg[1]
            else:
                cursor = pos
                while cursor < len(active) and active[cursor].isspace():
                    cursor += 1
                if cursor >= len(active) or active[cursor] != "\\":
                    continue
                register, pos = scientific._command(active, cursor)
            length = group(active, pos)
            if not length:
                continue
            value, end = length
            index = max(index, end)
            if register not in _REVIEW_LENGTHS:
                continue
            arguments = ["\\" + register, _normalize(value)]
        elif name == "linespread":
            length = group(active, pos)
            if not length:
                continue
            value, end = length
            arguments = [_normalize(value)]
        else:
            end = scientific.layout_assignment_end(active, pos, name)
            if end is None:
                continue
            value = re.sub(r"^\s*=\s*", "", active[pos:end]).strip()
            arguments = [_normalize(value)]
        index = max(index, end)
        literal = re.match(r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))", value)
        if literal is None:
            continue
        amount = float(literal[1])
        # Finite spacing changes do not prove poor typography. Negative body
        # gaps and line compression require actual PDF review; do not impose a
        # fixed threshold that rejects a legitimate conference template.
        if name == "linespread":
            if local or amount >= 1:
                continue
            rule = "body_line_compression"
            status = "blocked" if amount <= 0 else "manual_review"
        elif register == "baselineskip" and not local and amount <= 0:
            rule, status = "nonpositive_body_baseline", "blocked"
        elif amount < 0:
            rule, status = "negative_layout_spacing", "manual_review"
        else:
            continue
        operations.append(_presentation_operation(source, start, end, name, arguments, rule, status))
    return operations


def _graphic_option_operations(source: str, active: str) -> list[dict[str, Any]]:
    """Keep disappearing images and changed crops separate from their filenames."""
    operations = []
    from template_parameter_review import _shadowed, _constructed_usage
    opaque_command = (_shadowed(source, {'includegraphics'})
                      or _constructed_usage(source, 'includegraphics'))
    crop_keys = {'trim', 'viewport', 'clip', 'bb', 'page', 'pagebox', 'opacity', 'transparency'}
    safe_keys = {'width', 'height', 'totalheight', 'scale', 'angle', 'origin',
                 'keepaspectratio', 'interpolate', 'natwidth', 'natheight'}
    for ordinal, marker in enumerate(re.finditer(r'\\includegraphics(?![A-Za-z@])', active)):
        pos = marker.end()
        star = pos < len(active) and active[pos] == '*'
        pos += int(star)
        optional = group(active, pos, '[', ']')
        if optional:
            pos = optional[1]
        asset = group(active, pos)
        if not asset:
            continue
        path, end = asset
        fields = []
        for part in (optional[0].split(',') if optional else []):
            key, separator, value = part.partition('=')
            # Whitespace separates trim/viewport coordinates. Removing it can
            # identify distinct crops as the same inherited operation.
            key, value = _normalize(key), ' '.join(value.split())
            if key:
                fields.append((key, value if separator else 'true'))
        dimensions = [_normalize(value) for key, value in fields if key in {'width', 'height', 'totalheight', 'scale'}]
        hidden = any(key == 'draft' and value != 'false' for key, value in fields)
        content = [(key, value) for key, value in fields if key in crop_keys or key not in safe_keys]
        content = [(key, value) for key, value in content if not (key == 'draft' and value == 'false')]
        if not (content or star or _nonpositive_scale(dimensions)):
            continue
        target, family = _target(active, marker.start(), active[marker.start():end])
        # Ambiguous same-asset occurrences must not transfer an inherited crop.
        signature = path.strip() + ('*' if star else '')
        if not target.startswith('labels:'):
            signature += ':occurrence:' + str(ordinal)
        rule = ('opaque_graphic_options' if opaque_command
                else 'nonpositive_graphic_size' if _nonpositive_scale(dimensions)
                else 'hidden_graphic_content' if hidden else 'graphic_content_options')
        arguments = ([key + '=' + value for key, value in content] + (['starred_clip=true'] if star else []))
        if rule == 'nonpositive_graphic_size':
            arguments.extend('size=' + value for value in dimensions)
        operations.append({'command': 'includegraphics' + ('*' if star else ''),
                           'arguments': arguments, 'target': target, 'anchor_family': family,
                           'asset_reference': path.strip(),
                           'body_signature': hashlib.sha256(signature.encode('utf-8')).hexdigest(),
                           'rule': rule, 'status': 'blocked' if rule in {'nonpositive_graphic_size', 'hidden_graphic_content'} else 'manual_review',
                           'start_offset': marker.start(), 'end_offset': end,
                           'line': line_at(source, marker.start()), 'source': source[marker.start():end]})
    return operations


def _operations(source: str, *, bounded_layout: bool) -> list[dict[str, Any]]:
    active = _active(source)
    operations: list[dict[str, Any]] = []
    for match in _COMMAND.finditer(active):
        name = match[1]
        pos = match.end()
        star = pos < len(active) and active[pos] == "*"
        pos += int(star)
        arguments: list[str] = []
        body = ""
        if name in {"resizebox", "scalebox"}:
            first = group(active, pos)
            if first:
                arguments.append(_normalize(first[0]))
                pos = first[1]
                second = group(active, pos) if name == "resizebox" else group(active, pos, "[", "]")
                if second:
                    arguments.append(_normalize(second[0]))
                    pos = second[1]
                content = group(active, pos) if name != "resizebox" or second else None
                if content:
                    body, pos = content
            # TeX can also consume an unbraced macro as a body. Its type and
            # extent are opaque here; do not silently mark that source clean.
            kind = "table" if _TABLE.search(body) else "graphics" if _GRAPHIC.search(body) else "unknown"
            rule = "scaled_table" if kind == "table" else "scaled_graphics" if kind == "graphics" else "opaque_scaling"
            disposition = "manual_review"
            if _nonpositive_scale(arguments):
                rule, disposition = "nonpositive_scaling", "blocked"
            target, anchor_family = _target(active, match.start(), body)
        elif name == "vspace":
            amount = group(active, pos)
            if amount:
                arguments.append(_normalize(amount[0]))
                pos = amount[1]
            rule, disposition, target = "restricted_spacing", "manual_review", ""
        else:
            if not bounded_layout:
                continue
            strength = group(active, pos, "[", "]") if name == "pagebreak" else None
            if strength:
                arguments.append(_normalize(strength[0]))
                pos = strength[1]
                if strength[0].strip() in {"0", "1", "2", "3"}:
                    continue
            rule = "forced_page_break"
            disposition = "manual_review"
            target = ""
        operations.append({
            "command": name + ("*" if star else ""),
            "arguments": arguments,
            "target": target,
            "rule": rule,
            "status": disposition,
            "start_offset": match.start(),
            "end_offset": pos,
            "line": line_at(source, match.start()),
            "source": source[match.start():pos],
            **({"anchor_family": anchor_family, "body_signature": hashlib.sha256(_scale_body(body).encode("utf-8")).hexdigest()} if name in {"resizebox", "scalebox"} else {}),
        })
    operations.extend(_body_presentation_operations(source, active))
    operations.extend(_graphic_option_operations(source, active))
    for declaration in scientific.arraystretch_declarations(source):
        if declaration["value"] <= 0:
            operations.append(_presentation_operation(
                source, declaration["start"], declaration["end"],
                declaration["command"], ["\\arraystretch", declaration["value_text"]],
                "nonpositive_table_row_stretch", "blocked"))
    return operations


def _signature(operation: dict[str, Any]) -> tuple[Any, ...]:
    return operation["command"], tuple(operation["arguments"]), operation["target"], operation.get("anchor_family", ""), operation.get("body_signature", "")


_REVIEW_BINDING_SCHEMA = "paperfit.layout_review_binding.v1"
_REVIEW_REQUEST_SCHEMA = "paperfit.layout_policy_review.v1"
_REVIEW_ARTIFACT_CHECKS = {
    "pdf_exists", "pdf_not_older_than_source_set", "page_images_exist",
    "pdf_page_count_available", "page_images_complete", "page_images_readable",
    "page_images_not_older_than_pdf",
}


def _nonpositive_scale(arguments: list[str]) -> bool:
    """Reject explicit disappearing/mirrored content; opaque expressions stay reviewable."""
    for argument in arguments:
        match = re.fullmatch(
            r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
            r"(?:pt|bp|pc|cm|mm|in|em|ex|sp|\\[A-Za-z@]+)?", argument)
        if match and (not math.isfinite(float(match[1])) or float(match[1]) <= 0):
            return True
    return False


def _supported_scale_arguments(operation: dict[str, Any]) -> bool:
    """Unknown TeX factors cannot obtain a positivity exception from a boolean."""
    values = operation["arguments"]
    command = operation["command"].rstrip("*")
    if command == "scalebox":
        return bool(values) and all(re.fullmatch(r"\+?(?:\d+(?:\.\d*)?|\.\d+)", value)
                                    and math.isfinite(float(value)) and float(value) > 0
                                    for value in values)
    native = {"\\linewidth", "\\textwidth", "\\columnwidth", "\\textheight",
              "\\paperwidth", "\\paperheight", "\\width", "\\height", "\\totalheight"}
    if command != "resizebox" or len(values) != 2 or values == ["!", "!"]:
        return False
    for value in values:
        if value == "!":
            continue
        if value in native:
            continue
        literal = re.fullmatch(r"(\+?(?:\d+(?:\.\d*)?|\.\d+))(pt|bp|pc|cm|mm|in|em|ex|sp|\\[A-Za-z@]+)", value)
        if not literal or not math.isfinite(float(literal[1])) or float(literal[1]) <= 0:
            return False
        if literal[2].startswith("\\") and literal[2] not in native:
            return False
    return True


def _relative_path(root: Path, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("artifact path unavailable")
    path = (root / value).resolve()
    if not within(path, root):
        raise ValueError("artifact path outside project")
    return path.relative_to(root).as_posix()


def _valid_hash(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_identity(root: Path, files: Any) -> dict[str, str]:
    if not isinstance(files, list) or not files:
        raise ValueError("complete source inventory unavailable")
    identity: dict[str, str] = {}
    for item in files:
        if not isinstance(item, dict) or not _valid_hash(item.get("sha256")):
            raise ValueError("source inventory hash unavailable")
        path = _relative_path(root, item.get("path"))
        if path in identity:
            raise ValueError("duplicate source inventory path")
        identity[path] = item["sha256"]
    return identity


def verify_layout_review_binding_current(project_root: Path, binding: dict[str, Any]) -> None:
    """Late read barrier over the exact evidence already used for a review.

    This proves current byte identities at the read barrier, not atomic locking,
    TeX semantics, host page inspection or publication acceptance.
    """
    from runtime_snapshots import discover_source_files
    root = Path(project_root).resolve()
    if not isinstance(binding, dict) or binding.get("schema_version") != _REVIEW_BINDING_SCHEMA:
        raise ValueError("review binding schema unavailable at final read barrier")
    sources = _source_identity(root, binding.get("source_set"))
    discovered = {_relative_path(root, str(path)) for path in discover_source_files(root, binding["main_tex"])}
    if discovered != set(sources):
        raise ValueError("current source inventory changed at final read barrier")
    for relative, digest in sources.items():
        if _file_hash(root / relative) != digest:
            raise ValueError("current source bytes changed at final read barrier: " + relative)
    page_set = binding["page_images"]
    pages = _source_identity(root, page_set.get("pages"))
    pages_dir = root / _relative_path(root, binding["page_images_dir"])
    current_pages = {_relative_path(root, str(path)) for path in pages_dir.glob("page_*.png")}
    if current_pages != set(pages) or page_set.get("count") != len(pages):
        raise ValueError("current page inventory changed at final read barrier")
    imported = binding["imported_inputs"]
    identities = _source_identity(root, imported.get("files"))
    identities.update(pages)
    for record in (binding["pdf"], binding["compile_evidence"],
                   {"path": imported["fls_path"], "sha256": imported["fls_sha256"]}):
        relative = _relative_path(root, record.get("path"))
        digest = record.get("sha256")
        if not _valid_hash(digest):
            raise ValueError("review evidence hash unavailable at final read barrier")
        if relative in identities and identities[relative] != digest:
            raise ValueError("conflicting review identities at final read barrier")
        identities[relative] = digest
    for relative, digest in identities.items():
        if _file_hash(root / relative) != digest:
            raise ValueError("current review evidence changed at final read barrier: " + relative)


def collect_layout_review_binding(
    project_root: Path, *, main_tex: str, page_images_dir: str,
    compile_journal: str = "data/compile/journal.jsonl",
) -> dict[str, Any]:
    """Read current evidence for a host review; never compile, render, or approve.

    Only the canonical main.tex-neighbor PDF and latest native compile_result.v2
    journal entry with unchanged same-attempt sources and imported-input hashes
    is supported. All project resources in the recorder are checked. Isolated
    build receipts and older unbound journal entries remain unavailable.
    """
    from runtime_artifacts import collect_artifact_manifest
    from runtime_snapshots import discover_source_files
    from compile_inputs import live_compile_inputs

    root = Path(project_root).resolve()
    try:
        main = _relative_path(root, main_tex)
        pages_dir = _relative_path(root, page_images_dir)
        journal = _relative_path(root, compile_journal)
        _relative_path(root, str((root / main).with_suffix(".pdf")))
        # Do not follow an artifact/source symlink to a different project.
        for source in discover_source_files(root, main):
            _relative_path(root, str(source))
        for page in (root / pages_dir).glob("page_*.png"):
            _relative_path(root, str(page))
        live = collect_artifact_manifest(root, main, {"page_images_dir": pages_dir})
        checks = live["freshness"]["checks"]
        blocked = sorted(name for name in _REVIEW_ARTIFACT_CHECKS
                         if checks.get(name, {}).get("pass") is not True)
        if blocked:
            raise ValueError("current artifact checks failed: " + ", ".join(blocked))
        artifacts = live["artifacts"]
        source_set = _source_identity(root, artifacts["source_set"].get("files"))
        journal_path = root / journal
        if not journal_path.is_file() or journal_path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("current compile journal unavailable")
        journal_bytes = journal_path.read_bytes()
        lines = journal_bytes.decode("utf-8-sig").splitlines()
        if not lines or not lines[-1].strip():
            # A trailing newline is removed by splitlines(); an empty final row
            # is not skipped to fish out an earlier successful attempt.
            raise ValueError("latest compile attempt unavailable")
        attempt = json.loads(lines[-1])
        if not isinstance(attempt, dict) or attempt.get("schema_version") != "compile_result.v2":
            raise ValueError("latest compile attempt schema unsupported")
        if (attempt.get("success") is not True
                or type(attempt.get("returncode")) is not int or attempt["returncode"] != 0
                or attempt.get("timed_out") is not False
                or attempt.get("pdf_fresh") is not True
                or attempt.get("restored_baseline_pdf") is True
                or attempt.get("partial_pdf_available") is True):
            raise ValueError("latest compile attempt is not a successful fresh build")
        pdf = artifacts["pdf"]
        if (_relative_path(root, attempt.get("main_tex")) != main
                or _relative_path(root, attempt.get("pdf_path")) != pdf["path"]
                or attempt.get("pdf_sha256") != pdf["sha256"]
                or type(attempt.get("page_count")) is not int
                or attempt["page_count"] != pdf["page_count"]):
            raise ValueError("latest compile attempt does not match current PDF")
        fingerprint = attempt.get("source_fingerprint")
        if not isinstance(fingerprint, dict) or not fingerprint:
            raise ValueError("latest compile attempt has no same-attempt source hashes")
        compiled_source = _source_identity(root, [
            {"path": path, "sha256": digest} for path, digest in fingerprint.items()
        ])
        before_fingerprint = attempt.get("source_fingerprint_before")
        if not isinstance(before_fingerprint, dict) or not before_fingerprint:
            raise ValueError("latest compile attempt has no pre-build source hashes")
        before_source = _source_identity(root, [
            {"path": path, "sha256": digest} for path, digest in before_fingerprint.items()
        ])
        if attempt.get("source_unchanged_during_compile") is not True or before_source != compiled_source:
            raise ValueError("sources changed during latest compile attempt")
        if compiled_source != source_set:
            raise ValueError("current sources differ from compiled source inventory")
        resources = attempt.get("compile_inputs")
        if (not isinstance(resources, dict)
                or resources.get("schema") != "compile_input_binding.v1"
                or resources.get("bound") is not True
                or resources.get("unavailable") != []
                or resources.get("missing_prebuild_inputs") != []):
            raise ValueError("latest compile attempt has no complete bound imported-input receipt")
        imported_sets = []
        for phase in ("before", "after"):
            values = resources.get(phase)
            if not isinstance(values, dict) or not values:
                raise ValueError("compiled imported-input inventory unavailable: " + phase)
            imported_sets.append(_source_identity(root, [
                {"path": path, "sha256": digest} for path, digest in values.items()
            ]))
        if imported_sets[0] != imported_sets[1]:
            raise ValueError("imported project resources changed during latest compile attempt")
        recorder = _relative_path(root, resources.get("fls_path"))
        canonical_recorder = _relative_path(root, str((root / main).with_suffix(".fls")))
        if recorder != canonical_recorder or not _valid_hash(resources.get("fls_sha256")):
            raise ValueError("latest compile recorder identity unavailable or unsupported")
        imported_live = live_compile_inputs(root, root / main)
        if imported_live.get("unavailable") != []:
            raise ValueError("current imported project resources unavailable")
        current_files = imported_live.get("files")
        if not isinstance(current_files, dict) or not current_files:
            raise ValueError("current imported project inventory unavailable")
        current_inputs = _source_identity(root, [
            {"path": path, "sha256": digest} for path, digest in current_files.items()
        ])
        if (_relative_path(root, imported_live.get("fls_path")) != recorder
                or imported_live.get("fls_sha256") != resources["fls_sha256"]
                or current_inputs != imported_sets[1]):
            raise ValueError("current recorder or imported project resources differ from compiled inputs")
        page_set = artifacts["page_images"]
        binding = {
            "schema_version": _REVIEW_BINDING_SCHEMA,
            "main_tex": main, "page_images_dir": pages_dir, "compile_journal": journal,
            "source_set": [{"path": path, "sha256": digest}
                           for path, digest in sorted(source_set.items())],
            "pdf": {name: pdf[name] for name in ("path", "sha256", "page_count")},
            "page_images": {
                "count": page_set["count"], "aggregate_sha256": page_set["aggregate_sha256"],
                "pages": [{name: page[name] for name in ("path", "sha256", "page_number")}
                          for page in page_set["pages"]],
            },
            "imported_inputs": {
                "files": [{"path": path, "sha256": digest}
                          for path, digest in sorted(current_inputs.items())],
                "fls_path": recorder, "fls_sha256": resources["fls_sha256"],
                "scope": "Imported project resources and reachable source databases; installed external dependencies excluded.",
            },
            "compile_evidence": {
                "path": journal, "sha256": hashlib.sha256(journal_bytes).hexdigest(),
                "attempt_id": attempt.get("attempt_id"),
            },
        }
        verify_layout_review_binding_current(root, binding)
        return {"status": "available", "binding": binding, "blocking_checks": [],
                "scope_note": "Native compile_result.v2, actual project recorder and current byte identities only; host inspection and semantic preservation are not established. Isolated/unbound receipts do not authorize this review."}
    except (OSError, ValueError, KeyError, TypeError, UnicodeError) as error:
        return {"status": "unavailable", "binding": None, "blocking_checks": [str(error)],
                "scope_note": "Native-only review binding unavailable; isolated or unknown evidence may still be observed, but cannot authorize a current-build review."}


def _exact_operation(operation: dict[str, Any], file: str) -> dict[str, Any]:
    return {"file": file, **{key: operation.get(key) for key in (
        "command", "arguments", "rule", "line", "start_offset", "end_offset",
        "source", "target", "anchor_family", "body_signature", "asset_reference") if key in operation}}


def _approved(
    operation: dict[str, Any], approval: dict[str, Any], *, file: str,
    repaired: str, project_root: Path | None, review_binding: dict[str, Any] | None,
) -> tuple[bool, str]:
    if operation["status"] != "manual_review":
        return False, "hard_policy_block_cannot_be_approved"
    reviewable = {
        "restricted_spacing", "negative_layout_spacing", "body_line_compression",
        "forced_page_break", "scaled_table", "scaled_graphics",
        "local_explicit_font_size",
        "graphic_content_options",
    }
    if operation["rule"] not in reviewable:
        return False, "operation_requires_separate_review"
    if operation["rule"] in {"scaled_table", "scaled_graphics"} and not _supported_scale_arguments(operation):
        return False, "opaque_scale_arguments_require_separate_review"
    if approval.get("file") != file or approval.get("line") != operation["line"]:
        return False, "operation_anchor_does_not_match"
    if approval.get("command") != operation["command"] or approval.get("arguments") != operation["arguments"]:
        return False, "operation_arguments_do_not_match"
    evidence = approval.get("evidence")
    if (not isinstance(evidence, dict) or evidence.get("source") != "host_review"
            or not isinstance(evidence.get("reason"), str) or not evidence["reason"].strip()
            or evidence.get("visual_inspected") is not True
            or evidence.get("all_pages_inspected") is not True):
        return False, "explicit_full_document_host_review_required"
    if approval.get("exact_operation") != _exact_operation(operation, file):
        return False, "exact_operation_binding_missing_or_changed"
    if project_root is None or not review_binding or review_binding.get("status") != "available":
        return False, "current_compile_and_artifact_binding_unavailable"
    if evidence.get("binding") != review_binding["binding"]:
        return False, "source_pdf_page_or_compile_binding_changed"
    try:
        root = Path(project_root).resolve()
        path = root / _relative_path(root, file)
        if path.read_text(encoding="utf-8", errors="surrogateescape") != repaired:
            return False, "comparison_source_does_not_match_current_file"
    except (OSError, ValueError):
        return False, "current_source_unavailable"
    scope = evidence.get("scope")
    if operation["rule"] in {"restricted_spacing", "negative_layout_spacing", "body_line_compression"}:
        return (True, "source_bound_host_review") if scope == "local" else (False, "local_scope_required")
    if operation["rule"] == "forced_page_break":
        if scope == "endmatter" and operation["command"] == "clearpage" and evidence.get("endmatter_intrusion") is True:
            return True, "source_bound_host_review"
        if scope == "pagination" and evidence.get("no_blank_padding") is True:
            return True, "source_bound_host_review"
        return False, "content_flow_pagination_review_required"
    if operation["rule"] in {"scaled_table", "scaled_graphics"}:
        expected = {"table"} if operation["rule"] == "scaled_table" else {"figure", "graphics"}
        if scope in expected and evidence.get("readability_confirmed") is True:
            return True, "source_bound_host_review"
        return False, "rendered_readability_review_required"
    if operation["rule"] == "local_explicit_font_size":
        if (scope == "local" and evidence.get("readability_confirmed") is True
                and isinstance(evidence.get("measurement_context"), str)
                and evidence["measurement_context"].strip()):
            return True, "source_bound_host_measurement_review"
        return False, "local_font_measurement_and_readability_review_required"
    if operation["rule"] == "graphic_content_options":
        asset = evidence.get('source_asset_input')
        files = review_binding['binding']['imported_inputs']['files']
        if not isinstance(asset, dict) or asset not in files:
            return False, 'current_imported_source_asset_identity_required'
        reference_text = operation.get('asset_reference', '')
        if not isinstance(reference_text, str) or any(char in reference_text for char in '\\{}'):
            return False, 'literal_source_asset_reference_required'
        reference = Path(reference_text)
        if not reference.name or reference.is_absolute():
            return False, 'literal_relative_source_asset_reference_required'
        # Native TeX resolves ordinary relative inputs from the entry directory.
        # Search-path references may have a prefix and omit the extension, but
        # must still identify one imported asset, including literal directories.
        def path_matches(candidate, expected):
            return (candidate == expected if expected.suffix
                    else candidate.with_suffix('') == expected)
        entry = Path(review_binding['binding']['main_tex'])
        try:
            direct = (Path(project_root).resolve() / entry.parent / reference).resolve().relative_to(Path(project_root).resolve())
        except (OSError, ValueError):
            return False, 'source_asset_reference_escapes_project'
        candidates = [row for row in files if path_matches(Path(row['path']), direct)]
        if '..' not in reference.parts:
            parts = reference.parts
            candidates.extend(row for row in files if row not in candidates
                              and path_matches(Path(*Path(row['path']).parts[-len(parts):]), reference))
        if len(candidates) != 1 or asset != candidates[0]:
            return False, 'unambiguous_source_asset_reference_identity_required'
        if (scope in {'figure', 'graphics'} and evidence.get('readability_confirmed') is True
                and evidence.get('source_asset_inspected') is True
                and evidence.get('visible_content_preservation_confirmed') is True):
            return True, 'source_bound_host_graphic_content_review'
        return False, 'source_asset_and_visible_content_review_required'
    return False, "operation_requires_separate_review"



def compare_layout_policy(
    original: str,
    repaired: str,
    *,
    bounded_layout: bool = True,
    approvals: Iterable[dict[str, Any]] = (),
    file: str = "",
    project_root: Path | None = None,
    review_context: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Inspect newly introduced operations or changed scale/spacing arguments."""
    prior = Counter(_signature(operation) for operation in _operations(original, bounded_layout=bounded_layout))
    approvals = [approval for approval in approvals if isinstance(approval, dict) and approval.get("approved") is True]
    review_binding = None
    if approvals and project_root is not None and isinstance(review_context, dict):
        try:
            review_binding = collect_layout_review_binding(project_root, **review_context)
        except TypeError:
            review_binding = {"status": "unavailable", "binding": None, "blocking_checks": ["review context incomplete or unsupported"]}
    findings: list[dict[str, Any]] = []
    approved_operations: list[dict[str, Any]] = []
    approval_evaluations: list[dict[str, Any]] = []
    for operation in _operations(repaired, bounded_layout=bounded_layout):
        signature = _signature(operation)
        if prior[signature]:
            prior[signature] -= 1
            continue
        matched = False
        for approval in approvals:
            if approval.get("file") != file or approval.get("line") != operation["line"] or approval.get("command") != operation["command"]:
                continue
            accepted, reason = _approved(operation, approval, file=file, repaired=repaired, project_root=project_root, review_binding=review_binding)
            approval_evaluations.append({"exact_operation": _exact_operation(operation, file), "approved": accepted, "reason": reason})
            if accepted:
                approved_operations.append({"exact_operation": _exact_operation(operation, file), "evidence": approval["evidence"]})
                matched = True
                break
        if matched:
            continue
        findings.append({**operation, "file": file, "change": "introduced_or_changed"})
    if approved_operations and review_binding and project_root is not None:
        try:
            verify_layout_review_binding_current(project_root, review_binding["binding"])
        except (OSError, ValueError, KeyError, TypeError) as error:
            review_binding = {"status": "unavailable", "binding": None, "blocking_checks": [str(error)]}
            for approved in approved_operations:
                exact = approved["exact_operation"]
                findings.append({**exact, "status": "manual_review", "change": "review_binding_changed"})
            approved_operations = []
            for evaluation in approval_evaluations:
                if evaluation["approved"]:
                    evaluation.update(approved=False, reason="evidence_changed_at_final_read_barrier")
    status = "blocked" if any(finding["status"] == "blocked" for finding in findings) else "manual_review" if findings else "pass"
    return {
        "schema_version": "1.0",
        "checked": True,
        "status": status,
        "bounded_layout": bounded_layout,
        "findings": findings,
        "approved_operations": approved_operations,
        "approval_evaluations": approval_evaluations,
        "review_binding_status": review_binding["status"] if review_binding else "not_provided",
        "binding_blocking_checks": review_binding["blocking_checks"] if review_binding else [],
        "blocking_count": sum(finding["status"] == "blocked" for finding in findings),
        "review_count": sum(finding["status"] == "manual_review" for finding in findings),
        "scope_note": "Static policy diff; does not establish scientific preservation or visual quality.",
    }


def combine_policy_reports(reports: Iterable[dict[str, Any]]) -> dict[str, Any]:
    reports = list(reports)
    if not reports:
        return {"schema_version": "1.0", "checked": False, "status": "manual_review", "findings": [], "reason": "no tracked TeX baseline available"}
    findings = [finding for report in reports for finding in report.get("findings", [])]
    return {
        "schema_version": "1.0",
        "checked": all(report.get("checked") is True for report in reports),
        "approved_operations": [item for report in reports for item in report.get("approved_operations", [])],
        "approval_evaluations": [item for report in reports for item in report.get("approval_evaluations", [])],
        "status": "blocked" if any(f["status"] == "blocked" for f in findings) else "manual_review" if findings else "pass",
        "findings": findings,
        "blocking_count": sum(f["status"] == "blocked" for f in findings),
        "review_count": sum(f["status"] == "manual_review" for f in findings),
        "scope_note": "Static policy diff; does not establish scientific preservation or visual quality.",
    }


def snapshot_source_edit_checks(project_root: Path, rollback_target: str | Path, *, approvals: Iterable[dict[str, Any]] = (), review_context: dict[str, str] | None = None, source_reviews: Iterable[dict[str, Any]] = ()) -> dict[str, Any]:
    """Read a verified baseline once for live policy and layout-only prose checks."""
    from runtime_snapshots import _resolve_backup_file, load_snapshot_manifest, resolve_manifest_path, validate_snapshot_manifest
    from layout_prose_integrity import compare_layout_prose
    from scientific_integrity import scientific_payload_diff
    from source_review import apply_source_review

    root = project_root.resolve()
    source_reviews = list(source_reviews)
    try:
        manifest_path = resolve_manifest_path(root, rollback_target)
        manifest = load_snapshot_manifest(root, rollback_target)
        validation = validate_snapshot_manifest(root, manifest, snapshot_dir=manifest_path.parent)
        if not validation.get("valid"):
            raise ValueError("invalid rollback snapshot")
        if any(not isinstance(item.get("sha256"), str) or not re.fullmatch(r"[0-9a-fA-F]{64}", item["sha256"]) for item in manifest.get("files", [])):
            raise ValueError("rollback snapshot baseline hashes unavailable")
        reports = []
        prose_reports = []
        scientific_reports = []
        source_review_decisions = []
        changed_files = []
        original_sources = {}
        repaired_sources = {}
        for item in manifest.get("files", []):
            relative = str(item.get("path") or "")
            if Path(relative).suffix.lower() != ".tex":
                continue
            baseline = _resolve_backup_file(root, manifest_path.parent, item)
            current = root / relative
            if baseline is None or not current.is_file():
                raise ValueError(f"source unavailable: {relative}")
            if not current.resolve().is_relative_to(root) or any(path.is_symlink() for path in (current, *current.parents) if path != root and path.is_relative_to(root)):
                raise ValueError(f"source path escaped or became a symlink: {relative}")
            original_raw = baseline.read_bytes().decode("utf-8", errors="surrogateescape")
            repaired_raw = current.read_bytes().decode("utf-8", errors="surrogateescape")
            # Preserve byte-bound identities for include evidence while keeping
            # the established universal-newline source-review API unchanged.
            original = original_raw.replace("\r\n", "\n").replace("\r", "\n")
            repaired = repaired_raw.replace("\r\n", "\n").replace("\r", "\n")
            original_sources[relative] = original_raw
            repaired_sources[relative] = repaired_raw
            if original != repaired:
                changed_files.append(relative)
            reports.append(compare_layout_policy(
                original, repaired,
                file=relative, approvals=approvals, project_root=root, review_context=review_context,
            ))
            if original == repaired:
                prose_reports.append({"file": relative, "checked": True, "status": "pass", "comparison": "unchanged_source"})
                scientific_reports.append({"file": relative, "checked": True, "status": "pass", "comparison": "unchanged_source"})
            else:
                prose_report = compare_layout_prose(original, repaired)
                science_report = scientific_payload_diff(original, repaired)
                matching_reviews = [review for review in source_reviews if isinstance(review, dict)
                                    and review.get("review_scope") != "literal_include_context" and review.get("file") == relative]
                science_report, prose_report, decision = apply_source_review(
                    original, repaired, project_root=root, file=relative,
                    review=matching_reviews[0] if len(matching_reviews) == 1 else None,
                    scientific=science_report, prose=prose_report,
                )
                if len(matching_reviews) > 1:
                    decision = {**decision, "checked": True, "status": "manual_review", "reason": "multiple source reviews for the same file"}
                source_review_decisions.append({"file": relative, **decision})
                prose_reports.append({"file": relative, **prose_report})
                scientific_reports.append({"file": relative, **science_report})
        # A fragment's file-local parser has no information about a float in
        # its parent. Reconstruct only a bounded, unchanged literal include
        # graph. Keep the raw checks, and route exact wrapper-only ownership
        # changes to review rather than declaring removed content/body shrink.
        try:
            from include_context import compare_include_contexts
        except ModuleNotFoundError:
            from .include_context import compare_include_contexts
        ownership_components = {"tables", "math_floats", "numbers_floats", "protected_arguments",
                                "layout_objects", "caption_label_bindings", "unowned_float_payload"}
        context_files = sorted({
            item["file"] for item in scientific_reports
            if item["file"] in changed_files and item.get("status") not in {"pass", "unavailable"}
            and ownership_components.intersection(item.get("changed_components", []))
        } | {
            finding["file"] for item in reports for finding in item.get("findings", [])
            if finding.get("rule") == "body_font_shrink"
        })
        # Snapshot metadata is an OS path, not a literal TeX argument. Windows
        # callers may use backslashes here; normalize its checked project path
        # while leaving include argument validation deliberately strict.
        manifest_main = str(manifest.get("main_tex") or "")
        main_relative = (root / manifest_main).resolve().relative_to(root).as_posix() if manifest_main else ""
        include_context = compare_include_contexts(
            original_sources, repaired_sources, main_tex=main_relative,
            files_to_check=context_files,
        )
        for context in include_context.get("contexts", []):
            files = set(context["files"])
            context_font_blocked = any(
                finding.get("rule") in {"body_font_shrink", "body_font_size", "opaque_font_definition"}
                for finding in context["layout_policy"].get("findings", [])
            )
            if not context_font_blocked:
                for item in reports:
                    if item.get("file") not in files:
                        # compare_layout_policy records the file on findings,
                        # rather than on the report itself.
                        if not any(finding.get("file") in files for finding in item.get("findings", [])):
                            continue
                    removed = [finding for finding in item.get("findings", [])
                               if finding.get("file") in files and finding.get("rule") == "body_font_shrink"]
                    if not removed:
                        continue
                    item["file_local_report"] = {key: value for key, value in item.items() if key != "file_local_report"}
                    item["findings"] = [finding for finding in item["findings"] if finding not in removed]
                    item["include_context_corrections"] = {"root_file": context["root_file"], "removed_file_local_findings": removed,
                                                           "reason": "literal_parent_scope_is_not_body_font_shrink"}
                    item["blocking_count"] = sum(finding["status"] == "blocked" for finding in item["findings"])
                    item["review_count"] = sum(finding["status"] == "manual_review" for finding in item["findings"])
                    item["status"] = "blocked" if item["blocking_count"] else "manual_review" if item["findings"] else "pass"
            if context["layout_policy"].get("findings"):
                # Includes may also expose a real leak not visible in either
                # file alone. Expanded offsets are explicitly labelled and
                # never matched against a file-local approval.
                reports.append({**context["layout_policy"], "coordinate_system": "expanded_literal_include_context",
                                "source_identities": context["source_identities"],
                                "findings": [{**finding, "coordinate_system": "expanded_literal_include_context",
                                              "root_file": context["root_file"], "source_files": context["files"]}
                                             for finding in context["layout_policy"]["findings"]]})
            if context["float_wrapper_residual_equal"] and context.get("requires_host_review"):
                include_reviews = [review for review in source_reviews if isinstance(review, dict)
                                   and review.get("review_scope") == "literal_include_context"
                                   and review.get("file") == context["root_file"]]
                if len(include_reviews) > 1:
                    include_decision = {"checked": True, "status": "manual_review", "reason": "multiple include context reviews for the same owning file"}
                elif include_reviews:
                    try:
                        from source_review import validate_include_context_review
                    except ModuleNotFoundError:
                        from .source_review import validate_include_context_review
                    include_decision = validate_include_context_review(
                        project_root=root, rollback_target=rollback_target, file=context["root_file"], review=include_reviews[0])
                else:
                    include_decision = {"checked": True, "status": "not_used", "verification_method": "explicit_host_review"}
                source_review_decisions.append({"file": context["root_file"], "review_scope": "literal_include_context", **include_decision})
                include_verified = include_decision.get("status") == "verified"
                for channel in (scientific_reports, prose_reports):
                    for index, item in enumerate(channel):
                        if item.get("file") not in files or item.get("status") == "pass" or item.get("checked") is not True:
                            continue
                        raw = dict(item)
                        channel[index] = {**item, "status": "pass" if include_verified else "manual_review", "file_local_report": raw,
                                          "verification_method": "explicit_host_review" if include_verified else "bounded_include_context_requires_host_review",
                                          "assessment": {"classification": "include_float_scope_uncertainty",
                                                         "scope": "exact_ordered_source_residual",
                                                         "semantic_damage_proven": False, "requires_host_review": not include_verified,
                                                         "confirmed_changed_components": [],
                                                         "review_changed_components": item.get("changed_components", [])},
                                          "regression_reasons": [] if include_verified else ["Float ownership changed across literal includes; review numbering and the fresh PDF."],
                                          "include_context_root": context["root_file"], "host_review": include_decision}
                scientific_reports.append({"file": context["root_file"], "checked": True,
                                           "status": "pass" if include_verified else "manual_review",
                                           "comparison": "include_float_scope_review", "requires_host_review": not include_verified,
                                           "host_review": include_decision, "reason": context["host_check"]})
        report = combine_policy_reports(reports)
        report["file_reports"] = reports
        report["rollback_target"] = str(rollback_target)
        prose_findings = [item for item in prose_reports if item.get("status") != "pass"]
        prose_checked = bool(prose_reports) and all(item.get("checked") is True for item in prose_reports)
        prose = {"checked": prose_checked, "status": "blocked" if any(item.get("status") in {"failed", "blocked"} for item in prose_reports) else "manual_review" if not prose_checked or prose_findings else "pass", "findings": prose_findings, "file_reports": prose_reports}
        scientific_findings = [item for item in scientific_reports if item.get("status") != "pass"]
        scientific_checked = bool(scientific_reports) and all(item.get("checked") is True for item in scientific_reports)
        scientific = {"checked": scientific_checked, "status": "blocked" if any(item.get("status") in {"failed", "blocked"} for item in scientific_reports) else "manual_review" if not scientific_checked or scientific_findings else "pass", "findings": scientific_findings, "file_reports": scientific_reports}
        channels = [report, prose, scientific]
        return {
            "checked": all(channel.get("checked") is True for channel in channels),
            "status": "blocked" if any(channel["status"] == "blocked" for channel in channels) else "manual_review" if any(channel["status"] != "pass" for channel in channels) else "pass",
            "layout_policy": report,
            "layout_prose": prose,
            "scientific_payload": scientific,
            "changed_files": changed_files,
            "files_checked": len(reports),
            "source_review_decisions": source_review_decisions,
            "include_context": include_context,
            "main_tex": main_relative,
            "rollback_target": str(rollback_target),
        }
    except (OSError, ValueError, KeyError, TypeError) as error:
        unavailable = {"checked": False, "status": "manual_review", "findings": [], "reason": f"source baseline unavailable: {error}"}
        return {**unavailable, "layout_policy": dict(unavailable), "layout_prose": dict(unavailable), "scientific_payload": dict(unavailable)}


def snapshot_layout_policy(project_root: Path, rollback_target: str | Path, *, approvals: Iterable[dict[str, Any]] = (), review_context: dict[str, str] | None = None) -> dict[str, Any]:
    """Compatibility view of the policy channel of the shared source check."""
    return snapshot_source_edit_checks(project_root, rollback_target, approvals=approvals, review_context=review_context)["layout_policy"]


def build_layout_review_request(
    original: str, repaired: str, *, project_root: Path, main_tex: str,
    page_images_dir: str, file: str,
    compile_journal: str = "data/compile/journal.jsonl",
) -> dict[str, Any]:
    """Produce an unsigned request for actual full-PDF host review, not approval."""
    context = {"main_tex": main_tex, "page_images_dir": page_images_dir,
               "compile_journal": compile_journal}
    current = collect_layout_review_binding(project_root, **context)
    policy = compare_layout_policy(original, repaired, file=file)
    approvals = []
    scopes = {
        "scaled_table": "table", "scaled_graphics": "graphics",
        "forced_page_break": "pagination", "restricted_spacing": "local",
        "negative_layout_spacing": "local", "body_line_compression": "local",
        "local_explicit_font_size": "local",
        "graphic_content_options": "graphics",
    }
    for finding in policy["findings"]:
        reviewable = finding["status"] == "manual_review" and finding["rule"] in scopes
        if finding["rule"] in {"scaled_table", "scaled_graphics"} and not _supported_scale_arguments(finding):
            reviewable = False
        evidence = {
            "source": "host_review", "reason": "", "visual_inspected": False,
            "all_pages_inspected": False, "scope": scopes.get(finding["rule"], "unsupported"),
            "binding": current["binding"],
        }
        if finding["rule"] == "forced_page_break":
            evidence["no_blank_padding"] = False
        if finding["rule"] in {"scaled_table", "scaled_graphics"}:
            evidence["readability_confirmed"] = False
        if finding["rule"] == "local_explicit_font_size":
            evidence.update(readability_confirmed=False, measurement_context="")
        if finding["rule"] == "graphic_content_options":
            evidence.update(readability_confirmed=False, source_asset_inspected=False,
                            visible_content_preservation_confirmed=False, source_asset_input=None)
        approvals.append({
            "approved": False, "can_host_approve": reviewable,
            "file": file, "line": finding["line"], "command": finding["command"],
            "arguments": finding["arguments"],
            "exact_operation": _exact_operation(finding, file), "evidence": evidence,
        })
    return {
        "schema_version": _REVIEW_REQUEST_SCHEMA, "context": context, "file": file,
        "policy": policy, "binding_status": current["status"],
        "blocking_checks": current["blocking_checks"], "approvals": approvals,
        "next_step": "View every current page image and the fresh PDF; explicitly fill reason and review confirmations and set approved=true only for acceptable operations, then validate. Regenerate the request after any source/build/image changes.",
        "scope_note": "Policy approval only. Scientific/prose preservation and final goal acceptance remain independent; this command neither edits nor certifies a manuscript.",
    }


def _review_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("review request unavailable")
    def unique_keys(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate review JSON key")
            value[key] = item
        return value
    value = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique_keys)
    if not isinstance(value, dict) or value.get("schema_version") != _REVIEW_REQUEST_SCHEMA:
        raise ValueError("unsupported review request schema")
    if not isinstance(value.get("approvals"), list):
        raise ValueError("review approvals unavailable")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only, source-bound layout host review. This is not manuscript acceptance.")
    parser.add_argument("mode", choices=("request", "validate"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--main-tex", required=True)
    parser.add_argument("--file", help="Changed TeX file relative to root; defaults to main-tex.")
    parser.add_argument("--baseline-tex", required=True, help="Preserved original/checkpoint TeX inside the project.")
    parser.add_argument("--pages-dir", default="data/pages")
    parser.add_argument("--compile-journal", default="data/compile/journal.jsonl")
    parser.add_argument("--review-json", help="Generated request after explicit host review; required for validate.")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        file = _relative_path(root, args.file or args.main_tex)
        original = (root / _relative_path(root, args.baseline_tex)).read_text(encoding="utf-8", errors="surrogateescape")
        repaired = (root / file).read_text(encoding="utf-8", errors="surrogateescape")
        context = {"main_tex": args.main_tex, "page_images_dir": args.pages_dir,
                   "compile_journal": args.compile_journal}
        if args.mode == "request":
            result = build_layout_review_request(original, repaired, project_root=root, file=file, **context)
            exit_code = 2 if result["policy"]["status"] == "blocked" else 3 if result["policy"]["findings"] else 0
        else:
            if not args.review_json:
                raise ValueError("--review-json required for validate")
            request = _review_json(root / _relative_path(root, args.review_json))
            result = compare_layout_policy(original, repaired, file=file, approvals=request["approvals"],
                                           project_root=root, review_context=context)
            result["goal_acceptance_checked"] = False
            result["scientific_and_prose_preservation_checked"] = False
            exit_code = 2 if result["status"] == "blocked" else 3 if result["status"] != "pass" else 0
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return exit_code
    except (OSError, ValueError, TypeError, UnicodeError) as error:
        print(json.dumps({"checked": False, "status": "manual_review", "reason": str(error),
                          "goal_acceptance_checked": False}, ensure_ascii=False))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
