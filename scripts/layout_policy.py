"""Diff-based layout policy, separate from scientific-content preservation.

This is static source inspection, not TeX execution or a proof of visual quality.
Existing operations are grandfathered; removals are always allowed. Approvals
are separate caller evidence and are never inferred from a VLM finding or comment.
"""
from __future__ import annotations

from collections import Counter
import hashlib
from pathlib import Path
import re
from typing import Any, Iterable

from source_map import group, line_at, masked_tex
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
        arguments: list[str] = []
        end = pos
        if name in _FONT_COMMANDS:
            lexical_end = scope_end
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
                # The minimum 5pt rule applies to an explicit font point size,
                # not native mathematical scripts or a document-wide font
                # histogram. Relative/unknown spellings need independent review.
                if size and float(size[1]) < 5:
                    operations.append(_presentation_operation(
                        source, start, end, name, arguments,
                        "unreadable_explicit_font_size", "blocked"))
                    continue
                if baseline and float(baseline[1]) <= 0:
                    operations.append(_presentation_operation(
                        source, start, end, name, arguments,
                        "nonpositive_font_baseline", "blocked"))
                    continue
                if local and name not in shadowed_fonts and size and baseline and float(baseline[1]) > 0:
                    continue
            elif local and name not in shadowed_fonts:
                continue
            if name not in shadowed_fonts and not local and ((normal_reset and not active[end:scope_end].strip()) or _table_only_scope(active, end, scope_end, envs)):
                continue
            affected = re.sub(r"\\(?:tiny|scriptsize|footnotesize|small|normalsize|selectfont)"
                              r"(?![A-Za-z@])", "", active[end:scope_end])
            target = "body_font:" + hashlib.sha256(_normalize(affected).encode("utf-8")).hexdigest()
            operations.append(_presentation_operation(
                source, start, end, name, arguments,
                "opaque_font_definition" if name in shadowed_fonts else "body_font_shrink" if name in _SMALL_BODY_FONTS else "body_font_size",
                "blocked" if name in _SMALL_BODY_FONTS and name not in shadowed_fonts else "manual_review", target))
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
            disposition = "blocked" if kind != "unknown" else "manual_review"
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
            disposition = "manual_review" if name == "clearpage" else "blocked"
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
    for declaration in scientific.arraystretch_declarations(source):
        if declaration["value"] <= 0:
            operations.append(_presentation_operation(
                source, declaration["start"], declaration["end"],
                declaration["command"], ["\\arraystretch", declaration["value_text"]],
                "nonpositive_table_row_stretch", "blocked"))
    return operations


def _signature(operation: dict[str, Any]) -> tuple[Any, ...]:
    return operation["command"], tuple(operation["arguments"]), operation["target"], operation.get("anchor_family", ""), operation.get("body_signature", "")


def _approved(operation: dict[str, Any], approvals: Iterable[dict[str, Any]], file: str) -> bool:
    # Only reviewable spacing/endmatter pagination has an evidence exception.
    # Scaled tables/graphics and forced newpage shortcuts cannot self-approve.
    if operation["status"] != "manual_review":
        return False
    for approval in approvals:
        if not isinstance(approval, dict) or approval.get("approved") is not True:
            continue
        if approval.get("file", "") != file or approval.get("line") != operation["line"]:
            continue
        if approval.get("command") != operation["command"] or approval.get("arguments") != operation["arguments"]:
            continue
        evidence = approval.get("evidence")
        if not isinstance(evidence, dict) or evidence.get("source") != "host_review" or not evidence.get("reason"):
            continue
        if evidence.get("visual_inspected") is not True or not evidence.get("page_images"):
            continue
        if operation["rule"] in {"restricted_spacing", "negative_layout_spacing", "body_line_compression"} and evidence.get("scope") == "local":
            return True
        if operation["command"] == "clearpage" and evidence.get("scope") == "endmatter" and evidence.get("endmatter_intrusion") is True:
            return True
    return False


def compare_layout_policy(
    original: str,
    repaired: str,
    *,
    bounded_layout: bool = True,
    approvals: Iterable[dict[str, Any]] = (),
    file: str = "",
) -> dict[str, Any]:
    """Inspect newly introduced operations or changed scale/spacing arguments."""
    prior = Counter(_signature(operation) for operation in _operations(original, bounded_layout=bounded_layout))
    findings: list[dict[str, Any]] = []
    for operation in _operations(repaired, bounded_layout=bounded_layout):
        signature = _signature(operation)
        if prior[signature]:
            prior[signature] -= 1
            continue
        if _approved(operation, approvals, file):
            continue
        findings.append({**operation, "file": file, "change": "introduced_or_changed"})
    status = "blocked" if any(finding["status"] == "blocked" for finding in findings) else "manual_review" if findings else "pass"
    return {
        "schema_version": "1.0",
        "checked": True,
        "status": status,
        "bounded_layout": bounded_layout,
        "findings": findings,
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
        "status": "blocked" if any(f["status"] == "blocked" for f in findings) else "manual_review" if findings else "pass",
        "findings": findings,
        "blocking_count": sum(f["status"] == "blocked" for f in findings),
        "review_count": sum(f["status"] == "manual_review" for f in findings),
        "scope_note": "Static policy diff; does not establish scientific preservation or visual quality.",
    }


def snapshot_source_edit_checks(project_root: Path, rollback_target: str | Path, *, approvals: Iterable[dict[str, Any]] = ()) -> dict[str, Any]:
    """Read a verified baseline once for live policy and layout-only prose checks."""
    from runtime_snapshots import _resolve_backup_file, load_snapshot_manifest, resolve_manifest_path, validate_snapshot_manifest
    from layout_prose_integrity import compare_layout_prose
    from scientific_integrity import scientific_payload_diff

    root = project_root.resolve()
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
        changed_files = []
        for item in manifest.get("files", []):
            relative = str(item.get("path") or "")
            if Path(relative).suffix.lower() != ".tex":
                continue
            baseline = _resolve_backup_file(root, manifest_path.parent, item)
            current = root / relative
            if baseline is None or not current.is_file():
                raise ValueError(f"source unavailable: {relative}")
            original = baseline.read_text(encoding="utf-8", errors="surrogateescape")
            repaired = current.read_text(encoding="utf-8", errors="surrogateescape")
            if original != repaired:
                changed_files.append(relative)
            reports.append(compare_layout_policy(
                original, repaired,
                file=relative, approvals=approvals,
            ))
            if original == repaired:
                prose_reports.append({"file": relative, "checked": True, "status": "pass", "comparison": "unchanged_source"})
                scientific_reports.append({"file": relative, "checked": True, "status": "pass", "comparison": "unchanged_source"})
            else:
                prose_reports.append({"file": relative, **compare_layout_prose(original, repaired)})
                scientific_reports.append({"file": relative, **scientific_payload_diff(original, repaired)})
        report = combine_policy_reports(reports)
        report["rollback_target"] = str(rollback_target)
        prose_findings = [item for item in prose_reports if item.get("status") != "pass"]
        prose_checked = bool(prose_reports) and all(item.get("checked") is True for item in prose_reports)
        prose = {"checked": prose_checked, "status": "manual_review" if not prose_checked else "blocked" if prose_findings else "pass", "findings": prose_findings}
        scientific_findings = [item for item in scientific_reports if item.get("status") != "pass"]
        scientific_checked = bool(scientific_reports) and all(item.get("checked") is True for item in scientific_reports)
        scientific = {"checked": scientific_checked, "status": "manual_review" if not scientific_checked else "blocked" if scientific_findings else "pass", "findings": scientific_findings}
        channels = [report, prose, scientific]
        return {
            "checked": all(channel.get("checked") is True for channel in channels),
            "status": "blocked" if any(channel["status"] == "blocked" for channel in channels) else "manual_review" if any(channel["status"] != "pass" for channel in channels) else "pass",
            "layout_policy": report,
            "layout_prose": prose,
            "scientific_payload": scientific,
            "changed_files": changed_files,
            "files_checked": len(reports),
            "rollback_target": str(rollback_target),
        }
    except (OSError, ValueError, KeyError, TypeError) as error:
        unavailable = {"checked": False, "status": "manual_review", "findings": [], "reason": f"source baseline unavailable: {error}"}
        return {**unavailable, "layout_policy": dict(unavailable), "layout_prose": dict(unavailable), "scientific_payload": dict(unavailable)}


def snapshot_layout_policy(project_root: Path, rollback_target: str | Path, *, approvals: Iterable[dict[str, Any]] = ()) -> dict[str, Any]:
    """Compatibility view of the policy channel of the shared source check."""
    return snapshot_source_edit_checks(project_root, rollback_target, approvals=approvals)["layout_policy"]
