from __future__ import annotations
import hashlib
import json
import os
import sys
import subprocess
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import pymupdf as fitz
except ImportError:
    fitz = None
from gatekeeper_enforcer import make_decision
from runtime_artifacts import collect_artifact_manifest
from orchestrator_runtime import OrchestratorRuntime
from runtime_types import TaskSpec
from runtime_status import build_runtime_status
from host_context import build_host_context

@unittest.skipIf(fitz is None, "PDF evidence tests require PyMuPDF")
class RuntimePageObjectiveTest(unittest.TestCase):
    def fixture(self, count, *, reported=3, policy="exact", scope=None):
        temporary=tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        root=Path(temporary.name); data=root/"data"; (data/"pages").mkdir(parents=True)
        main=root/"main.tex"; main.write_text(r"\documentclass{article}\begin{document}Fixture.\end{document}")
        pdf=fitz.open()
        for n in range(count): pdf.new_page().insert_text((72,72), "Visible page %d" % (n+1))
        pdf.save(root/"main.pdf"); pdf.close()
        with fitz.open(root/"main.pdf") as doc:
            for n,page in enumerate(doc): page.get_pixmap().save(data/"pages"/("page_%03d.png"%(n+1)))
        visual=data/"visual_signal_report.json"
        visual.write_text(json.dumps({"summary":{"pages_analyzed":reported},"findings":[]}))
        (data/"defect_report.json").write_text('{"defects": []}')
        (data/"gatekeeper_decision.json").write_text('{"decision": "DONE"}')
        state={"main_tex":"main.tex", "compile_success":True,"page_images_rendered":True,
            "task":{"target_pages":3,"page_policy":policy,"page_budget_scope":scope},
            "artifacts":{"visual_signal_report":"data/visual_signal_report.json",
                         "defect_report":"data/defect_report.json",
                         "gatekeeper_decision":"data/gatekeeper_decision.json",
                         "page_images_dir":"data/pages"},
            "visual_signals_summary":{"updated_at":"2026-10-09"}}
        # Deterministic freshness ordering; no compiler or external service is mocked into a page count.
        files=[main,root/"main.pdf",*sorted((data/"pages").glob('*.png')),visual,
               data/"defect_report.json",data/"gatekeeper_decision.json"]
        for n,path in enumerate(files): os.utime(path,(1800000000+n,1800000000+n))
        return root,state

    def decision(self,root,state):
        previous=Path.cwd()
        try:
            os.chdir(root)
            return make_decision(state,{"defects":[]},False,None)
        finally: os.chdir(previous)

    def test_actual_pdf_supersedes_missing_or_wrong_visual_page_counts(self):
        for count,reported,expected in ((2,2,"CONTINUE"),(3,None,"DONE"),(4,3,"CONTINUE"),(4,None,"CONTINUE")):
            with self.subTest(count=count, reported=reported):
                root,state=self.fixture(count,reported=reported)
                result=self.decision(root,state)
                self.assertEqual(result["decision"],expected)
                objective=result["checks"]["page_objective"]
                self.assertEqual(objective["measured_pages"],count)
                self.assertEqual(objective["target_met"],count==3)
                self.assertEqual(objective["pdf_sha256"],hashlib.sha256((root/"main.pdf").read_bytes()).hexdigest())

    def test_at_most_allows_under_target_but_not_unknown_scope_or_stale_pdf(self):
        root,state=self.fixture(2,policy="at_most")
        self.assertEqual(self.decision(root,state)["decision"],"DONE")
        for policy in ("exact","at_most"):
            for scope in ("main_body","with_refs"):
                root,state=self.fixture(3,policy=policy,scope=scope)
                result=self.decision(root,state)
                self.assertEqual(result["decision"],"CONTINUE")
                self.assertIsNone(result["checks"]["page_objective"]["target_met"])
        root,state=self.fixture(3,policy="at_most")
        os.utime(root/"main.tex",(1900000000,1900000000))
        self.assertEqual(self.decision(root,state)["decision"],"CONTINUE")

    def prepared_runtime(self,root,state):
        runtime=OrchestratorRuntime(state_path=str(root/"data/state.json"))
        runtime.init_task("main.tex",task_type="visual_only",target_pages=3,
            page_policy=state["task"]["page_policy"],page_budget_scope=state["task"]["page_budget_scope"])
        runtime.manager.update({"compile_success":True,"page_images_rendered":True,
            "status":"DONE","last_gatekeeper_decision":"DONE","artifacts":state["artifacts"]})
        return runtime

    def test_direct_done_setter_and_report_ingestion_recheck_target(self):
        root,state=self.fixture(2)
        runtime=self.prepared_runtime(root,state)
        original=(root/"main.tex").read_bytes()
        previous=Path.cwd()
        try:
            os.chdir(root)
            result=runtime.apply_gatekeeper_decision("DONE")
            self.assertEqual(result["status"],"EVALUATING")
            result=runtime.manager.ingest_gatekeeper_decision("data/gatekeeper_decision.json")
            self.assertEqual(result["last_gatekeeper_decision"],"CONTINUE")
            self.assertEqual(result["status"],"EVALUATING")
        finally: os.chdir(previous)
        self.assertEqual((root/"main.tex").read_bytes(),original)

    def test_live_status_and_host_context_do_not_inherit_old_done(self):
        root,state=self.fixture(2)
        runtime=self.prepared_runtime(root,state)
        manifest=collect_artifact_manifest(root,"main.tex",state["artifacts"])
        self.assertEqual(manifest["freshness"]["status"],"pass")
        (root/"data/run_result.json").write_text(json.dumps({"status":"done","artifact_manifest":manifest}))
        result=build_runtime_status(project_root=root)
        self.assertEqual(result["status"],"EVALUATING")
        self.assertFalse(result["page_objective"]["target_met"])
        host=build_host_context(root/"main.tex",root=root)
        self.assertEqual(host["page_objective"]["page_policy"],"exact")
        self.assertFalse(host["page_objective"]["target_met"])
        (root/"main.tex").write_text((root/"main.tex").read_text()+" changed")
        os.utime(root/"main.tex",(1900000000,1900000000))
        host=build_host_context(root/"main.tex",root=root)
        self.assertIsNone(host["page_objective"]["target_met"])

    def test_unknown_or_other_main_task_does_not_supply_host_target(self):
        root,state=self.fixture(3)
        (root/"data/task.json").write_text(json.dumps({"task_type":"visual_only",
            "main_tex":"other.tex","target_pages":3,"page_policy":"exact"}))
        self.assertEqual(build_host_context(root/"main.tex",root=root)["page_objective"]["status"],"not_requested")
        host=build_host_context(root/"main.tex",root=root,target_pages=3,page_policy="exact")
        self.assertIsNone(host["page_objective"]["target_met"])

    def test_page_fit_cli_forwards_to_read_only_tool_without_runtime(self):
        if not shutil.which("node"):
            self.skipTest("Node is unavailable")
        root,state=self.fixture(2)
        main_before=(root/"main.tex").read_bytes()
        pdf_before=(root/"main.pdf").read_bytes()
        cli=Path(__file__).resolve().parent.parent/"bin/paperfit.js"
        output=root/"objective.json"
        environment={**os.environ,"PAPERFIT_PYTHON":sys.executable,"PYTHONDONTWRITEBYTECODE":"1"}
        result=subprocess.run(["node",str(cli),"page-fit","--main","main.tex",
            "--target-pages","3","--page-policy","exact","--page-budget-scope","with_appendix",
            "--pdf","main.pdf","--before-pdf","main.pdf","--attempt-tier","1","--defects-stable","--output",str(output)],
            cwd=root,env=environment,capture_output=True,text=True,encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
        self.assertEqual(result.returncode,0,result.stderr)
        payload=json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(payload["scope"],"host_page_fit_toolbox")
        self.assertEqual(payload["observed_pdf_objective"]["physical_pdf_pages"],2)
        self.assertIsNone(payload["page_objective"]["target_met"])
        self.assertEqual((root/"main.tex").read_bytes(),main_before)
        self.assertEqual((root/"main.pdf").read_bytes(),pdf_before)
        self.assertFalse((root/"data/state.json").exists())
        help_result=subprocess.run(["node",str(cli),"page-fit","--help"],env=environment,
            capture_output=True,text=True,encoding="utf-8",creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
        self.assertEqual(help_result.returncode,0,help_result.stderr)
        self.assertIn("--page-policy",help_result.stdout)
        self.assertIn("--data-dir",help_result.stdout)

    def test_public_run_task_rechecks_injected_done_without_rolling_back_source(self):
        root,state=self.fixture(2)
        before=(root/"main.tex").read_bytes()
        runtime=OrchestratorRuntime(state_path=str(root/"data/state.json"))
        def injected_done(**kwargs):
            runtime.manager.update({"compile_success":True,"page_images_rendered":True,
                "status":"DONE","last_gatekeeper_decision":"DONE","artifacts":state["artifacts"]})
            return runtime.manager.load()
        spec=TaskSpec(task_type="visual_only",main_tex="main.tex",project_root=str(root),
                      target_pages=3,page_policy="exact")
        with mock.patch.object(runtime,"_run_visual_only_observe_actions",return_value={}), \
             mock.patch.object(runtime,"_run_round_core",side_effect=injected_done):
            result=runtime.run_task(task_spec=spec,output_path="data/run_result.json")
        self.assertEqual(result["status"],"continue")
        self.assertEqual(result["gatekeeper_decision"],"CONTINUE")
        self.assertEqual((root/"main.tex").read_bytes(),before)
        self.assertEqual(runtime.manager.load()["terminal_success_guard"]["status"],"goal_pending")
        self.assertFalse((root/"data/rollback_report.json").exists())

if __name__ == "__main__": unittest.main()
