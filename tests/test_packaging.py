"""Repository and runtime layout checks for Marketplace/HACS installation."""

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
INTEGRATION = ROOT / "custom_components/elga_ace"


class PackagingTests(unittest.TestCase):
    def test_root_hacs_manifest_has_correct_types(self):
        metadata = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
        manifest = json.loads((INTEGRATION / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["name"], manifest["name"])
        self.assertIs(metadata["content_in_root"], False)
        self.assertIs(metadata["zip_release"], False)
        self.assertIs(metadata["render_readme"], True)
        self.assertEqual(metadata["homeassistant"], "2026.11.0")
        self.assertNotIn("filename", metadata)
        self.assertTrue((ROOT / "README.md").is_file())

    def test_one_integration_and_matching_domain(self):
        directories = [directory.name for directory in (ROOT / "custom_components").iterdir()
                       if directory.is_dir() and not directory.name.startswith((".", "__"))]
        self.assertEqual(directories, ["elga_ace"])
        manifest = json.loads((INTEGRATION / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["domain"], INTEGRATION.name)

    def test_hacs_required_integration_metadata(self):
        manifest = json.loads((INTEGRATION / "manifest.json").read_text(encoding="utf-8"))
        required = {"domain", "name", "version", "documentation", "issue_tracker", "codeowners"}
        self.assertTrue(required <= manifest.keys())
        self.assertRegex(manifest["version"], r"^\d+\.\d+\.\d+$")
        self.assertIn("@wouterokkerse", manifest["codeowners"])
        for field in ("documentation", "issue_tracker"):
            address = urlparse(manifest[field])
            self.assertEqual(address.scheme, "https")
            self.assertEqual(address.netloc, "github.com")
            self.assertTrue(address.path.startswith("/wouterokkerse/remeha-bluetooth"))

    def test_manifest_field_order(self):
        manifest = json.loads((INTEGRATION / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(list(manifest)[:2], ["domain", "name"])
        self.assertEqual(list(manifest)[2:], sorted(list(manifest)[2:]))

    def test_repository_install_keeps_all_runtime_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            installed = Path(temporary) / "custom_components/elga_ace"
            shutil.copytree(INTEGRATION, installed, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            for name in (
                "__init__.py", "manifest.json", "config_flow.py", "sensor.py",
                "binary_sensor.py", "client.py", "protocol.py", "pairing.py",
                "transport.py", "coordinator.py", "registers.py", "esphome_setup.py",
                "esphome/elga-pairing.yaml",
                "register_catalog.json", "strings.json", "translations/en.json",
            ):
                with self.subTest(file=name):
                    self.assertTrue((installed / name).is_file())
            catalog = json.loads((installed / "register_catalog.json").read_text(encoding="utf-8"))
            self.assertEqual(len(catalog["registers"]), 487)
            for path in installed.rglob("*.py"):
                source = path.read_text(encoding="utf-8")
                self.assertIsNone(re.search(r"^\s*(?:from|import)\s+(?:elga_read|tools|tests)\b", source, re.MULTILINE))

    def test_readme_explains_separate_proxy_install_and_old_releases(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("### Marketplace / HACS", readme)
        self.assertIn("does **not** install", readme)
        self.assertIn("already-published older release tag", readme)
