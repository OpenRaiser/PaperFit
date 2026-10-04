#!/usr/bin/env python3
"""
证据收集器

汇总多模态证据链，供 quality-gatekeeper-agent 验收使用。
收集内容包括：编译日志摘要、页图清单、代码变更 diff、诊断报告路径等。

用法:
    python evidence_collector.py --round <N> --output <dir>
"""

import json
import shutil
import argparse
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional


class EvidenceCollector:
    """证据收集器"""

    def __init__(self, project_root: Path = None):
        self.project_root = project_root or Path.cwd()
        self.data_dir = self.project_root / "data"
        self.evidence_dir = self.data_dir / "evidence"

    def collect(
        self,
        round_num: int,
        log_file: Optional[Path] = None,
        pages_dir: Optional[Path] = None,
        diagnostic_report: Optional[Path] = None,
        repair_plan_report: Optional[Path] = None,
        semantic_report: Optional[Path] = None,
        modified_files: Optional[List[Path]] = None,
        state_file: Optional[Path] = None
    ) -> Dict[str, Any]:
        """收集指定轮次的所有证据并打包"""
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        round_evidence_dir = self.evidence_dir / f"round_{round_num:02d}"
        round_evidence_dir.mkdir(exist_ok=True)

        evidence = {
            "round": round_num,
            "timestamp": datetime.now().isoformat(),
            "files": {}
        }

        # 1. 复制编译日志
        if log_file and log_file.exists():
            dest = round_evidence_dir / "compile.log"
            shutil.copy2(log_file, dest)
            evidence["files"]["log"] = str(dest)
            evidence["log_summary"] = self._summarize_log(log_file)

        # 2. 复制页图目录
        if pages_dir and pages_dir.exists():
            dest_pages = round_evidence_dir / "pages"
            if dest_pages.exists():
                shutil.rmtree(dest_pages)
            shutil.copytree(pages_dir, dest_pages)
            evidence["files"]["pages_dir"] = str(dest_pages)
            evidence["page_count"] = len(list(dest_pages.glob("page_*.png")))

        # 3. 复制诊断报告
        if diagnostic_report and diagnostic_report.exists():
            dest = round_evidence_dir / diagnostic_report.name
            shutil.copy2(diagnostic_report, dest)
            evidence["files"]["diagnostic_report"] = str(dest)
            evidence["visual_signal_summary"] = self._summarize_visual_report(diagnostic_report)

        if repair_plan_report and repair_plan_report.exists():
            dest = round_evidence_dir / repair_plan_report.name
            shutil.copy2(repair_plan_report, dest)
            evidence["files"]["repair_plan_report"] = str(dest)
            evidence["repair_plan_summary"] = self._summarize_repair_plan(repair_plan_report)

        # 3.1 复制语义补丁报告
        if semantic_report and semantic_report.exists():
            dest = round_evidence_dir / semantic_report.name
            shutil.copy2(semantic_report, dest)
            evidence["files"]["semantic_patch_report"] = str(dest)

        # 4. 记录修改的文件及 diff
        if modified_files:
            evidence["modified_files"] = [str(f) for f in modified_files]
            evidence["diffs"] = self._collect_diffs(modified_files, round_evidence_dir)

        # 5. 复制状态文件快照
        if state_file and state_file.exists():
            dest = round_evidence_dir / "state_snapshot.json"
            shutil.copy2(state_file, dest)
            evidence["files"]["state_snapshot"] = str(dest)

        # 6. 生成证据清单
        manifest_path = round_evidence_dir / "manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(evidence, f, indent=2, ensure_ascii=False)

        return evidence

    def _summarize_visual_report(self, report_path: Path) -> Dict[str, Any]:
        summary: Dict[str, Any] = {
            "priority_pages": [],
            "priority_objects": [],
            "cross_page_hints": [],
            "crossref_hints": [],
        }
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            routing_hints = report.get("routing_hints") or {}
            summary["priority_pages"] = list(routing_hints.get("priority_pages") or [])[:10]
            for item in (report.get("priority_objects") or [])[:5]:
                summary["priority_objects"].append(
                    {
                        "page": int(item.get("page") or 0),
                        "object_kind": item.get("object_kind"),
                        "reason": item.get("reason"),
                        "priority_score": item.get("priority_score"),
                    }
                )
            for item in (report.get("cross_page_hints") or [])[:5]:
                summary["cross_page_hints"].append(
                    {
                        "taxonomy_defect_id": item.get("taxonomy_defect_id"),
                        "pages": item.get("pages") or [],
                    }
                )
            for item in (report.get("crossref_hints") or [])[:5]:
                summary["crossref_hints"].append(
                    {
                        "label": item.get("label"),
                        "float_type": item.get("float_type"),
                        "severity": item.get("severity"),
                    }
                )
        except Exception:
            pass
        return summary

    def _summarize_repair_plan(self, report_path: Path) -> Dict[str, Any]:
        summary: Dict[str, Any] = {
            "total_candidates": 0,
            "top_candidates": [],
        }
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            summary["total_candidates"] = int(((report.get("summary") or {}).get("total_candidates")) or 0)
            for item in (report.get("candidates") or [])[:5]:
                target = item.get("target") or {}
                summary["top_candidates"].append(
                    {
                        "candidate_type": item.get("candidate_type"),
                        "page": item.get("page"),
                        "label": target.get("label"),
                        "object_kind": target.get("object_kind"),
                        "proposed_action": item.get("proposed_action"),
                        "priority_score": item.get("priority_score"),
                    }
                )
        except Exception:
            pass
        return summary

    def _summarize_log(self, log_path: Path) -> Dict[str, int]:
        """快速提取日志摘要"""
        summary = {
            "errors": 0,
            "warnings": 0,
            "overfull_hbox": 0,
            "underfull_hbox": 0
        }
        try:
            content = log_path.read_text(encoding="utf-8", errors="ignore")
            summary["errors"] = content.count("!")
            summary["warnings"] = content.count("Warning:")
            summary["overfull_hbox"] = content.count("Overfull \\hbox")
            summary["underfull_hbox"] = content.count("Underfull \\hbox")
        except Exception:
            pass
        return summary

    def _collect_diffs(self, modified_files: List[Path], dest_dir: Path) -> Dict[str, str]:
        """收集文件的 git diff 或简单前后对比（需要备份支持）"""
        diffs = {}
        backups_dir = self.data_dir / "backups"

        for file_path in modified_files:
            # 尝试找到最近的备份
            backup_pattern = f"{file_path.name}_*"
            backups = sorted(backups_dir.glob(backup_pattern), reverse=True)
            if backups:
                # 生成 diff
                try:
                    result = subprocess.run(
                        ["diff", "-u", str(backups[0]), str(file_path)],
                        capture_output=True,
                        text=True
                    )
                    diffs[str(file_path)] = result.stdout
                except Exception:
                    diffs[str(file_path)] = f"# Unable to generate diff for {file_path}"
            else:
                diffs[str(file_path)] = f"# No backup found for {file_path}"

        # 保存 diff 文件
        diff_file = dest_dir / "changes.diff"
        with open(diff_file, "w", encoding="utf-8") as f:
            for path, diff_content in diffs.items():
                f.write(f"--- {path}\n")
                f.write(diff_content)
                f.write("\n\n")

        return diffs


def main():
    parser = argparse.ArgumentParser(description="收集 PaperFit 迭代证据")
    parser.add_argument("--round", "-r", type=int, required=True, help="迭代轮次")
    parser.add_argument("--log", help="编译日志路径")
    parser.add_argument("--pages", help="页图目录")
    parser.add_argument("--report", help="诊断报告路径")
    parser.add_argument("--repair-plan-report", help="修复计划路径")
    parser.add_argument("--semantic-report", help="语义补丁报告路径")
    parser.add_argument("--modified", nargs="+", help="修改的文件列表")
    parser.add_argument("--state", help="状态文件路径")
    parser.add_argument("--output", "-o", help="输出目录", default="data/evidence")

    args = parser.parse_args()

    collector = EvidenceCollector()
    evidence = collector.collect(
        round_num=args.round,
        log_file=Path(args.log) if args.log else None,
        pages_dir=Path(args.pages) if args.pages else None,
        diagnostic_report=Path(args.report) if args.report else None,
        repair_plan_report=Path(args.repair_plan_report) if args.repair_plan_report else None,
        semantic_report=Path(args.semantic_report) if args.semantic_report else None,
        modified_files=[Path(f) for f in args.modified] if args.modified else None,
        state_file=Path(args.state) if args.state else None
    )

    print(json.dumps(evidence, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
