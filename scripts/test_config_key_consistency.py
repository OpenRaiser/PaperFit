from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

import yaml  # noqa: E402


def _template_keys() -> set:
    data = yaml.safe_load((REPO_ROOT / "config" / "templates.yaml").read_text(encoding="utf-8"))
    return {str(key) for key in (data.get("templates") or data or {}).keys()}


class ConfigKeyConsistencyTest(unittest.TestCase):
    def test_benchmark_representatives_reference_real_templates(self) -> None:
        payload = json.loads(
            (REPO_ROOT / "config" / "benchmark_representatives.json").read_text(encoding="utf-8")
        )
        keys = _template_keys()
        referenced = [
            str(case.get("template"))
            for case in payload.get("representatives") or payload.get("cases") or []
            if case.get("template")
        ]
        self.assertTrue(referenced, "no benchmark representatives found")
        for template in referenced:
            self.assertIn(template, keys, f"benchmark template {template} missing from templates.yaml")

    def test_wizard_fallback_templates_exist_in_registry(self) -> None:
        import paperfit_wizard

        with mock.patch.object(paperfit_wizard, "load_templates_with_registry", return_value={}):
            templates = paperfit_wizard.load_templates()
        self.assertTrue(templates, "wizard fallback list is empty")
        keys = _template_keys()
        for entry in templates:
            self.assertIn(
                entry["value"],
                keys,
                f"wizard fallback template {entry['value']} missing from templates.yaml",
            )


if __name__ == "__main__":
    unittest.main()
