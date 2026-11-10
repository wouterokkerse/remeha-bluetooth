import csv
import json
import unittest
from dataclasses import replace

import support

from custom_components.elga_ace.registers import INTEGER_LENGTHS, load_registers


class RegisterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registers = load_registers()
        cls.by_address = {(register.index, register.subindex): register for register in cls.registers}

    def test_entire_export_covered_once(self):
        with (support.ROOT / "elga_registers.csv").open(encoding="utf-8-sig", newline="") as source:
            addresses = {(int(row["index_decimal"]), int(row["subindex"])) for row in csv.DictReader(source, delimiter=";")}
        self.assertEqual(len(self.registers), 487)
        self.assertEqual(set(self.by_address), addresses)
        self.assertEqual(len({register.key for register in self.registers}), 487)
        self.assertEqual(len({register.name for register in self.registers}), 487)
        self.assertEqual(sum(register.binary for register in self.registers), 48)

    def test_all_scalars_match_transport_length(self):
        for register in self.registers:
            if register.datatype in INTEGER_LENGTHS:
                with self.subTest(register=register.key):
                    self.assertEqual(register.length, INTEGER_LENGTHS[register.datatype])

    def test_capture_values_and_new_register_scales(self):
        cases = (
            (0x5031, 0, "620c", 31.7), (0x4359, 0, "8c0a", 27.0),
            (0x501E, 0, "2a02", 5.54), (0x4350, 0, "0019", 64.0),
            (0x50A9, 0, "ca0d", 35.3), (0x434D, 0, "1c0c", 31.0),
            (0x434E, 0, "8a02", 6.5), (0x5404, 1, "d700", 21.5),
            (0x5013, 0, "620c", 31.7), (0x5302, 0, "ac0d", 35.0),
        )
        for index, subindex, raw, expected in cases:
            with self.subTest(index=hex(index)):
                self.assertEqual(self.by_address[index, subindex].decode(bytes.fromhex(raw)), expected)
        self.assertIn("discharge", self.by_address[0x4350, 0].name)
        self.assertIn("Control", self.by_address[0x5031, 0].name)

    def test_negative_and_missing_temperature(self):
        outside = self.by_address[0x501E, 0]
        self.assertEqual(outside.decode(bytes.fromhex("d6fd")), -5.54)
        self.assertIsNone(outside.decode(bytes.fromhex("0080")))
        self.assertIsNone(outside.decode(b""))

    def test_unsigned_counter_not_signed(self):
        counter = next(register for register in self.registers if register.datatype == "uint32" and register.factor == 1)
        self.assertEqual(counter.decode(bytes.fromhex("000000f0")), 0xF0000000)

    def test_incorrect_length_is_not_silently_decoded(self):
        with self.assertRaises(ValueError):
            self.by_address[0x501E, 0].decode(b"\x01")

    def test_hex_and_text_state_limits(self):
        structured = next(register for register in self.registers if register.datatype == "array")
        self.assertEqual(structured.decode(b"\x12\x34"), "1234")
        self.assertEqual(len(structured.decode(b"\x12" * 500)), 255)
        text = next(register for register in self.registers if register.datatype == "string")
        self.assertEqual(text.decode(b"EHC-07\x00\x00"), "EHC-07")

    def test_unknown_units_are_not_guessed(self):
        register = replace(self.by_address[0x5031, 0], unit_id="unknown")
        self.assertIsNone(register.unit)
        self.assertIsNone(register.device_class)
        self.assertTrue(register.attributes()["read_only"])

    def test_english_translations_match_and_mask_pin(self):
        strings = json.loads((support.PACKAGE / "strings.json").read_text())
        english = json.loads((support.PACKAGE / "translations/en.json").read_text())
        self.assertEqual(strings, english)
        self.assertIn("pin", strings["config"]["step"]["pair"]["data"])
        manifest = json.loads((support.PACKAGE / "manifest.json").read_text())
        self.assertEqual(manifest["domain"], "elga_ace")
        self.assertTrue(manifest["config_flow"])

    def test_hacs_metadata_and_proxy_package_use_standard_installation(self):
        metadata = json.loads((support.ROOT / "hacs.json").read_text())
        self.assertFalse(metadata["content_in_root"])
        self.assertFalse(metadata["zip_release"])
        package = (support.ROOT / "esphome/elga-pairing.yaml").read_text()
        self.assertIn("auto_connect: false", package)
        self.assertIn("ble_client.passkey_reply", package)
        self.assertIn("!secret elga_pin", package)
        self.assertNotIn("external_components", package)
        self.assertNotIn("api:", package)
