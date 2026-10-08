"""Read-only, compact evidence and source navigation for the host Agent."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from typing import Any
try:
    from source_map import build_source_map, within
    from runtime_artifacts import collect_artifact_manifest
    from visibility_review import review_pdf_visibility
except ImportError:
    from .source_map import build_source_map, within
    from .runtime_artifacts import collect_artifact_manifest
    from .visibility_review import review_pdf_visibility

def load_report(path: Path) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        return {}, "missing"
    if path.stat().st_size > 32 * 1024 * 1024:
        return {}, "too_large"
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError):
        return {}, "invalid"
    return (value, "available") if isinstance(value, dict) else ({}, "invalid")


_VISION_FRESHNESS_CHECKS = {
    "pdf_exists", "pdf_not_older_than_source_set", "page_images_exist",
    "pdf_page_count_available", "page_images_complete", "page_images_readable",
    "page_images_not_older_than_pdf", "visual_not_older_than_pages",
}


def evidence_freshness(main: Path, root: Path, data_dir: Path) -> dict[str, Any]:
    """Compare a runtime snapshot with live artifacts, without following its paths."""
    if not within(data_dir, root):
        return {"status": "unknown", "vision_status": "unknown",
                "blocking_checks": ["artifact_manifest_data_directory_external"], "checks": {},
                "manifest_status": "external", "identity": {}}
    artifacts = {name: str(data_dir / filename) for name, filename in {
        "page_images_dir": "pages", "visual_signal_report": "visual_signal_report.json",
        "defect_report": "defect_report.json", "gatekeeper_decision": "gatekeeper_decision.json",
    }.items()}
    try:
        live = collect_artifact_manifest(root, str(main), artifacts)
    except (OSError, ValueError):
        return {"status": "unknown", "vision_status": "unknown",
                "blocking_checks": ["live_artifact_manifest_unavailable"], "checks": {},
                "manifest_status": "unavailable", "identity": {}}
    current = live["artifacts"]
    fresh = dict(live["freshness"])
    blocking = list(fresh["blocking_checks"])
    results = sorted((p for p in data_dir.glob("run_result*.json") if p.is_file()),
                     key=lambda p: p.stat().st_mtime, reverse=True)[:1]
    report, report_status = (load_report(results[0]) if within(results[0].resolve(), root) else ({}, "external")) if results else ({}, "missing")
    recorded = report.get("artifact_manifest")
    recorded = recorded if isinstance(recorded, dict) else {}
    entries = recorded.get("artifacts")
    entries = entries if isinstance(entries, dict) else {}
    recorded_fresh = recorded.get("freshness")
    valid_fresh = (isinstance(recorded_fresh, dict) and
                   recorded_fresh.get("status") in {"pass", "stale_or_missing"} and
                   isinstance(recorded_fresh.get("blocking_checks"), list))
    snapshot_status = "available" if entries and report_status == "available" and valid_fresh else "missing_or_invalid"
    identity: dict[str, Any] = {}
    def valid_hash(value: Any) -> bool:
        return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdefABCDEF" for c in value)

    unknown = snapshot_status != "available"
    if unknown:
        blocking.append("artifact_manifest_missing_or_invalid")
    else:
        # Snapshot paths are metadata only. A path outside this project cannot
        # authorize reading another project or asserting current applicability.
        expected = {"main_tex": main, "pdf": main.with_suffix(".pdf"),
                    "visual_signal_report": data_dir / "visual_signal_report.json"}
        for name, target in expected.items():
            entry = entries.get(name)
            path = entry.get("path") if isinstance(entry, dict) else None
            try:
                candidate = (root / path).resolve() if isinstance(path, str) else None
                valid = (candidate is not None and within(candidate, root) and candidate == target.resolve()
                         and valid_hash(entry.get("sha256")))
            except (OSError, ValueError):
                valid = False
            if not valid:
                blocking.append("artifact_manifest_" + name + "_path_invalid")
                unknown = True
        for name, field in (("source_set", "files"), ("page_images", "pages")):
            entry = entries.get(name)
            members = entry.get(field) if isinstance(entry, dict) else None
            if not isinstance(members, list) or not members:
                blocking.append("artifact_manifest_" + name + "_invalid")
                unknown = True
                continue
            if entry.get("count") != len(members) or (name == "page_images" and not valid_hash(entry.get("aggregate_sha256"))):
                blocking.append("artifact_manifest_" + name + "_identity_invalid")
                unknown = True
            for member in members:
                path = member.get("path") if isinstance(member, dict) else None
                try:
                    valid = (isinstance(path, str) and within((root / path).resolve(), root)
                             and valid_hash(member.get("sha256")))
                except (OSError, ValueError):
                    valid = False
                if not valid:
                    blocking.append("artifact_manifest_" + name + "_path_invalid")
                    unknown = True
                    break
        if not unknown:
            for name in ("main_tex", "pdf", "visual_signal_report", "page_images", "source_set"):
                before, after = entries.get(name, {}), current.get(name, {})
                if name == "source_set":
                    before_id = sorted((x.get("path"), x.get("sha256")) for x in before.get("files", []))
                    after_id = sorted((x.get("path"), x.get("sha256")) for x in after.get("files", []))
                else:
                    field = "aggregate_sha256" if name == "page_images" else "sha256"
                    before_id, after_id = before.get(field), after.get(field)
                matches = bool(before_id) and before_id == after_id
                identity[name + "_matches_snapshot"] = matches
                if not matches:
                    blocking.append("artifact_manifest_" + name + "_changed")
    recorded_fresh = recorded.get("freshness")
    recorded_fresh = recorded_fresh if isinstance(recorded_fresh, dict) else {}
    recorded_blocking = recorded_fresh.get("blocking_checks", [])
    recorded_blocking = [x for x in recorded_blocking if isinstance(x, str)] if isinstance(recorded_blocking, list) else []
    blocking = list(dict.fromkeys(blocking + recorded_blocking))
    vision_blocking = [x for x in blocking if x in _VISION_FRESHNESS_CHECKS or x.startswith("artifact_manifest_")]
    essentials_missing = not all(current.get(name, {}).get("exists") for name in ("pdf", "page_images", "visual_signal_report"))
    vision_status = "unknown" if unknown or essentials_missing else ("stale_or_missing" if vision_blocking else "current")
    identity.update({"main_tex": {key: current["main_tex"].get(key) for key in ("path", "sha256")},
                     "pdf": {key: current["pdf"].get(key) for key in ("path", "sha256")},
                     "page_images": {key: current["page_images"].get(key) for key in ("count", "aggregate_sha256")}})
    return {"status": "unknown" if unknown else ("stale_or_missing" if blocking else "pass"),
            "vision_status": vision_status, "blocking_checks": blocking,
            "vision_blocking_checks": vision_blocking, "checks": fresh["checks"],
            "manifest_status": snapshot_status,
            "manifest_path": str(results[0]) if results else None,
            "recorded_status": recorded_fresh.get("status", "unknown"),
            "recorded_blocking_checks": recorded_blocking, "identity": identity}

def build_host_context(main: Path, *, root: Path | None = None, data_dir: Path | None = None, limit: int = 30) -> dict[str, Any]:
    if limit < 1:
        raise ValueError("limit must be positive")
    main = main.resolve()
    if root is not None:
        root = root.resolve()
        root_resolution = "explicit"
    elif within(main, Path.cwd().resolve()):
        root = Path.cwd().resolve()
        root_resolution = "working_directory"
    else:
        root = main.parent
        root_resolution = "main_parent_fallback"
    sources = build_source_map(main, root=root)
    data_dir = Path(data_dir).resolve() if data_dir is not None else root / "data"
    plan, plan_status = load_report(data_dir / "visual_action_plan.json")
    visual, visual_status = load_report(data_dir / "visual_signal_report.json")
    raw_entries = plan.get("entries", [])
    if not isinstance(raw_entries, list):
        raw_entries = []
        plan_status = "invalid"
    entries = [x for x in raw_entries if isinstance(x, dict)]
    indexed = {}
    for obj in sources["objects"]:
        for label in obj["labels"]:
            indexed.setdefault(label, []).append(obj)
    compact = []
    ranks = {"critical": 0, "blocking": 0, "major": 1, "degrading": 1, "minor": 2, "cosmetic": 3}
    for item in sorted(entries, key=lambda x: (ranks.get(str(x.get("severity")).lower(), 4), x.get("page") if type(x.get("page")) is int else 0)):
        obj_hits = indexed.get(item.get("label"), [])
        expected_kind = {"figure_like": "figure", "figure": "figure", "table_like": "table", "table": "table"}.get(item.get("object_type"))
        if expected_kind:
            obj_hits = [o for o in obj_hits if o["kind"] == expected_kind]
        obj = obj_hits[0] if len(obj_hits) == 1 and obj_hits[0]["anchor_status"] == "resolved" else None
        status = item.get("status")
        # A duplicate/dynamic source never inherits a legacy executable assertion.
        if item.get("label") and obj is None and status == "executable":
            status = "human_review"
        compact.append({
            "finding_id": item.get("finding_id"), "page": item.get("page"), "bbox": item.get("bbox"),
            "family": item.get("family"), "severity": item.get("severity"),
            "status": status, "confidence": item.get("confidence"),
            "evidence": item.get("evidence"), "recommended_actions": item.get("recommended_actions"),
            "label": item.get("label"),
            "source_anchor": {"file": obj["file"], "start_line": obj["start_line"], "end_line": obj["end_line"]} if obj else None,
            "source_anchor_status": "resolved" if obj else ("ambiguous" if len(obj_hits) > 1 else "requires_host_review"),
            "graphics": obj["graphics"] if obj else [],
            "source_environment": obj["environment"] if obj else None,
            "environment_arguments": obj.get("environment_arguments", {}) if obj else {},
        })
    # The explicit diagnosis is authoritative; old provenance summaries may be stale.
    if "vlm_diagnosis" in visual:
        value = visual["vlm_diagnosis"]
        vision = dict(value) if isinstance(value, dict) else {"status": "unknown", "diagnosis_report_status": "invalid"}
        vision_source = "vlm_diagnosis"
    else:
        machine = visual.get("machine_sources")
        value = machine.get("vlm") if isinstance(machine, dict) else None
        vision = dict(value) if isinstance(value, dict) else {}
        vision_source = "machine_sources.vlm" if isinstance(value, dict) else "unavailable"
    vision.setdefault("status", "unknown")
    vision.setdefault("clean", None)
    provider_clean = vision["clean"]
    if vision["clean"] is True and vision["status"] != "completed":
        vision["clean"] = None
        vision["diagnosis_report_status"] = "inconsistent"
    freshness = evidence_freshness(main, root, data_dir)
    vision["provider_status"] = vision["status"]
    vision["provider_clean"] = provider_clean
    vision["evidence_status"] = freshness["vision_status"]
    vision["applicable_to_current_artifacts"] = (
        None if freshness["vision_status"] == "unknown" else freshness["vision_status"] == "current")
    if freshness["vision_status"] != "current":
        vision["clean"] = None
    # Provider completion describes its snapshot; freshness describes this project now.
    visibility = review_pdf_visibility(main.with_suffix('.pdf'), root=root,
                           reviews_path=data_dir / 'pdf_visibility_reviews.json')
    return {
        "schema_version": "1.0", "main": sources["main"],
        "project_root": str(root), "project_root_resolution": root_resolution,
        "reports": {"action_plan": plan_status, "visual": visual_status,
                    "full_action_plan": str(data_dir / "visual_action_plan.json"),
                    "full_visual_report": str(data_dir / "visual_signal_report.json")},
        "vision": vision, "vision_source": vision_source,
        "pdf_visibility": visibility,
        "artifact_freshness": freshness,
        "source_navigation_coverage": sources["object_marker_coverage"],
        "finding_counts": {"total": len(compact), "shown": min(limit, len(compact)), "omitted": max(0, len(compact)-limit)},
        "findings": compact[:limit], "source_map": sources,
        "host_tasks": [
            "Inspect rendered pages, including pages without machine findings.",
            "Review painted-text bounds in pdf_visibility even when VLM reports no defects; intentional clipping needs current-PDF and inspected-page evidence.",
            "Review freshness blocking checks; stale or unknown vision is not a clean current PDF.",
            "Treat recommendations as evidence; verify the target before editing.",
            "Use exact source files and lines, not flattened include-expansion line numbers.",
            "Review unsupported object/caption markers and uncertain source anchors; static navigation is not TeX-expanded coverage.",
            "Compile and render after source edits; preserve scientific content and template requirements.",
            "Report unresolved findings and incomplete visual coverage explicitly."
        ],
    }

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--main", required=True)
    parser.add_argument("--root", help="Project/compilation root; defaults to cwd when it contains main, otherwise main.parent")
    parser.add_argument("--data-dir")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--output")
    args = parser.parse_args()
    payload = build_host_context(Path(args.main), root=Path(args.root) if args.root else None, data_dir=Path(args.data_dir) if args.data_dir else None, limit=args.limit)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        dest = Path(args.output)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        print(json.dumps({"output": str(dest), "finding_counts": payload["finding_counts"],
                          "source_objects": len(payload["source_map"]["objects"]),
                          "source_warnings": len(payload["source_map"]["warnings"])}))
    else:
        print(text)
if __name__ == "__main__":
    main()
