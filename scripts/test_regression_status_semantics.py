"""状态语义回归：noop 修复不许混入 layout_evaluable；注入来源模式显式化。"""

from __future__ import annotations

import json
import sys
import tarfile
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import Image


SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent  # PaperFit 仓库根（scripts/ 的上一级）
for entry in (str(SCRIPTS_DIR), str(REPO_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import regression.mvp_runner as mvp_runner  # noqa: E402
from regression.mvp_runner import (  # noqa: E402
    SOURCE_MODE_INJECT_FROM_RAW,
    SourceModeMismatchError,
    detect_raw_is_clean,
    run_mvp,
)
from regression.run_case import (  # noqa: E402
    _derive_case_states,
    default_paperfit_runner,
    run_paperfit_case,
)




def _payload(*, status="continue", failure=None, repair_summary=None, executor=None, totals=None):
    runtime_actions = {
        "initial_observe": {
            "compile": {"success": True, "case_status": "layout_evaluable"},
            "render": {"success": True},
        }
    }
    if executor is not None:
        runtime_actions["repair_plan_executor"] = executor
    payload = {
        "status": status,
        "runtime_actions": runtime_actions,
        "failure": failure,
    }
    if repair_summary is not None:
        payload["repair_execution_summary"] = repair_summary
    if totals is not None:
        payload["round_execution_records"] = totals["records"]
        payload["execution_totals"] = totals["totals"]
    return payload


NOOP_SUMMARY = {"status": "noop", "applied_count": 0, "accepted_count": 0, "rejected_count": 4, "rolled_back_count": 0}
# 与真实 payload 同构：ACMmm noop 案例里唯一的执行器痕迹嵌在 runtime_actions
NOOP_EXECUTOR = {"success": True, "status": "noop", "applied_count": 0}


class DeriveStatusNoEffectTest(unittest.TestCase):
    """noop（applied=0）不许进 layout_evaluable。"""

    def test_noop_summary_with_renderable_pdf_is_no_effect(self):
        states = _derive_case_states(_payload(repair_summary=NOOP_SUMMARY))
        self.assertEqual(states["status"], "no_effect")
        self.assertEqual(states["run_status"], "completed")
        self.assertEqual(states["candidate_render_status"], "rendered")  # PDF 确实渲出来了
        self.assertEqual(states["repair_effect_status"], "no_effect")

    def test_noop_executor_action_fallback_is_no_effect(self):
        # 旧 run_result.json（顶层无 repair_execution_summary）也要判出来
        states = _derive_case_states(_payload(executor=NOOP_EXECUTOR))
        self.assertEqual(states["status"], "no_effect")
        self.assertEqual(states["repair_effect_status"], "no_effect")

    def test_round_records_zero_applied_is_no_effect(self):
        states = _derive_case_states(
            _payload(
                totals={
                    "records": [{"round": 1, "applied": 0, "status": "noop"}],
                    "totals": {"applied": 0, "accepted": 0},
                }
            )
        )
        self.assertEqual(states["status"], "no_effect")

    def test_round_records_multi_round_applied_total_wins(self):
        # round1 修了 2 处、round2 noop：总计 applied=2 → accepted，不是 no_effect
        states = _derive_case_states(
            _payload(
                totals={
                    "records": [
                        {"round": 1, "applied": 2, "status": "partial"},
                        {"round": 2, "applied": 0, "status": "noop"},
                    ],
                    "totals": {"applied": 2, "accepted": 2},
                }
            )
        )
        self.assertEqual(states["status"], "layout_evaluable")
        self.assertEqual(states["repair_effect_status"], "accepted")

    def test_guard_rolled_back_survives_even_with_renderable_pdf_and_noop_summary(self):
        # guard 优先级最高：即使 PDF 可渲染、即使摘要 noop，回滚就是回滚
        states = _derive_case_states(
            _payload(
                status="blocked",
                failure={"failure_type": "post_repair_hard_guard_failed", "reason": "BLOCKED"},
                repair_summary=NOOP_SUMMARY,
            )
        )
        self.assertEqual(states["status"], "guard_rolled_back")
        self.assertEqual(states["repair_effect_status"], "guard_rolled_back")
        self.assertEqual(states["candidate_render_status"], "rendered")

    def test_approval_required_never_layout_evaluable(self):
        states = _derive_case_states(
            _payload(
                status="approval_required",
                failure={"failure_type": "approval_required", "reason": "needs human"},
                repair_summary={"status": "noop", "applied_count": 0},
            )
        )
        self.assertEqual(states["status"], "approval_required")
        self.assertEqual(states["repair_effect_status"], "approval_required")
        self.assertNotEqual(states["status"], "layout_evaluable")

    def test_blocked_never_layout_evaluable(self):
        states = _derive_case_states(
            _payload(status="blocked", failure={"failure_type": "remaining_defects", "reason": "BLOCKED"})
        )
        self.assertEqual(states["status"], "blocked")
        self.assertEqual(states["repair_effect_status"], "blocked")

    def test_applied_repairs_remain_layout_evaluable(self):
        states = _derive_case_states(
            _payload(repair_summary={"status": "partial", "applied_count": 2, "accepted_count": 1})
        )
        self.assertEqual(states["status"], "layout_evaluable")
        self.assertEqual(states["repair_effect_status"], "accepted")


class DeriveStatusViaRunPaperfitCaseTest(unittest.TestCase):
    def _b0(self, root: Path) -> tuple[Path, str]:
        project = root / "b0_project"
        project.mkdir(parents=True)
        (project / "paper.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\nplain body with enough words here\n\\end{document}\n",
            encoding="utf-8",
        )
        return project, "paper.tex"

    def test_noop_case_records_no_effect_with_reason(self):
        def noop_runner(project, task_path, output_path):
            payload = _payload(repair_summary=NOOP_SUMMARY)
            output_path.write_text(json.dumps(payload), encoding="utf-8")
            return payload

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            b0, main = self._b0(root)
            result = run_paperfit_case(
                b0_project=b0,
                work_root=root / "runs",
                case_id="example-paper-06",
                paper_id="ACMmm",
                main_tex_relpath=main,
                runner=noop_runner,
            )
        self.assertEqual(result.status, "no_effect")
        self.assertEqual(result.run_status, "completed")
        self.assertEqual(result.candidate_render_status, "rendered")
        self.assertEqual(result.repair_effect_status, "no_effect")
        self.assertTrue(any("repair_no_effect" in reason for reason in result.reasons))






FAKE_ORCHESTRATOR = r"""
import json
import sys
from pathlib import Path

# 调用形态与真 orchestrator 相同：run-task <task.json> --output <run_result.json>
args = sys.argv[1:]
output_path = Path(args[args.index("--output") + 1])
data_dir = Path("data")
data_dir.mkdir(exist_ok=True)

mode = "ok"
mode_file = Path("fake_mode.txt")
if mode_file.is_file():
    mode = mode_file.read_text(encoding="utf-8").strip() or "ok"

if mode == "crash":
    sys.stdout.buffer.write(b"\x81\x8b\xff non-utf8 stdout")
    sys.stdout.buffer.flush()
    sys.stderr.write("orchestrator exploded")
    sys.stderr.flush()
    sys.exit(3)

payload = {
    "status": "continue",
    "runtime_actions": {
        "initial_observe": {
            "compile": {"success": True, "case_status": "layout_evaluable"},
            "render": {"success": True},
        },
        "repair_plan_executor": {"success": True, "status": "noop", "applied_count": 0},
    },
    "failure": {"failure_type": "remaining_defects", "reason": "CONTINUE"},
}
output_path.write_text(json.dumps(payload), encoding="utf-8")
# run_result.json 顶层不带 repair_execution_summary：真实 orchestrator 就是这样，
# noop 判定必须靠 runner 补读这份执行报告
(data_dir / "repair_execution_report.json").write_text(
    json.dumps({"schema_version": "1.0", "status": "noop", "applied_count": 0,
                "accepted_count": 0, "rejected_count": 4, "rolled_back_count": 0}),
    encoding="utf-8",
)
pages = data_dir / "pages"
pages.mkdir(exist_ok=True)
from PIL import Image
Image.new("RGB", (40, 50), "white").save(pages / "page_001.png")
"""


class DefaultRunnerProcessAuditTest(unittest.TestCase):
    """runner 必须落进程审计信息，且退出码 0 也读 execution report 判 no_effect。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.orchestrator = self.root / "fake_orchestrator.py"
        self.orchestrator.write_text(FAKE_ORCHESTRATOR, encoding="utf-8")
        self.project = self.root / "project"
        (self.project / "data").mkdir(parents=True)
        self.task = self.project / "data" / "task.json"
        self.task.write_text("{}", encoding="utf-8")
        self.output = self.project / "data" / "run_result.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _runner(self):
        return default_paperfit_runner(self.project, self.orchestrator, sys.executable)

    def test_exit_zero_reads_execution_report_and_classifies_no_effect(self):
        payload = self._runner()(self.project, self.task, self.output)
        self.assertEqual(payload["repair_execution_summary"]["status"], "noop")
        self.assertEqual(payload["repair_execution_summary"]["applied_count"], 0)
        process = payload["paperfit_process"]
        self.assertEqual(process["command"][0], sys.executable)
        self.assertEqual(process["cwd"], str(self.project))
        self.assertEqual(process["returncode"], 0)
        self.assertIsInstance(process["duration_sec"], float)
        self.assertGreaterEqual(process["duration_sec"], 0.0)
        # 无输出写空串，不许 None
        self.assertEqual(process["stdout"], "")
        self.assertEqual(process["stderr"], "")
        # 端到端：该 payload 判 no_effect，不再混入 layout_evaluable
        states = _derive_case_states(payload)
        self.assertEqual(states["status"], "no_effect")

    def test_nonzero_exit_without_output_is_runner_crash_with_audit(self):
        (self.project / "fake_mode.txt").write_text("crash", encoding="utf-8")
        payload = self._runner()(self.project, self.task, self.output)
        self.assertEqual(payload["status"], "runner_crash")
        process = payload["paperfit_process"]
        self.assertEqual(process["returncode"], 3)
        self.assertIn("orchestrator exploded", process["stderr"])
        self.assertIn("orchestrator exploded", payload["failure"]["stderr_tail"])
        self.assertEqual(process["stdout"], "\ufffd\ufffd\ufffd non-utf8 stdout")

    def test_non_gbk_bytes_in_stdout_are_readable_str(self):
        (self.project / "fake_mode.txt").write_text("crash", encoding="utf-8")
        payload = self._runner()(self.project, self.task, self.output)
        process = payload["paperfit_process"]
        self.assertIsInstance(process["stdout"], str)
        self.assertIn("non-utf8 stdout", process["stdout"])




PAPER_MAIN = (
    "\\documentclass{article}\n"
    "\\usepackage{graphicx}\n"
    "\\begin{document}\n"
    "This paper studies layout repair on real documents with enough plain words.\n"
    "\\begin{figure}[t]\\includegraphics[width=0.9\\linewidth]{fig.pdf}\\end{figure}\n"
    "\\begin{table}[h]\\begin{tabular}{ll}\na & b \\\\\n\\end{tabular}\\end{table}\n"
    "Second paragraph with sufficient words for the injection point to exist.\n"
    "\\end{document}\n"
)

DISTURBED_MAIN = PAPER_MAIN.replace(
    "\\begin{document}\n",
    "\\begin{document}\n% DISTURBANCE:D1_long_token_overflow:BEGIN\n",
)


def _make_paperfit_data(
    root: Path,
    venues=("AAAI", "ICLR"),
    per_venue=1,
    *,
    disturbed: bool = False,
) -> Path:
    data_root = root / "paperfit-data"
    cases = []
    perturbation_rows = []
    index = 0
    for venue in venues:
        raw_dir = data_root / "raw" / venue
        raw_dir.mkdir(parents=True)
        for sub in range(per_venue):
            paper_id = f"arXiv-2601.0000{index}v1"
            src = data_root / "_src" / paper_id
            (src / "figs").mkdir(parents=True)
            (src / "figs" / "fig.pdf").write_bytes(b"%PDF tiny")
            (src / "paper.tex").write_text(DISTURBED_MAIN if disturbed else PAPER_MAIN, encoding="utf-8")
            archive = raw_dir / f"{paper_id}.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(src, arcname=paper_id)
            cases.append(
                {
                    "case_id": f"{venue}/{paper_id}",
                    "venue_id": venue,
                    "paper_id": paper_id,
                    "target_pages": 8,
                    "profile": "hard" if index % 2 else "normal",
                    "column_type": "double",
                    "seed": 1000 + index,
                    "asset_bytes": 1000 + index * 10,
                    "requested_perturbations": ["D1_long_token_overflow", "B2_shrink_graphics"],
                }
            )
            perturbation_rows.append(
                {
                    "case_id": f"{venue}/{paper_id}",
                    "perturbation_id": "D1_long_token_overflow",
                    "requested": True,
                    "applied": True,
                    "validation_status": "passed",
                }
            )
            perturbation_rows.append(
                {
                    "case_id": f"{venue}/{paper_id}",
                    "perturbation_id": "B2_shrink_graphics",
                    "requested": True,
                    "applied": False,
                    "validation_status": "standard",
                }
            )
            index += 1
    (data_root / "metadata").mkdir(parents=True)
    (data_root / "metadata" / "cases.jsonl").write_text(
        "\n".join(json.dumps(case) for case in cases) + "\n", encoding="utf-8"
    )
    (data_root / "metadata" / "perturbations.jsonl").write_text(
        "\n".join(json.dumps(row) for row in perturbation_rows) + "\n", encoding="utf-8"
    )
    return data_root


def _fake_compile(project: Path) -> dict:
    main = sorted(project.glob("*.tex"))
    stem = main[0].stem if main else "paper"
    (project / f"{stem}.pdf").write_bytes(b"%PDF fake")
    (project / f"{stem}.log").write_text("Output written on main.pdf (8 pages).\n", encoding="utf-8")
    return {"success": True}


def _fake_render(project: Path, pages_dir: Path) -> dict:
    pages_dir.mkdir(parents=True, exist_ok=True)
    for page in range(1, 3):
        Image.new("RGB", (40, 50), "white").save(pages_dir / f"page_{page:03d}.png")
    return {"success": True, "page_count": 2}


class DetectRawIsCleanTest(unittest.TestCase):
    def test_clean_and_disturbed_and_missing_archives(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            clean_data = _make_paperfit_data(root / "clean", venues=("AAAI",), disturbed=False)
            disturbed_data = _make_paperfit_data(root / "dirty", venues=("AAAI",), disturbed=True)
            clean_archive = clean_data / "raw" / "AAAI" / "arXiv-2601.00000v1.tar.gz"
            dirty_archive = disturbed_data / "raw" / "AAAI" / "arXiv-2601.00000v1.tar.gz"
            self.assertTrue(detect_raw_is_clean(clean_archive))
            self.assertFalse(detect_raw_is_clean(dirty_archive))
            self.assertFalse(detect_raw_is_clean(root / "nope.tar.gz"))  # fail-closed


class InjectFromRawMismatchTest(unittest.TestCase):
    """向 frozen_b0 数据传 --inject：明确拒绝，不产生半套结果。"""

    def test_frozen_b0_data_is_rejected_before_any_artifact(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            data_root = _make_paperfit_data(root, venues=("AAAI",), disturbed=True)
            out_root = root / "regression_runs"
            with self.assertRaises(SourceModeMismatchError) as ctx:
                run_mvp(
                    paperfit_data=data_root,
                    out_root=out_root,
                    case_count=1,
                    compile_fn=_fake_compile,
                    render_fn=_fake_render,
                    source_mode=SOURCE_MODE_INJECT_FROM_RAW,
                )
            self.assertIn("raw_is_clean", str(ctx.exception))
            # 明确拒绝 = 什么都没落盘（没有 run 目录、没有半套 pack/报告）
            self.assertFalse(out_root.exists() or any(out_root.glob("*")))

    def test_unknown_source_mode_is_value_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            data_root = _make_paperfit_data(root, venues=("AAAI",))
            with self.assertRaises(ValueError):
                run_mvp(
                    paperfit_data=data_root,
                    out_root=root / "runs",
                    case_count=1,
                    source_mode="inject_sometimes",
                )


class FrozenB0EndToEndTest(unittest.TestCase):
    """frozen_b0 默认模式：三列状态 + 扰动事实记账 + noop 不进可评估桶。"""

    def test_records_three_columns_and_noop_excluded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # AAAI（index 0）由 _SwitchingRunner 输出 noop 执行摘要；ICLR 正常 accepted
            data_root = _make_paperfit_data(root, venues=("AAAI", "ICLR"), per_venue=1, disturbed=True)
            runner = _SwitchingRunner().runner
            result = run_mvp(
                paperfit_data=data_root,
                out_root=root / "regression_runs",
                case_count=2,
                apply=True,
                paperfit_runner=runner,
                compile_fn=_fake_compile,
                render_fn=_fake_render,
            )
            run_dir = Path(result["run_dir"])
            records = {
                json.loads(line)["paper_id"]: json.loads(line)
                for line in (run_dir / "case_records.jsonl").read_text(encoding="utf-8").splitlines()
            }
            aaaa = records["AAAI"]
            iclr = records["ICLR"]

            # ICLR（修复 accepted）走旧口径
            self.assertEqual(iclr["candidate_status"], "layout_evaluable")
            self.assertEqual(iclr["run_status"], "completed")
            self.assertEqual(iclr["candidate_render_status"], "rendered")
            self.assertEqual(iclr["repair_effect_status"], "accepted")

            # AAAI（noop）三列如实拆分，不再冒充可评估
            self.assertEqual(aaaa["candidate_status"], "no_effect")
            self.assertEqual(aaaa["run_status"], "completed")
            self.assertEqual(aaaa["candidate_render_status"], "rendered")
            self.assertEqual(aaaa["repair_effect_status"], "no_effect")
            self.assertEqual(aaaa["environment_state"], "no_effect")

            # 旧字段仍存在（funnel/报告兼容）
            for record in (aaaa, iclr):
                for legacy in ("case_id", "paper_id", "perturbation_types", "injection_status", "b0_status", "candidate_status"):
                    self.assertIn(legacy, record)

            # 漏斗：noop 案例不许进 candidate_rendered
            report = json.loads((run_dir / "reports" / "regression_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["coverage"]["stages"]["b0_rendered"], 2)
            self.assertEqual(report["coverage"]["stages"]["candidate_rendered"], 1)
            self.assertIn("candidate:no_effect", report["coverage"]["failure_reasons"])

            # OURS 侧：accepted 导出、noop 不导出（源码等价 B0，导出只造假配对）
            ours_pages = sorted((run_dir / "methods" / "OURS").glob("*/*/pages"))
            self.assertEqual(len(ours_pages), 1)

            # frozen_b0 的扰动事实记账：metadata 的 applied/validated + 来源
            aaaa_case_dir = list((run_dir / "benchmark_pack" / "AAAI").glob("*"))[0]
            benchmark = json.loads((aaaa_case_dir / "benchmark_case.json").read_text(encoding="utf-8"))["benchmark_case"]
            self.assertEqual(benchmark["requested_perturbations"], ["D1_long_token_overflow", "B2_shrink_graphics"])
            self.assertEqual(benchmark["applied_perturbations"], ["D1_long_token_overflow"])
            self.assertEqual(benchmark["validated_perturbations"], ["D1_long_token_overflow"])
            self.assertEqual(benchmark["selected_perturbation_source"], "dataset_b0")
            self.assertEqual(aaaa["perturbation_types"], ["D1_long_token_overflow"])
            self.assertEqual(aaaa["perturbation_source"], "dataset_b0")

            manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["source_mode"], "frozen_b0")


class _SwitchingRunner:
    """AAAI 案例输出 noop 执行报告，其他案例正常 accepted。"""

    NOOP_SUMMARY = {"status": "noop", "applied_count": 0, "accepted_count": 0, "rejected_count": 1, "rolled_back_count": 0}

    def runner(self, project: Path, task_path: Path, output_path: Path) -> dict:
        noop = "AAAI" in project.parts
        pages = project / "data" / "pages"
        pages.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (40, 50), "white").save(pages / "page_001.png")
        payload = {
            "status": "continue" if noop else "done",
            "runtime_actions": {
                "initial_observe": {
                    "compile": {"success": True, "case_status": "layout_evaluable"},
                    "render": {"success": True},
                },
            },
            "failure": {"failure_type": "remaining_defects", "reason": "CONTINUE"} if noop else None,
        }
        if noop:
            payload["repair_execution_summary"] = dict(self.NOOP_SUMMARY)
            (project / "data" / "repair_execution_report.json").write_text(
                json.dumps({"status": "noop", "applied_count": 0}), encoding="utf-8"
            )
        else:
            payload["repair_execution_summary"] = {"status": "partial", "applied_count": 2, "accepted_count": 1}
            main = sorted(project.glob("*.tex"))[0]
            main.write_text(main.read_text(encoding="utf-8") + "% repaired\n", encoding="utf-8")
        output_path.write_text(json.dumps(payload), encoding="utf-8")
        return payload


class InjectFromRawCleanDataTest(unittest.TestCase):
    """干净 raw + inject_from_raw：允许注入并记账 mvp_injector 来源。"""

    def test_clean_raw_injects_and_records_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            data_root = _make_paperfit_data(root, venues=("AAAI",), disturbed=False)
            runner = _SwitchingRunner().runner
            result = run_mvp(
                paperfit_data=data_root,
                out_root=root / "regression_runs",
                case_count=1,
                paperfit_runner=runner,
                compile_fn=_fake_compile,
                render_fn=_fake_render,
                source_mode=SOURCE_MODE_INJECT_FROM_RAW,
            )
            run_dir = Path(result["run_dir"])
            self.assertEqual(result["pack_summary"]["success"], 1)
            case_dir = list((run_dir / "benchmark_pack" / "AAAI").glob("*"))[0]
            benchmark = json.loads((case_dir / "benchmark_case.json").read_text(encoding="utf-8"))["benchmark_case"]
            self.assertEqual(benchmark["selected_perturbation_source"], "mvp_injector")
            self.assertEqual(benchmark["requested_perturbations"], ["D1_long_token_overflow"])
            self.assertEqual(benchmark["applied_perturbations"], ["D1_long_token_overflow"])
            # 注入真的落进了 B0 源码
            b0_main = (case_dir / "b0_project" / "paper.tex").read_text(encoding="utf-8")
            self.assertIn("PERTURBED: D1_long_token_overflow", b0_main)
            record = json.loads((run_dir / "case_records.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(record["injection_status"], "success")
            self.assertEqual(record["perturbation_source"], "mvp_injector")


class RunIdCollisionTest(unittest.TestCase):
    def test_same_second_runs_get_distinct_run_ids(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            data_root = _make_paperfit_data(root, venues=("AAAI",), disturbed=True)
            runner = _SwitchingRunner().runner
            out_root = root / "regression_runs"
            first = run_mvp(
                paperfit_data=data_root, out_root=out_root, case_count=1,
                paperfit_runner=runner, compile_fn=_fake_compile, render_fn=_fake_render,
            )
            second = run_mvp(
                paperfit_data=data_root, out_root=out_root, case_count=1,
                paperfit_runner=runner, compile_fn=_fake_compile, render_fn=_fake_render,
            )
            self.assertNotEqual(first["run_dir"], second["run_dir"])

            import re as re_mod

            for run_dir in (first["run_dir"], second["run_dir"]):
                self.assertRegex(Path(run_dir).name, r"^MVP1_\d{8}_\d{6}_\d{6}_[0-9a-f]{8}$")
            self.assertTrue(Path(first["run_dir"]).is_dir())
            self.assertTrue(Path(second["run_dir"]).is_dir())


class FrozenPackReuseTest(unittest.TestCase):
    """固定时钟下同 run_id 二次调用：复用冻结 pack，不许踩 glob 生成器 len() 的坑。"""

    def test_existing_frozen_pack_is_reused_without_len_crash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            data_root = _make_paperfit_data(root, venues=("AAAI",), disturbed=True)
            runner = _SwitchingRunner().runner
            out_root = root / "regression_runs"

            class _FixedClock:
                @staticmethod
                def now():
                    return datetime(2026, 10, 6, 12, 0, 0)

            def _boom_compile(project: Path) -> dict:
                raise AssertionError("frozen pack must be reused, compile_fn must not run")

            with mock.patch.object(mvp_runner, "datetime", _FixedClock), mock.patch.object(
                mvp_runner.uuid, "uuid4", lambda: SimpleNamespace(hex="deadbeefdeadbeef")
            ):
                first = run_mvp(
                    paperfit_data=data_root, out_root=out_root, case_count=1,
                    paperfit_runner=runner, compile_fn=_fake_compile, render_fn=_fake_render,
                )
                # 旧代码在这里 len(generator) 直接 TypeError；复用分支不该重建 pack
                second = run_mvp(
                    paperfit_data=data_root, out_root=out_root, case_count=1,
                    paperfit_runner=runner, compile_fn=_boom_compile, render_fn=_fake_render,
                )
            self.assertEqual(first["run_dir"], second["run_dir"])
            self.assertEqual(first["pack_summary"], second["pack_summary"])


if __name__ == "__main__":
    unittest.main()
