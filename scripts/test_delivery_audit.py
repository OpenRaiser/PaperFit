"""Delivery asset boundaries, with temporary roots and no server/model calls."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from delivery_audit import audit_delivery, check_html_links, sha256


SERVER = '''from pathlib import Path
UI_ROOT = Path(__file__).resolve().parent / "web"
class Handler:
    def _serve_static(self, request_path):
        rel = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        path = (UI_ROOT / rel).resolve()
        try:
            path.relative_to(UI_ROOT)
        except ValueError:
            return
raise AssertionError("This frozen server must never be executed by the audit")
'''


class DeliveryAuditTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Deliberately unrelated to historical usernames, PDF counts or layout.
        self.root = Path(self.temp.name).resolve() / "a new delivery"
        self.ui = self.root / "tools" / "preview" / "web"
        self.ui.mkdir(parents=True)
        self.server = self.ui.parent / "local_server.py"
        self.server.write_text(SERVER, encoding="utf-8")
        self.html = self.ui / "index.html"
        self.html.write_text('<link href="/styles.css?version=1"><script src="/app.js#module"></script>', encoding="utf-8")
        (self.ui / "styles.css").write_text("body{}", encoding="utf-8")
        (self.ui / "app.js").write_text("export {};", encoding="utf-8")
        self.service = {
            "html": self.html.relative_to(self.root).as_posix(),
            "server": self.server.relative_to(self.root).as_posix(),
            "ui_root": self.ui.relative_to(self.root).as_posix(),
            "server_sha256": sha256(self.server),
        }

    def test_verified_service_resolves_real_assets_and_hashes(self):
        broken, served = check_html_links(self.root, self.html, [self.service])
        self.assertEqual(broken, [])
        self.assertEqual(len(served), 2)
        self.assertEqual({item["url"] for item in served}, {"/styles.css?version=1", "/app.js#module"})
        self.assertTrue(all(item["sha256"] == sha256(self.root / item["asset"]) for item in served))

    def test_missing_and_escaped_served_assets_are_rejected(self):
        (self.ui / "styles.css").unlink()
        bad, served = check_html_links(self.root, self.html, [self.service])
        self.assertEqual([item["target"] for item in bad], ["/styles.css?version=1"])
        self.assertEqual(len(served), 1)
        self.html.write_text('<script src="/../local_server.py"></script>', encoding="utf-8")
        bad, served = check_html_links(self.root, self.html, [self.service])
        self.assertEqual(len(bad), 1)
        self.assertEqual(served, [])

    def test_file_viewer_root_relative_url_is_rejected_despite_existing_asset(self):
        page = self.root / "viewer.html"
        (self.root / "styles.css").write_text("body{}", encoding="utf-8")
        page.write_text('<link href="/styles.css">', encoding="utf-8")
        bad, served = check_html_links(self.root, page, [self.service])
        self.assertEqual(len(bad), 1)
        self.assertEqual(served, [])
        page.write_text('<link href="styles.css">', encoding="utf-8")
        self.assertEqual(check_html_links(self.root, page, [self.service]), ([], []))

    def test_arbitrary_root_manifest_and_service_are_read_only(self):
        manifest = {"files": [{"path": path.relative_to(self.root).as_posix(), "sha256": sha256(path)} for path in self.root.rglob("*") if path.is_file()]}
        before = {path.relative_to(self.root).as_posix(): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        report = audit_delivery(self.root, manifest=manifest, services=[self.service])
        self.assertEqual(report["status"], "pass", report)
        self.assertEqual(report["manifest_files_checked"], 4)
        self.assertEqual(report["html_files_checked"], 1)
        self.assertEqual(len(report["served_source_assets"]), 2)
        after = {path.relative_to(self.root).as_posix(): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    def test_server_sha_mismatch_cannot_authorize_service_urls(self):
        self.server.write_text(SERVER + "# changed\n", encoding="utf-8")
        report = audit_delivery(self.root, services=[self.service])
        self.assertEqual(report["status"], "failed")
        self.assertIn("SHA", report["errors"][0]["reason"])
        self.assertEqual(report["served_source_assets"], [])
        self.assertEqual(len(report["broken_links"]), 2)

    def test_matching_sha_does_not_override_static_route_or_containment(self):
        variants = [
            SERVER.replace('from pathlib import Path', 'from other_module import Path'),
            SERVER.replace('request_path.lstrip("/")', 'request_path'),
            SERVER.replace('path.relative_to(UI_ROOT)', 'pass'),
            SERVER.replace('except ValueError:\n            return', 'except ValueError:\n            pass'),
            SERVER.replace(' / "web"', ' / "different_web"'),
        ]
        for source in variants:
            with self.subTest(source=source):
                self.server.write_text(source, encoding="utf-8")
                service = {**self.service, "server_sha256": sha256(self.server)}
                report = audit_delivery(self.root, services=[service])
                self.assertEqual(report["status"], "failed", report)
                self.assertEqual(report["served_source_assets"], [])

    def test_declared_file_sha_mismatch_and_escape_are_rejected(self):
        asset = self.ui / "app.js"
        manifest = {"files": {asset.relative_to(self.root).as_posix(): sha256(asset), "../outside.txt": "0" * 64}}
        asset.write_text("changed", encoding="utf-8")
        report = audit_delivery(self.root, manifest=manifest, services=[self.service])
        self.assertEqual(report["status"], "failed")
        self.assertEqual(len([error for error in report["errors"] if error["kind"] == "manifest"]), 2)

    def test_file_url_decoding_query_and_escape(self):
        page = self.root / "viewer.html"
        (self.root / "page notes.pdf").write_bytes(b"%PDF-fixture")
        page.write_text('<a href="page%20notes.pdf?download=1#page=2">notes</a>', encoding="utf-8")
        self.assertEqual(check_html_links(self.root, page), ([], []))
        outside = self.root.parent / "outside.pdf"
        outside.write_bytes(b"%PDF-fixture")
        page.write_text('<a href="%2e%2e/outside.pdf">escaped</a>', encoding="utf-8")
        bad, served = check_html_links(self.root, page)
        self.assertEqual(len(bad), 1)
        self.assertEqual(served, [])

    def test_service_url_uses_frozen_servers_actual_non_decoding_mapping(self):
        self.html.write_text('<script src="/%61pp.js"></script>', encoding="utf-8")
        bad, served = check_html_links(self.root, self.html, [self.service])
        self.assertEqual(len(bad), 1)
        self.assertEqual(served, [])

    def test_base_url_cannot_silently_change_file_link_resolution(self):
        page = self.root / "viewer.html"
        (self.root / "styles.css").write_text("body{}", encoding="utf-8")
        page.write_text('<base href="https://example.com/"><link href="styles.css">', encoding="utf-8")
        bad, served = check_html_links(self.root, page)
        self.assertEqual(len(bad), 1)
        self.assertIn("base URL", bad[0]["reason"])
        self.assertEqual(served, [])

    def test_current_monitor_service_contract_matches_real_product_source(self):
        root = Path(__file__).resolve().parent.parent
        server = root / "scripts" / "monitor_server.py"
        html = root / "scripts" / "monitor_ui" / "index.html"
        service = {"html": "scripts/monitor_ui/index.html", "server": "scripts/monitor_server.py", "ui_root": "scripts/monitor_ui", "server_sha256": sha256(server)}
        bad, served = check_html_links(root, html, [service])
        self.assertEqual(bad, [])
        self.assertEqual({item["asset"] for item in served}, {"scripts/monitor_ui/styles.css", "scripts/monitor_ui/app.js"})

    def test_duplicate_or_external_service_registration_is_rejected(self):
        for services in ([self.service, self.service], [{**self.service, "server": "../external.py"}]):
            with self.subTest(services=services):
                report = audit_delivery(self.root, services=services)
                self.assertEqual(report["status"], "failed", report)
                self.assertTrue(report["errors"])

    def test_cli_accepts_custom_root_and_configuration_and_reports_exit_status(self):
        config = self.root.parent / "services.json"
        manifest = self.root.parent / "manifest.json"
        config.write_text(json.dumps({"services": [self.service]}), encoding="utf-8")
        manifest.write_text(json.dumps({"files": {self.service["server"]: self.service["server_sha256"]}}), encoding="utf-8")
        command = [sys.executable, "-B", str(Path(__file__).with_name("delivery_audit.py")), "--root", str(self.root), "--services", str(config), "--manifest", str(manifest)]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "pass")
        (self.ui / "app.js").unlink()
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["status"], "failed")


if __name__ == "__main__":
    unittest.main()
