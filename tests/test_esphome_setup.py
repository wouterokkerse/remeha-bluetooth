import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import support  # noqa: F401

from custom_components.elga_ace.esphome_setup import (
    ProxyConfigError, configure_proxy, update_proxy_yaml, update_secrets,
)

MAC = "00:80:E1:26:5E:9C"
PROXY = """esphome:
  name: elga-proxy

packages:
  board: !include common/board.yaml

api:
  encryption:
    key: !secret api_encryption_key
"""


class SecretsTests(unittest.TestCase):
    def test_appends_quoted_pin_and_keeps_other_secrets(self):
        text = update_secrets('wifi_ssid: "home"\n# comment\n', "000123")
        self.assertEqual(text, 'wifi_ssid: "home"\n# comment\n\nelga_pin: "000123"\n')

    def test_replaces_existing_pin(self):
        text = update_secrets('elga_pin: 1\nwifi_ssid: x\n', "42")
        self.assertEqual(text, 'elga_pin: "42"\nwifi_ssid: x\n')


class ProxyYamlTests(unittest.TestCase):
    def test_adds_substitution_and_package_to_existing_mapping(self):
        text = update_proxy_yaml(PROXY, MAC)
        self.assertTrue(text.startswith(f'substitutions:\n  elga_address: "{MAC}"\n'))
        self.assertIn("packages:\n  elga_pairing: !include elga-pairing.yaml\n  board: !include common/board.yaml", text)
        self.assertIn("key: !secret api_encryption_key", text)

    def test_is_idempotent_and_updates_address(self):
        once = update_proxy_yaml(PROXY, MAC)
        self.assertEqual(update_proxy_yaml(once, MAC), once)
        changed = update_proxy_yaml(once, "11:22:33:44:55:66")
        self.assertIn('elga_address: "11:22:33:44:55:66"', changed)
        self.assertEqual(changed.count("elga_address"), 1)

    def test_list_style_packages_and_existing_substitutions(self):
        source = "substitutions:\n    name: elga-proxy\npackages:\n    - !include a.yaml\nesphome:\n    name: ${name}\n"
        text = update_proxy_yaml(source, MAC)
        self.assertIn(f'substitutions:\n    elga_address: "{MAC}"\n    name: elga-proxy', text)
        self.assertIn("packages:\n    - !include elga-pairing.yaml\n    - !include a.yaml", text)

    def test_no_packages_block_appends_one(self):
        text = update_proxy_yaml("esphome:\n  name: p\n", MAC)
        self.assertTrue(text.endswith("packages:\n  elga_pairing: !include elga-pairing.yaml\n"))

    def test_old_custom_package_needs_manual_cleanup(self):
        with self.assertRaises(ProxyConfigError) as error:
            update_proxy_yaml("packages:\n  x: !include elga-pairing-package.yaml\n", MAC)
        self.assertEqual(error.exception.reason, "old_package")


class ConfigureTests(unittest.TestCase):
    def test_writes_all_files_with_backups_then_reports_no_change(self):
        with TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "elga-proxy.yaml").write_text(PROXY, encoding="utf-8")
            (folder / "secrets.yaml").write_text('wifi_ssid: "x"\n', encoding="utf-8")
            result = configure_proxy(folder, "elga-proxy", MAC.lower(), "000123")
            self.assertEqual(result.proxy_file, "elga-proxy.yaml")
            self.assertEqual(set(result.changed_files), {"elga-pairing.yaml", "secrets.yaml", "elga-proxy.yaml"})
            self.assertIn("!secret elga_pin", (folder / "elga-pairing.yaml").read_text(encoding="utf-8"))
            self.assertIn(MAC, (folder / "elga-proxy.yaml").read_text(encoding="utf-8"))
            self.assertEqual((folder / "elga-proxy.yaml.elga-backup").read_text(encoding="utf-8"), PROXY)
            self.assertTrue((folder / "secrets.yaml.elga-backup").exists())
            again = configure_proxy(folder, "elga-proxy", MAC, "000123")
            self.assertFalse(again.changed)

    def test_finds_proxy_by_node_name(self):
        with TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "garage.yaml").write_text(PROXY, encoding="utf-8")
            (folder / "other.yaml").write_text("esphome:\n  name: other\n", encoding="utf-8")
            self.assertEqual(configure_proxy(folder, "elga-proxy", MAC, "1").proxy_file, "garage.yaml")

    def test_missing_folder_or_file_is_reported(self):
        with TemporaryDirectory() as temporary:
            folder = Path(temporary)
            for target, reason in ((folder / "missing", "esphome_dir_not_found"), (folder, "proxy_yaml_not_found")):
                with self.subTest(reason=reason), self.assertRaises(ProxyConfigError) as error:
                    configure_proxy(target, "elga-proxy", MAC, "1")
                self.assertEqual(error.exception.reason, reason)


if __name__ == "__main__":
    unittest.main()
