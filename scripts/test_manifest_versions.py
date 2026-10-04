from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATHS = [
    REPO_ROOT / "package.json",
    REPO_ROOT / ".claude-plugin" / "plugin.json",
    REPO_ROOT / ".claude-plugin" / "marketplace.json",
    REPO_ROOT / "plugins" / "paperfit" / ".codex-plugin" / "plugin.json",
]


def _collect_versions(payload) -> list:
    versions: list = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key == "version" and isinstance(value, str):
                versions.append(value)
            else:
                versions.extend(_collect_versions(value))
    elif isinstance(payload, list):
        for item in payload:
            versions.extend(_collect_versions(item))
    return versions


class ManifestVersionConsistencyTest(unittest.TestCase):
    def test_all_distribution_manifests_share_one_version(self) -> None:
        reference = json.loads(MANIFEST_PATHS[0].read_text(encoding="utf-8"))["version"]
        self.assertRegex(reference, r"^\d+\.\d+\.\d+$")
        for manifest_path in MANIFEST_PATHS:
            with self.subTest(manifest=manifest_path.name):
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                versions = _collect_versions(payload)
                self.assertTrue(versions, f"{manifest_path} has no version field")
                for version in versions:
                    self.assertEqual(version, reference)


if __name__ == "__main__":
    unittest.main()
