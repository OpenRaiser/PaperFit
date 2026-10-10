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
    from pdf_layout_profile import profile_pdf_layout
    from page_objective import evaluate_page_objective
    from runtime_types import TaskSpec
    from state_schema import validate_state
    from isolated_build import verify_isolated_build, is_included_input_path
    from compile_inputs import verify_native_build
    from host_advice import host_guidance, joint_layout_plan, recommendation_review, print_host_json
except ImportError:
    from .source_map import build_source_map, within
    from .runtime_artifacts import collect_artifact_manifest
    from .visibility_review import review_pdf_visibility
    from .pdf_layout_profile import profile_pdf_layout
    from .page_objective import evaluate_page_objective
    from .runtime_types import TaskSpec
    from .state_schema import validate_state
    from .isolated_build import verify_isolated_build, is_included_input_path
    from .compile_inputs import verify_native_build
    from .host_advice import host_guidance, joint_layout_plan, recommendation_review, print_host_json

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

def bind_visibility_to_current_source(visibility: dict[str, Any], freshness: dict[str, Any], *,
                                      isolated_binding: dict[str, Any] | None = None,
                                      native_binding: dict[str, Any] | None = None) -> dict[str, Any]:
    """Geometry of a neighboring PDF does not establish a current source build."""
    result = dict(visibility)
    runtime_bound = (result.get('pdf_location') != 'external' and
             native_binding is not None and native_binding.get('status') == 'current' and
             native_binding.get('source_pdf_binding') == 'verified_native_inputs' and
             result.get('pdf_path') == native_binding.get('pdf_path') and
             bool(result.get("pdf_sha256")) and
             result.get("pdf_sha256") == native_binding.get('pdf_sha256'))
    isolated_bound = (isolated_binding is not None and
                      isolated_binding.get('status') == 'current' and
                      isolated_binding.get('source_pdf_binding') == 'verified_isolated_build' and
                      bool(result.get('pdf_sha256')) and
                      result.get('pdf_sha256') == isolated_binding.get('pdf_sha256'))
    # An explicitly selected build directory must verify itself; a neighboring
    # runtime PDF cannot rescue a wrong, failed or stale isolated build receipt.
    bound = isolated_bound if isolated_binding is not None else runtime_bound
    result["applicable_to_current_source"] = True if bound else None
    result["source_pdf_binding"] = (
        'verified_isolated_build' if isolated_bound else 'verified_native_inputs' if bound else 'unverified')
    if bound and 'geometry_status' in result:
        result['status'] = result['geometry_status']
        result['pass'] = bool(result.get('geometry_pass'))
        result.pop('applicability_reason', None)
    if result.get("pdf_sha256") and not bound:
        result.setdefault("geometry_status", result.get("status"))
        result.setdefault("geometry_pass", result.get("pass"))
        result["status"] = "unverified_current_source"
        result["pass"] = False
        result["applicability_reason"] = "Selected PDF geometry is retained, but its binding to the current source build is not verified."
        if isolated_binding is not None:
            result['binding_reason'] = isolated_binding.get('reason') or 'isolated_build_pdf_identity_not_verified'
        elif native_binding is not None:
            result['binding_reason'] = native_binding.get('reason') or 'native_build_pdf_identity_not_verified'
    return result


def _current_task_objective(main: Path, root: Path, data_dir: Path) -> dict[str, Any]:
    if not within(data_dir, root):
        return {}
    candidates = [p for p in (data_dir / "task.json", data_dir / "state.json")
                  if p.is_file() and within(p.resolve(), root)]
    for path in sorted(candidates, key=lambda p: p.stat().st_mtime_ns, reverse=True):
        payload, status = load_report(path)
        if status != "available":
            continue
        try:
            if path.name == "state.json":
                state = validate_state(payload)
                task, task_main = state["task"], state["main_tex"]
            else:
                spec = TaskSpec.from_dict(payload)
                task, task_main = spec.to_dict(), spec.main_tex
            if isinstance(task_main, str) and (root / task_main).resolve() == main and within(main, root):
                return dict(task)
        except (ValueError, TypeError, OSError):
            continue
    return {}


def build_host_context(main: Path, *, root: Path | None = None, data_dir: Path | None = None,
                       pdf: Path | None = None, build_dir: Path | None = None, limit: int = 30,
                       target_pages: int | None = None, page_policy: str | None = None,
                       page_budget_scope: str | None = None) -> dict[str, Any]:
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
    build_dir = build_dir.resolve() if build_dir is not None else None
    selected_pdf = (pdf.resolve() if pdf is not None else
                    build_dir / 'build' / (main.stem + '.pdf') if build_dir is not None else
                    main.with_suffix('.pdf'))
    isolated_binding = (verify_isolated_build(main, root=root, pdf=selected_pdf, build_dir=build_dir)
                        if build_dir is not None else None)
    geometry = review_pdf_visibility(selected_pdf, root=root,
                              reviews_path=data_dir / 'pdf_visibility_reviews.json',
                              allow_external_pdf=pdf is not None or build_dir is not None)
    layout_profile = profile_pdf_layout(selected_pdf)
    native_binding = (verify_native_build(main, root=root, pdf=selected_pdf)
                      if isolated_binding is None else None)
    visibility = bind_visibility_to_current_source(geometry, freshness,
        isolated_binding=isolated_binding, native_binding=native_binding)
    layout_profile['applicable_to_current_source'] = (
        visibility.get('applicable_to_current_source')
        if layout_profile.get('pdf_sha256') and layout_profile['pdf_sha256'] == visibility.get('pdf_sha256')
        else None)
    task = _current_task_objective(main, root, data_dir) if target_pages is None else {}
    objective = evaluate_page_objective(
        target_pages=target_pages if target_pages is not None else task.get("target_pages"),
        page_policy=page_policy or task.get("page_policy") or "at_most",
        page_budget_scope=page_budget_scope or task.get("page_budget_scope"),
        page_count=layout_profile.get("page_count"), pdf_sha256=layout_profile.get("pdf_sha256"),
        evidence_status="current" if layout_profile.get("applicable_to_current_source") is True else "unknown",
        measurement_source="host_current_pdf")
    for item in compact:
        item["host_review"] = recommendation_review(
            evidence_status=freshness["vision_status"],
            target_status=item["source_anchor_status"])
    return {
        "schema_version": "1.0", "main": sources["main"],
        "host_guidance": host_guidance(),
        "layout_plan": joint_layout_plan(objective),
        "page_objective": objective,
        "project_root": str(root), "project_root_resolution": root_resolution,
        "selected_pdf": str(selected_pdf),
        "pdf_selection": "explicit" if pdf is not None else "isolated_build" if build_dir is not None else "source_neighbor",
        "isolated_build_binding": isolated_binding,
        "native_build_binding": native_binding,
        "reports": {"action_plan": plan_status, "visual": visual_status,
                    "full_action_plan": str(data_dir / "visual_action_plan.json"),
                    "full_visual_report": str(data_dir / "visual_signal_report.json")},
        "vision": vision, "vision_source": vision_source,
        "pdf_visibility": visibility,
        "pdf_layout_profile": layout_profile,
        "artifact_freshness": freshness,
        "source_navigation_coverage": sources["object_marker_coverage"],
        "finding_counts": {"total": len(compact), "shown": min(limit, len(compact)), "omitted": max(0, len(compact)-limit)},
        "findings": compact[:limit], "source_map": sources,
        "host_tasks": [
            "Inspect rendered pages, including pages without machine findings.",
            "Use pdf_layout_profile to locate small type, short terminal lines and sparse text tails for actual zoomed page/source review; its observations do not authorize rejection or acceptance.",
            "Preserve useful source/PDF checkpoints and compare the user's page objective, readability and source checks before final nomination; latest is not automatically best.",
            "Review painted-text bounds in pdf_visibility even when VLM reports no defects; intentional clipping needs current-PDF and inspected-page evidence.",
            "Review freshness blocking checks; stale or unknown vision is not a clean current PDF.",
            "Explicit external PDF geometry is observation only unless isolated_build_binding verifies the current inputs and selected PDF; this does not establish VLM freshness or actual page views.",
            "Unknown source binding permits inspection, comparison and native editing within the user's authorized scope; obtain a fresh build before certifying the edited source instead of treating uncertainty as an edit prohibition or automatic acceptance.",
            "Treat machine findings as hypotheses. Verify the current page and source; accept, adapt or reject the suggested repair.",
            "Use native host editing for independently located repairs, including combined or unlisted strategies. Optional batch statuses do not define the host's user-granted edit scope; no per-finding acknowledgement is required.",
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
    parser.add_argument("--pdf", help="Explicit PDF from an isolated build; selection alone does not establish current-source binding")
    parser.add_argument("--build-dir", help="Explicit isolated_build directory to verify current inputs and PDF hashes; without --pdf selects build/<main-stem>.pdf")
    parser.add_argument("--target-pages", type=int)
    parser.add_argument("--page-policy", choices=["at_most", "exact"])
    parser.add_argument("--page-budget-scope", choices=["main_body", "with_refs", "with_appendix"])
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--output", help="New report path; existing files, source, PDFs and receipts are never overwritten")
    args = parser.parse_args()
    dest = Path(args.output) if args.output else None
    if dest is not None and (dest.exists() or dest.is_symlink()):
        parser.error(f"Output path already exists: {dest}. Choose a new report output path.")
    # Missing artifacts remain reserved inputs/evidence, not vacant report
    # paths. Never create JSON at the selected PDF or a known build receipt.
    main_path = Path(args.main).resolve()
    protected_paths = {main_path, main_path.with_suffix('.pdf'), main_path.with_suffix('.log')}
    if args.pdf:
        protected_paths.add(Path(args.pdf).resolve())
    if args.build_dir:
        build_path = Path(args.build_dir).resolve()
        protected_paths.update((build_path / name).resolve() for name in (
            'build_record.json', 'stdout.txt', 'stderr.txt',
            'build/' + main_path.stem + '.pdf', 'build/' + main_path.stem + '.log'))
    if dest is not None and dest.resolve() in protected_paths:
        parser.error(f"Output path is reserved for source or a build artifact: {dest}. "
                     "Choose a new report output path.")
    payload = build_host_context(Path(args.main), root=Path(args.root) if args.root else None,
                                 data_dir=Path(args.data_dir) if args.data_dir else None,
                                 pdf=Path(args.pdf) if args.pdf else None, limit=args.limit,
                                 build_dir=Path(args.build_dir) if args.build_dir else None,
                                 target_pages=args.target_pages, page_policy=args.page_policy,
                                 page_budget_scope=args.page_budget_scope)
    if dest is not None and args.build_dir and any(
            is_included_input_path(dest.resolve(), root=tree) for tree in
            (Path(payload['project_root']), Path(args.build_dir) / 'source')):
        parser.error(f"Report output would add an isolated-build input: {dest}. "
                     "Choose a new report under .paperfit/reports/ or outside the project and copied source directories.")
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if dest is not None:
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            # The initial check gives an early, useful error; exclusive creation
            # also protects source and receipts if a path appears during work.
            with dest.open('x', encoding='utf-8') as stream:
                stream.write(text)
        except FileExistsError:
            parser.error(f"Output path already exists: {dest}. Choose a new report output path.")
        except OSError as error:
            parser.error(f"Cannot create new report at {dest}: {error}")
        print(json.dumps({"output": str(dest), "finding_counts": payload["finding_counts"],
                          "source_objects": len(payload["source_map"]["objects"]),
                          "source_warnings": len(payload["source_map"]["warnings"])}))
    else:
        print_host_json(payload)
if __name__ == "__main__":
    main()
