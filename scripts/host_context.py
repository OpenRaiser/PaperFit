"""Read-only, compact evidence and source navigation for the host Agent."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from typing import Any
try:
    from source_map import build_source_map, within
except ImportError:
    from .source_map import build_source_map, within

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
    if vision["clean"] is True and vision["status"] != "completed":
        vision["clean"] = None
        vision["diagnosis_report_status"] = "inconsistent"
    # Preserve the provider's actual status. Missing/failed vision is not a clean page.
    return {
        "schema_version": "1.0", "main": sources["main"],
        "project_root": str(root), "project_root_resolution": root_resolution,
        "reports": {"action_plan": plan_status, "visual": visual_status,
                    "full_action_plan": str(data_dir / "visual_action_plan.json"),
                    "full_visual_report": str(data_dir / "visual_signal_report.json")},
        "vision": vision, "vision_source": vision_source,
        "source_navigation_coverage": sources["object_marker_coverage"],
        "finding_counts": {"total": len(compact), "shown": min(limit, len(compact)), "omitted": max(0, len(compact)-limit)},
        "findings": compact[:limit], "source_map": sources,
        "host_tasks": [
            "Inspect rendered pages, including pages without machine findings.",
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
