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

_COMMAND = re.compile(r"\\(resizebox|scalebox|newpage|clearpage|pagebreak|vspace)(?![A-Za-z@])")
_TABLE = re.compile(r"\\begin\s*\{(?:tabular\*?|tabularx|tabulary|longtable|table\*?)\}")
_GRAPHIC = re.compile(r"\\includegraphics\*?(?:\s*\[[^\]]*\])?\s*\{([^{}]+)\}")
_FLOAT = re.compile(r"\\(begin|end)\s*\{(table\*?|figure\*?|longtable)\}")


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


def _target(active: str, offset: int, body: str) -> str:
    graphics = tuple(_normalize(value) for value in _GRAPHIC.findall(body))
    if graphics:
        return "graphics:" + ",".join(graphics)
    # A labelled float anchors an existing wrapper even if its tabular layout
    # changes. Unlabelled objects use their literal body to avoid transferring
    # a grandfathered violation to a different table.
    stack: list[tuple[str, int]] = []
    for marker in _FLOAT.finditer(active):
        if marker[1] == "begin":
            stack.append((marker[2], marker.start()))
        elif stack and stack[-1][0] == marker[2]:
            _, start = stack.pop()
            if start <= offset < marker.end():
                labels = re.findall(r"\\label\s*\{([^{}]+)\}", active[start:marker.end()])
                if labels:
                    return "labels:" + ",".join(labels)
    return "body:" + hashlib.sha256(_normalize(body).encode("utf-8")).hexdigest()


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
            target = _target(active, match.start(), body)
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
        })
    return operations


def _signature(operation: dict[str, Any]) -> tuple[Any, ...]:
    return operation["command"], tuple(operation["arguments"]), operation["target"]


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
        if operation["rule"] == "restricted_spacing" and evidence.get("scope") == "local":
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
