import unittest

import support

from custom_components.elga_ace import protocol


class ProtocolTests(unittest.TestCase):
    def test_crc(self):
        self.assertEqual(protocol.crc16_modbus(b"123456789"), 0x4B37)

    def test_ble_capture(self):
        challenge = bytes.fromhex("9c5e26e130290d4d")
        self.assertEqual(protocol.ble_unlock_request(challenge).hex(), "048ddea52790dca8a4")

    def test_can_capture(self):
        self.assertEqual(protocol.control_frame(protocol.identification_request(1)).hex(), "ff0102010002000323d6")
        challenge = bytes.fromhex("2e4818e1a1d3a8df")
        self.assertEqual(protocol.control_frame(protocol.unlock_request(challenge, 1, 4)).hex(), "ff01020100020203ff04a72cbba94f6cf51f2da6")
        response = bytes.fromhex("0201000201032e4818e1a1d3a8df")
        self.assertEqual(protocol.parse_challenge(response, 1), challenge)
        protocol.validate_unlock(bytes.fromhex("020100020303ff04ff01"), 1, 4)

    def test_appliance_capture(self):
        self.assertEqual(protocol.control_frame(protocol.identification_request(0)).hex(), "ff01020000020000e2ab")
        challenge = bytes.fromhex("2e4818e1a1d3946f")
        self.assertEqual(protocol.control_frame(protocol.unlock_request(challenge, 0, 5)).hex(), "ff01020000020200ff0597e5cbb1383b49b2ca0f")
        protocol.validate_unlock(bytes.fromhex("020000020300ff05ff01"), 0, 5)

    def test_unlock_rejects_bad_status_or_level(self):
        for value in ("020100020303ff04ff00", "020100020303ff05ff01", "020100020303ff04"):
            with self.subTest(value=value), self.assertRaises(protocol.ProtocolError):
                protocol.validate_unlock(bytes.fromhex(value), 1, 4)

    def test_bad_challenge(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.authorization_key(b"short", 4)
        with self.assertRaises(protocol.ProtocolError):
            protocol.parse_challenge(b"invalid", 0)

    def test_fragmented_capture(self):
        reassembler = protocol.Reassembler()
        packet = bytes.fromhex("ff010201000201032e4818e1a1d3a8df72d6")
        self.assertIsNone(reassembler.feed(b"\x00" + packet[1:9]))
        self.assertEqual(reassembler.feed(b"\xff" + packet[9:]), packet[1:-2])

    def test_crc_failure_resets_reassembly(self):
        reassembler = protocol.Reassembler()
        with self.assertRaises(protocol.ProtocolError):
            reassembler.feed(b"\xffbad-checksum")
        self.assertEqual(reassembler.feed(protocol.frame(b"valid")), b"valid")

    def test_fragment_sequence(self):
        reassembler = protocol.Reassembler()
        with self.assertRaises(protocol.ProtocolError):
            reassembler.feed(b"\x01out-of-order")
        self.assertEqual(reassembler.feed(protocol.frame(b"valid")), b"valid")

    def test_raw_gateway_error(self):
        error = protocol.parse_gateway_error(bytes.fromhex("020300000402000323d65900aa6900f401001100"))
        self.assertEqual(error.code.hex(), "03000004")
        self.assertFalse(error.is_register_error)
        self.assertTrue(protocol.parse_gateway_error(bytes.fromhex("0201000103")).is_register_error)
        self.assertIsNone(protocol.parse_gateway_error(bytes.fromhex("0200000000")))

    def test_read_request_and_full_correlation(self):
        self.assertEqual(protocol.read_request(1, 0x5031, 0).hex(), "ff0102000000000001503100afb7")
        header = protocol.read_response_header(1, 0x5404, 1)
        self.assertEqual(header.hex(), "0102000000010001540401")
