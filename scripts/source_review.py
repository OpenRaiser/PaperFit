"""Explicit, current-build host adjudication for narrowly classified uncertainty.

No edits, builds, automatic approval, or escape hatch for scientific changes.
Raw automated reports remain available when a host-reviewed channel passes.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any
try:
 from scientific_integrity import scientific_payload_diff, active_source
 from layout_prose_integrity import compare_layout_prose
 from layout_policy import collect_layout_review_binding, _relative_path
except ImportError:
 from .scientific_integrity import scientific_payload_diff, active_source
 from .layout_prose_integrity import compare_layout_prose
 from .layout_policy import collect_layout_review_binding, _relative_path

SCHEMA="paperfit.source_uncertainty_review.v1"
def text_hash(text:str)->str:
 return hashlib.sha256(text.encode("utf-8",errors="surrogateescape")).hexdigest()

def empty_table_skeleton_uncertainty(original:str,repaired:str)->dict[str,Any]:
 """Only an unmatched empty tabular immediately followed by stray braces.

 Removing any cell, caption, asset, text or other command cannot use this path.
 Even an empty matched table is not covered. Original comments stay inactive.
 """
 old=active_source(original);new=active_source(repaired)
 normalized=lambda s:re.sub(r"\s+"," ",s).strip()
 result={"status":"not_applicable","checked":True}
 try:
  from scientific_integrity import _macro_definitions
 except ImportError:
  from .scientific_integrity import _macro_definitions
 definitions=_macro_definitions(old)+_macro_definitions(new)
 defined={str(r[1] if r[0] in {'def','gdef','edef','xdef','let'} else r[2]).lstrip('\\') for r in definitions}
 if defined&{'begin','end','tabular'}:
  return result
 pattern=re.compile(r"\\begin\s*\{tabular\}\s*\{[lcr| ]+\}\s*\}{1,4}")
 for match in pattern.finditer(old):
  # No matching active end can belong to this skeleton.
  next_begin=old.find(r"\begin{tabular}",match.end())
  end=old.find(r"\end{tabular}",match.end())
  if end>=0 and (next_begin<0 or end<next_begin):continue
  residual=old[:match.start()]+" "+old[match.end():]
  if scientific_payload_diff(original,repaired).get("status")!="pass":continue
  if scientific_payload_diff(residual,new).get("status")!="pass":continue
  try:
   from scientific_integrity import _opaque_source_invocations
  except ImportError:
   from .scientific_integrity import _opaque_source_invocations
  if _opaque_source_invocations(old)!=_opaque_source_invocations(residual):continue
  if compare_layout_prose(residual,new).get("status")!="pass":continue
  return {"checked":True,"status":"pending","source_start":match.start(),"source_end":match.end(),
          "spelling":match[0],"reason":"Only an unmatched empty table opener and immediately adjacent stray closing braces became inactive; no active table cells, caption, text or asset were removed."}
 return result

def reviewable_uncertainty(original:str,repaired:str)->dict[str,Any]:
 science=scientific_payload_diff(original,repaired);prose=compare_layout_prose(original,repaired)
 kinds=[]
 if science.get("assessment",{}).get("classification")=="manual_review" and prose.get("status")=="pass":
  for name in ("layout_argument_uncertainty","reference_anchor_uncertainty","reference_font_hook_uncertainty","combined_layout_uncertainty","template_parameter_uncertainty"):
   if science.get(name,{}).get("status")=="pending":kinds.append(name)
 hook=science.get("reference_font_hook_uncertainty",{})
 if hook.get("status")=="pending" and "reference_font_hook_uncertainty" not in kinds and (science.get("status")=="pass" or science.get("assessment",{}).get("classification")=="manual_review"):
  kinds.append("reference_font_hook_uncertainty")
 skeleton=empty_table_skeleton_uncertainty(original,repaired)
 if skeleton["status"]=="pending":kinds.append("empty_table_skeleton_uncertainty")
 return {"eligible":bool(kinds),"kinds":kinds,"scientific_payload":science,
         "layout_prose":prose,"empty_table_skeleton_uncertainty":skeleton}

def build_source_review_request(original:str,repaired:str,*,project_root:Path,file:str,
                                main_tex:str,page_images_dir:str,
                                compile_journal:str="data/compile/journal.jsonl")->dict[str,Any]:
 root=Path(project_root).resolve();file=_relative_path(root,file)
 context={"main_tex":main_tex,"page_images_dir":page_images_dir,"compile_journal":compile_journal}
 live=collect_layout_review_binding(root,**context)
 current=(root/file).read_text(encoding="utf-8",errors="surrogateescape")
 classification=reviewable_uncertainty(original,repaired)
 if current!=repaired:raise ValueError("candidate text does not match current source")
 template_evidence=[]
 if "template_parameter_uncertainty" in classification["kinds"] and live.get("status")=="available":
  try:
   from template_parameter_review import suggest_evidence
  except ImportError:
   from .template_parameter_review import suggest_evidence
  imported={item["path"]:item["sha256"] for item in live["binding"]["imported_inputs"]["files"]}
  template_evidence=suggest_evidence(root,imported,classification["scientific_payload"]["template_parameter_uncertainty"])
 return {"schema_version":SCHEMA,"file":file,"context":context,
         "original_text_sha256":text_hash(original),"candidate_text_sha256":text_hash(repaired),
         "classification":classification,"binding_status":live["status"],
         "blocking_checks":live["blocking_checks"],"binding":live["binding"],
         "approved":False,"source_reviewed":False,"all_pages_inspected":False,
         "content_preservation_confirmed":False,"template_checked":False,
         "intended_object_checked":False,"reason":"","declaration_evidence":[],
         "template_parameter_evidence":template_evidence,
         "scope_note":"Explicit host adjudication of these classified differences only. Raw automated failure remains. This is neither layout-policy approval nor final manuscript acceptance."}

def load_source_review(path:Path)->dict[str,Any]:
 if not path.is_file() or path.stat().st_size>32*1024*1024:raise ValueError("source review unavailable")
 def unique(items):
  value={}
  for key,item in items:
   if key in value:raise ValueError("duplicate source-review JSON key")
   value[key]=item
  return value
 payload=json.loads(path.read_text(encoding="utf-8-sig"),object_pairs_hook=unique)
 if not isinstance(payload,dict) or payload.get("schema_version")!=SCHEMA:raise ValueError("unsupported source-review schema")
 return payload

def validate_source_review(original:str,repaired:str,*,project_root:Path|None,file:str,
                           review:dict[str,Any]|None)->dict[str,Any]:
 result={"checked":True,"status":"not_used","verification_method":"explicit_host_review"}
 if not review:return result
 result["status"]="manual_review"
 try:
  if project_root is None or not isinstance(review,dict) or review.get("schema_version")!=SCHEMA:raise ValueError("source-review root/schema unavailable")
  root=Path(project_root).resolve();relative=_relative_path(root,file)
  context=review.get("context")
  if not isinstance(context,dict) or set(context)!={"main_tex","page_images_dir","compile_journal"}:raise ValueError("source-review context unavailable")
  if review.get("file")!=relative or review.get("original_text_sha256")!=text_hash(original) or review.get("candidate_text_sha256")!=text_hash(repaired):raise ValueError("review is for different baseline/candidate")
  if (root/relative).read_text(encoding="utf-8",errors="surrogateescape")!=repaired:raise ValueError("candidate differs from current file")
  classification=reviewable_uncertainty(original,repaired)
  if not classification["eligible"] or classification!=review.get("classification"):raise ValueError("difference is not classified reviewable uncertainty")
  live=collect_layout_review_binding(root,**context)
  if live["status"]!="available" or not live["binding"] or live["binding"]!=review.get("binding"):raise ValueError("current source, inputs, PDF, pages or latest compile changed/unavailable")
  for flag in ("approved","source_reviewed","all_pages_inspected","content_preservation_confirmed","template_checked"):
   if review.get(flag) is not True:raise ValueError("explicit host confirmation missing: "+flag)
  if not isinstance(review.get("reason"),str) or not review["reason"].strip():raise ValueError("host explanation missing")
  families=set(classification["kinds"])
  if "combined_layout_uncertainty" in families:
   families.update(classification["scientific_payload"].get("combined_layout_uncertainty",{}).get("families",[]))
  if "template_parameter_uncertainty" in families:
   try:
    from template_parameter_review import verify_template_glue_evidence
   except ImportError:
    from .template_parameter_review import verify_template_glue_evidence
   template_class=classification["scientific_payload"]["template_parameter_uncertainty"]
   imported={item["path"]:item["sha256"] for item in live["binding"]["imported_inputs"]["files"]}
   verify_template_glue_evidence(root,imported,template_class,review.get("template_parameter_evidence"))
   families.update(template_class.get("additional_families",[]))
  if "reference_anchor_uncertainty" in families and review.get("intended_object_checked") is not True:raise ValueError("reference target review missing")
  if "layout_argument_uncertainty" in families:
   evidence=review.get("declaration_evidence")
   if not isinstance(evidence,list) or not evidence:raise ValueError("actual local declaration evidence required")
   imported={item["path"]:item["sha256"] for item in live["binding"]["imported_inputs"]["files"]}
   for item in evidence:
    path=_relative_path(root,item.get("path"))
    target=root/path
    if imported.get(path)!=item.get("sha256") or hashlib.sha256(target.read_bytes()).hexdigest()!=item.get("sha256"):raise ValueError("declaration evidence is not a current compiled input")
    if not isinstance(item.get("excerpt"),str) or not item["excerpt"].strip() or item["excerpt"] not in target.read_text(encoding="utf-8",errors="surrogateescape"):raise ValueError("declaration excerpt unavailable")
  return {**result,"status":"verified","kinds":classification["kinds"],"reason":review["reason"],"binding":live["binding"]}
 except (OSError,ValueError,KeyError,TypeError,UnicodeError) as exc:
  result["reason"]=str(exc);return result

def apply_source_review(original:str,repaired:str,*,project_root:Path|None,file:str,
                        review:dict[str,Any]|None,scientific:dict[str,Any],prose:dict[str,Any]):
 decision=validate_source_review(original,repaired,project_root=project_root,file=file,review=review)
 if decision["status"]!="verified":
  pending_names=("layout_argument_uncertainty","reference_anchor_uncertainty","reference_font_hook_uncertainty","combined_layout_uncertainty","template_parameter_uncertainty")
  if prose.get("status")=="pass" and not any(scientific.get(name,{}).get("status")=="pending" for name in pending_names):
   return scientific,prose,decision
  classified=reviewable_uncertainty(original,repaired)
  if not classified["eligible"]:return scientific,prose,decision
  decision={**decision,"status":"manual_review","kinds":classified["kinds"],"requires_host_review":True}
  # This is unresolved, not a successful automated check or proven damage.
  # Retain full raw reports; confirmed payload losses never reach this branch.
  if scientific.get("status")!="pass" or "reference_font_hook_uncertainty" in classified["kinds"]:
   scientific={**scientific,"automated_report":scientific,"automated_status":scientific["status"],
               "status":"manual_review","verification_method":"pending_host_review"}
  if prose.get("status")!="pass":
   prose={**prose,"automated_report":prose,"automated_status":prose["status"],
          "status":"manual_review","verification_method":"pending_host_review"}
  return scientific,prose,decision
 kinds=decision["kinds"]
 if scientific.get("status")!="pass" and any(name!="empty_table_skeleton_uncertainty" for name in kinds):
  scientific={**scientific,"automated_report":scientific,"automated_status":scientific["status"],
              "status":"pass","regression_reasons":[],"verification_method":"explicit_host_review",
              "host_review":decision}
 if "empty_table_skeleton_uncertainty" in kinds or "reference_font_hook_uncertainty" in kinds:
  prose={**prose,"automated_report":prose,"automated_status":prose["status"],
         "status":"pass","verification_method":"explicit_host_review","host_review":decision}
 return scientific,prose,decision

def main(argv=None)->int:
 parser=argparse.ArgumentParser(description="Read-only host review of classified source uncertainty; never bypass confirmed loss.")
 parser.add_argument("mode",choices=("request","validate"))
 parser.add_argument("--root",type=Path,required=True);parser.add_argument("--file",required=True)
 parser.add_argument("--main-tex",required=True);parser.add_argument("--baseline-tex",required=True)
 parser.add_argument("--pages-dir",default="data/pages");parser.add_argument("--compile-journal",default="data/compile/journal.jsonl")
 parser.add_argument("--review-json")
 args=parser.parse_args(argv);root=args.root.resolve()
 try:
  file=_relative_path(root,args.file)
  original=(root/_relative_path(root,args.baseline_tex)).read_text(encoding="utf-8",errors="surrogateescape")
  repaired=(root/file).read_text(encoding="utf-8",errors="surrogateescape")
  if args.mode=="request":
   result=build_source_review_request(original,repaired,project_root=root,file=file,main_tex=args.main_tex,page_images_dir=args.pages_dir,compile_journal=args.compile_journal)
   code=3
  else:
   if not args.review_json:raise ValueError("--review-json required")
   review=load_source_review(root/_relative_path(root,args.review_json))
   result=validate_source_review(original,repaired,project_root=root,file=file,review=review)
   code=0 if result["status"]=="verified" else 3
 except (OSError,ValueError,TypeError,UnicodeError) as exc:
  result={"checked":False,"status":"manual_review","reason":str(exc)};code=3
 print(json.dumps(result,ensure_ascii=False,indent=2));return code
if __name__=="__main__":raise SystemExit(main())
