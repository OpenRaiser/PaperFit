"""Page objective evidence shared by host tools and runtime gates.

A whole PDF only measures with_appendix. Other scopes need an independently
established boundary; neither a vision page count nor a heading guess is one.
"""
from __future__ import annotations
from typing import Any, Dict, Optional

PAGE_POLICIES = {"at_most", "exact"}
PAGE_SCOPES = {"main_body", "with_refs", "with_appendix"}

def page_distance(page_count: Optional[int], target_pages: Optional[int], page_policy: str = "at_most") -> Optional[int]:
    if page_policy not in PAGE_POLICIES:
        raise ValueError("page_policy must be at_most or exact")
    if type(page_count) is not int or page_count < 1 or type(target_pages) is not int or target_pages < 1:
        return None
    return abs(page_count-target_pages) if page_policy == "exact" else max(0, page_count-target_pages)

def evaluate_page_objective(*, target_pages: Optional[int] = None,
                            page_policy: str = "at_most", page_budget_scope: Optional[str] = None,
                            page_count: Optional[int] = None, measured_scope: str = "with_appendix",
                            evidence_status: str = "unknown", pdf_sha256: Optional[str] = None,
                            measurement_source: Optional[str] = None) -> Dict[str, Any]:
    if page_policy not in PAGE_POLICIES:
        raise ValueError("page_policy must be at_most or exact")
    scope = page_budget_scope or "with_appendix"
    if scope not in PAGE_SCOPES or measured_scope not in PAGE_SCOPES:
        raise ValueError("unsupported page budget scope")
    if target_pages is not None and (type(target_pages) is not int or target_pages < 1):
        raise ValueError("target_pages must be a positive integer")
    count = page_count if type(page_count) is int and page_count > 0 else None
    report = {"schema_version": "1.0", "target_pages": target_pages, "page_policy": page_policy,
              "page_budget_scope": scope, "measured_scope": measured_scope,
              "measured_pages": count, "measurement_source": measurement_source,
              "pdf_sha256": pdf_sha256, "evidence_status": evidence_status,
              "status": "unknown", "target_met": None, "distance": None,
              "direction": None, "reason": None}
    if target_pages is None:
        report.update(status="not_requested", reason="no_page_target")
    elif scope != measured_scope:
        report["reason"] = "scope_boundary_unverified"
    elif count is None:
        report["reason"] = "page_count_unavailable"
    elif not (isinstance(pdf_sha256, str) and len(pdf_sha256) == 64 and
              all(c in "0123456789abcdefABCDEF" for c in pdf_sha256)):
        report["reason"] = "pdf_identity_unavailable"
    elif evidence_status != "current":
        report["reason"] = "measurement_not_bound_to_current_source"
    else:
        distance = page_distance(count, target_pages, page_policy)
        report.update(status="met" if distance == 0 else "unmet", target_met=distance == 0,
                      distance=distance, direction="over_target" if count > target_pages else
                      "under_target" if count < target_pages else "on_target",
                      reason="measured_current_pdf")
    return report

def page_objective_from_manifest(task: Dict[str, Any], manifest: Dict[str, Any]) -> Dict[str, Any]:
    pdf = (manifest.get("artifacts") or {}).get("pdf") or {}
    binding = manifest.get("native_build_binding") or {}
    current = ((manifest.get("freshness") or {}).get("status") == "pass"
               and binding.get("status") == "current"
               and binding.get("pdf_sha256") == pdf.get("sha256"))
    sha = pdf.get("sha256")
    identifiable = (isinstance(sha, str) and len(sha) == 64 and
                    all(c in "0123456789abcdefABCDEF" for c in sha))
    return evaluate_page_objective(target_pages=task.get("target_pages"),
        page_policy=task.get("page_policy") or "at_most", page_budget_scope=task.get("page_budget_scope"),
        page_count=pdf.get("page_count") if pdf.get("page_count_status") == "available" else None,
        evidence_status="current" if current and identifiable else "unknown",
        pdf_sha256=sha, measurement_source="runtime_pdf_manifest")
